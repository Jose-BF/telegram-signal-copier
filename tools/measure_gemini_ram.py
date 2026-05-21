"""
measure_gemini_ram.py — Mide la huella de memoria del cliente google.genai
en uso prolongado para detectar posibles memory leaks.

Uso:
    python tools/measure_gemini_ram.py

Requiere: psutil
    pip install psutil   (si no esta ya instalado)

Comportamiento:
  1. Mide RSS del proceso antes de cualquier import de google.genai
  2. Importa google.genai + crea Client → mide RSS (delta = footprint del cliente)
  3. Hace 50 llamadas reales a gemini-2.5-flash con un prompt similar al que
     el bot usa en classifier.py (clasificacion de mensaje de gestion).
  4. Mide RSS cada 10 llamadas. Reporta delta total y por-llamada.
  5. Diagnostico final:
       <5MB de crecimiento total       → ESTABLE (sin leak detectable)
       5-20MB                          → CRECIMIENTO MODERADO (vigilar)
       >20MB                           → POSIBLE LEAK (investigar)

Coste de la prueba: 50 llamadas a Gemini Flash. A 30 calls/dia tipicos del
bot, esto consume ~1.5 dias de tu cuota. Free tier 1500 RPD = sobradisimo.

El script NO toca MT5 ni Telegram. Solo importa config para leer la API key.
"""

import gc
import os
import sys
from pathlib import Path

# Permitir importar config desde la raiz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import psutil
except ImportError:
    print("ERROR: psutil no instalado.")
    print("Instala con:  pip install psutil")
    sys.exit(1)


def get_rss_mb(proc: psutil.Process) -> float:
    """RSS = Resident Set Size en MB. Memoria fisica usada por el proceso."""
    return proc.memory_info().rss / 1024 / 1024


def main() -> int:
    proc = psutil.Process(os.getpid())

    print("=" * 60)
    print("  Medicion de huella de memoria: google.genai")
    print("=" * 60)

    # Baseline: solo Python + stdlib + psutil + sys.path setup
    rss_baseline = get_rss_mb(proc)
    print(f"\n[1/4] RSS baseline (sin importar genai): {rss_baseline:6.1f} MB")

    # Importar config (ligero, solo lee .env)
    import config
    rss_after_config = get_rss_mb(proc)
    print(f"[2/4] RSS tras 'import config':          {rss_after_config:6.1f} MB "
          f"(+{rss_after_config - rss_baseline:.1f})")

    if not getattr(config, "GOOGLE_API_KEY", None):
        print("\nERROR: config.GOOGLE_API_KEY no configurada en el .env")
        return 1

    # Importar google.genai y crear cliente (esto carga las deps pesadas:
    # httpx, pydantic, google-auth, anyio, websockets, tenacity, ...).
    from google import genai
    rss_after_import = get_rss_mb(proc)
    print(f"[3/4] RSS tras 'from google import genai': {rss_after_import:6.1f} MB "
          f"(+{rss_after_import - rss_after_config:.1f})")

    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    rss_after_client = get_rss_mb(proc)
    print(f"[4/4] RSS tras genai.Client():           {rss_after_client:6.1f} MB "
          f"(+{rss_after_client - rss_after_import:.1f})")

    # Prompt realista (similar al _PROMPT_TEMPLATE legacy del classifier).
    # Usamos un mensaje que el regex local NO captura para forzar llamada a Gemini
    # (en produccion seria fallback, aqui llamamos directo).
    prompt = (
        "You are a trading signal classifier for a gold (XAUUSD) Telegram channel. "
        "The message MAY contain MULTIPLE actions. Return a JSON ARRAY of every "
        "action present. Respond ONLY with valid JSON, no extra text or markdown.\n\n"
        "Message: \"Gold is reacting nicely from our zone, secure profits if you "
        "are satisfied with the move\"\n\n"
        "Available actions:\n"
        "- CLOSE_ALL: close all positions\n"
        "- CLOSE_FIRST: close first/oldest entries\n"
        "- CLOSE_AT_TP: close at specific TP (1..5)\n"
        "- MOVE_SL_TO_BE: move stop loss to breakeven\n"
        "- MOVE_SL_TO_PRICE: move stop loss to specific price\n"
        "- INFORMATIONAL: no action needed (status, commentary)\n\n"
        "JSON format: [{\"action\": \"X\", \"price\": null_or_number, "
        "\"confidence\": 0.0_to_1.0}]\n\n"
        "If purely informational, return [{\"action\": \"INFORMATIONAL\", "
        "\"price\": null, \"confidence\": 1.0}].\n"
        "If you cannot understand, return []."
    )

    n_total = 50
    batch_size = 10
    n_batches = n_total // batch_size

    print(f"\n{'=' * 60}")
    print(f"  Lanzando {n_total} llamadas a gemini-2.5-flash en {n_batches} "
          f"batches de {batch_size}")
    print(f"{'=' * 60}\n")

    rss_after_warmup = rss_after_client
    measurements: list[tuple[int, float]] = [(0, rss_after_warmup)]
    errors = 0

    for b in range(n_batches):
        for i in range(batch_size):
            n = b * batch_size + i + 1
            try:
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                _ = resp.text  # forzar deserializacion
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  Llamada #{n} ERROR: {type(e).__name__}: {e}")
                elif errors == 4:
                    print(f"  ... (silenciando errores siguientes, total al final)")

        # Forzar GC para que mediciones sean fair (no inflar por basura
        # pendiente que se va a recolectar igualmente).
        gc.collect()
        rss = get_rss_mb(proc)
        n_calls = (b + 1) * batch_size
        measurements.append((n_calls, rss))

        delta_total = rss - rss_after_warmup
        delta_per_call = delta_total / n_calls if n_calls else 0
        print(f"  Batch {b + 1}/{n_batches} ({n_calls:>3} calls): "
              f"RSS={rss:6.1f} MB  "
              f"(+{delta_total:+5.1f} total, {delta_per_call:+0.3f} MB/call)")

    print(f"\n{'=' * 60}")
    print(f"  Resumen")
    print(f"{'=' * 60}")
    print(f"\n  RSS baseline (solo Python):        {rss_baseline:6.1f} MB")
    print(f"  RSS con genai importado y Client:  {rss_after_client:6.1f} MB  "
          f"(+{rss_after_client - rss_baseline:.1f})")

    final_rss = measurements[-1][1]
    growth = final_rss - rss_after_warmup
    growth_per_call = growth / n_total if n_total else 0
    growth_per_1k = growth_per_call * 1000

    print(f"  RSS tras {n_total} llamadas:           {final_rss:6.1f} MB  "
          f"(+{growth:+.1f} desde tras-Client)")
    print(f"\n  Tasa de crecimiento: {growth_per_call:+.3f} MB/call  →  "
          f"{growth_per_1k:+.1f} MB / 1000 calls")

    if errors > 0:
        print(f"\n  Errores Gemini: {errors}/{n_total} ({errors / n_total * 100:.0f}%)")

    print(f"\n{'=' * 60}")
    print(f"  Diagnostico")
    print(f"{'=' * 60}")

    if growth < 5:
        print("\n  ✓ ESTABLE — crecimiento <5MB tras 50 llamadas.")
        print("    No hay leak detectable. Cliente Gemini global es seguro en")
        print("    produccion continua. OK pasar al Paso 1.")
        return 0
    elif growth < 20:
        print(f"\n  ⚠ CRECIMIENTO MODERADO — {growth:.1f}MB en 50 llamadas.")
        print(f"    Estimacion en produccion (30 calls/dia): "
              f"{growth_per_call * 30:.1f}MB/dia → "
              f"{growth_per_call * 30 * 30:.0f}MB/mes.")
        print("    No bloquea Paso 1, pero vigilar en produccion. Si crece a")
        print("    >500MB tras varias semanas, cerrar y recrear cliente periodicamente.")
        return 0
    else:
        print(f"\n  ✗ POSIBLE LEAK — {growth:.1f}MB en 50 llamadas.")
        print(f"    Estimacion en produccion (30 calls/dia): "
              f"{growth_per_call * 30:.1f}MB/dia → "
              f"{growth_per_call * 30 * 30:.0f}MB/mes.")
        print(f"    Saturaria 4GB de la VM en {3000 / growth_per_call / 30:.0f} dias.")
        print("\n    PARAR antes del Paso 1. Reportar este output al asistente.")
        print("    Posibles causas: httpx connection pool no se libera, response")
        print("    objects retenidos, deps caching responses.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
