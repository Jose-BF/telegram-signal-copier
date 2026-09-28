# Pregunta para el soporte de la empresa de fondeo (preparada el 27/09/2026)

**Respuesta (28/09/2026, según Jose): FTMO confirma que se puede trabajar así, cogiendo las señales
indicadas con gestión propia.** Pendiente: guardar aquí el texto o una captura de la respuesta escrita,
como evidencia.

Enviarla por escrito (chat o email) **antes de pagar ningún reto** y guardar la respuesta en esta
carpeta. Casi todas las empresas prohíben copiar operaciones de la cuenta de otra persona; nuestro
caso es distinto, pero hay que tenerlo confirmado por escrito.

## Versión en inglés (para enviar)

> Hello,
>
> Before purchasing a 2-step challenge, I would like written confirmation that my trading
> method is allowed on the challenge, the verification and the funded account.
>
> I run my own Expert Advisor on MT5, on XAUUSD only. It reads trade alerts ("buy gold now" /
> "sell gold now") published in public Telegram channels and uses them only as a timing
> trigger. Every decision after that is made by my own EA with my own rules:
> - whether to take the alert at all (my own market filters; most alerts are skipped);
> - entry method and price (e.g. waiting for a pullback);
> - position size, number of entries, take profit, stop loss, break-even and time exits.
>
> So my trades do not replicate the channel's trades or anyone else's account: entry prices,
> sizes and exits differ. There is no trade copier connected to any third-party account, and
> no one else has access to my account.
>
> 1. Is this method allowed in the challenge, the verification and the funded account?
> 2. Is holding several positions on XAUUSD at the same time (a basket of up to 5 entries in
>    the same direction, each with its own stop loss) allowed?
> 3. Are there any restrictions on trading around high-impact news for this account type?
>
> Thank you.

## Versión en español (para entenderla)

> Hola:
>
> Antes de comprar un reto de 2 fases, quiero confirmación por escrito de que mi forma de operar
> está permitida en el reto, en la verificación y en la cuenta fondeada.
>
> Uso mi propio EA en MT5, solo en XAUUSD. Lee avisos de operación ("compra oro ya" / "vende oro
> ya") publicados en canales públicos de Telegram y los usa solo como disparador de momento. Todo lo
> demás lo decide mi EA con mis propias reglas:
> - si coger el aviso o no (filtros propios de mercado; la mayoría se descartan);
> - cómo y a qué precio entrar (por ejemplo, esperando un retroceso);
> - tamaño, número de entradas, objetivo, stop, break-even y salidas por tiempo.
>
> Mis operaciones no replican las del canal ni las de ninguna otra cuenta: los precios de entrada,
> los tamaños y las salidas son distintos. No hay ningún copiador conectado a una cuenta de otra
> persona, y nadie más tiene acceso a mi cuenta.
>
> 1. ¿Está permitido en el reto, la verificación y la cuenta fondeada?
> 2. ¿Se permite tener varias posiciones de XAUUSD a la vez (una cesta de hasta 5 entradas en la
>    misma dirección, cada una con su stop)?
> 3. ¿Hay alguna restricción de operar alrededor de noticias fuertes en este tipo de cuenta?
>
> Gracias.

## Reglas consultadas (webs oficiales, 27/09)
Detalle y fuentes en `research/prop_rules.py`.

- **FTMO 2 fases:**
  - objetivos del 10 % y luego del 5 %;
  - pérdida diaria del 5 % contando el flotante, medida desde el saldo a las 00:00 CE(S)T;
  - pérdida total del 10 %, fija;
  - 4 días de trading por fase y sin límite de días;
  - regla del mejor día: ningún día puede superar el 50 % del beneficio de los días positivos
    (no te suspende, pero obliga a seguir operando);
  - noticias: libre en el reto; en la cuenta fondeada estándar, nada de abrir ni cerrar
    operaciones de 2 min antes a 2 min después (la cuenta "Swing" no tiene esa restricción).
- **The5ers High Stakes:**
  - objetivos del 10 % y luego del 5 %;
  - pérdida diaria del 5 % desde el mínimo entre saldo y equity de medianoche;
  - pérdida total del 10 %;
  - 3 días con beneficio de al menos el 0,5 %;
  - sin límite de tiempo;
  - nada de operar de 2 min antes a 2 min después de noticias fuertes.
- **FundedNext Stellar 2 fases:**
  - objetivos del 8 % y luego del 5 %;
  - pérdida diaria del 5 % y total del 10 %, fija;
  - 5 días por fase y sin límite de tiempo;
  - noticias permitidas;
  - los EA van "con complementos" de pago.
