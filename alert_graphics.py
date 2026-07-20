"""Truthful PNG charts for human-review Telegram alerts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable

from provider_names import provider_display_name


class AlertGraphicUnavailable(RuntimeError):
    """The optional graphical alert could not be built safely."""


@dataclass(frozen=True)
class ChartModel:
    provider: str
    direction: str
    current: float | None
    entry: float | None
    target: float | None
    stop_levels: tuple[float, ...]
    stop_source: str
    prices: tuple[float, ...]
    floating_pnl: float
    n_open: int
    n_initial: int
    elapsed_min: float | None


def _finite_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _unique_levels(values: Iterable) -> tuple[float, ...]:
    levels = []
    for value in values:
        parsed = _finite_float(value)
        if parsed is None or any(abs(parsed - seen) < 0.01 for seen in levels):
            continue
        levels.append(parsed)
    return tuple(sorted(levels))


def _next_target(direction: str, current: float | None,
                 targets: Iterable) -> float | None:
    levels = _unique_levels(targets)
    if not levels:
        return None
    direction = str(direction or "").upper()
    if current is None:
        return max(levels) if direction == "BUY" else min(levels)
    if direction == "BUY":
        ahead = [level for level in levels if level > current + 0.01]
        return min(ahead) if ahead else max(levels)
    ahead = [level for level in levels if level < current - 0.01]
    return max(ahead) if ahead else min(levels)


def _downsample(values: list[float], max_points: int) -> tuple[float, ...]:
    if max_points <= 0 or not values:
        return ()
    if len(values) <= max_points:
        return tuple(values)
    if max_points == 1:
        return (values[-1],)
    last = len(values) - 1
    indexes = [round(idx * last / (max_points - 1))
               for idx in range(max_points)]
    return tuple(values[idx] for idx in indexes)


def collect_recent_prices(
    symbol: str,
    direction: str,
    *,
    mt5_module=None,
    window_minutes: float = 15.0,
    max_points: int = 120,
    now: datetime | None = None,
) -> tuple[float, ...]:
    """Read a bounded real-tick trajectory; return empty on any uncertainty."""
    try:
        if mt5_module is None:
            import MetaTrader5 as mt5_module
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        since = now - timedelta(minutes=max(1.0, float(window_minutes)))
        ticks = mt5_module.copy_ticks_range(
            symbol, since, now, mt5_module.COPY_TICKS_INFO,
        )
        if ticks is None:
            return ()
        field = "bid" if str(direction or "").upper() == "BUY" else "ask"
        values = []
        for tick in ticks:
            try:
                raw = tick[field]
            except (KeyError, TypeError, ValueError):
                raw = getattr(tick, field, None)
            value = _finite_float(raw)
            if value is not None:
                values.append(value)
        return _downsample(values, max_points)
    except Exception:
        return ()


def build_chart_model(ctx, prices: Iterable[float]) -> ChartModel:
    current = _finite_float(getattr(ctx, "current_price", None))
    entry = _finite_float(getattr(ctx, "entry_price", None))
    effective_sls = _unique_levels(getattr(ctx, "effective_sls", []) or [])
    if effective_sls:
        stop_levels = effective_sls
        stop_source = "actual"
    else:
        stop_levels = _unique_levels([getattr(ctx, "sl", None)])
        stop_source = "proveedor"
    verified_prices = tuple(
        value for value in (_finite_float(item) for item in prices)
        if value is not None
    )
    elapsed = getattr(ctx, "elapsed_min", None)
    try:
        elapsed = float(elapsed)
    except (TypeError, ValueError):
        elapsed = None
    return ChartModel(
        provider=provider_display_name(getattr(ctx, "channel", None)),
        direction=str(getattr(ctx, "direction", "?") or "?").upper(),
        current=current,
        entry=entry,
        target=_next_target(
            getattr(ctx, "direction", ""), current,
            getattr(ctx, "effective_tps", None) or getattr(ctx, "tps", []) or [],
        ),
        stop_levels=stop_levels,
        stop_source=stop_source,
        prices=verified_prices,
        floating_pnl=float(getattr(ctx, "floating_pnl_total", 0.0) or 0.0),
        n_open=int(getattr(ctx, "n_open", 0) or 0),
        n_initial=int(getattr(ctx, "n_initial", 0) or 0),
        elapsed_min=elapsed,
    )


def chart_level_specs(model: ChartModel) -> tuple[tuple[str, float, str], ...]:
    """Return non-overlapping, semantically accurate horizontal levels."""
    specs = []
    if model.target is not None:
        specs.append(("TP", model.target, "target"))
    stops_at_entry = (
        model.entry is not None
        and [stop for stop in model.stop_levels
             if abs(stop - model.entry) < 0.01]
    )
    if model.entry is not None:
        label = "ENTRADA / BE"
        if stops_at_entry:
            label += (" / SL ACTUAL" if model.stop_source == "actual"
                      else " / SL PROVEEDOR")
        specs.append((label, model.entry, "entry"))
    remaining_stops = [
        stop for stop in model.stop_levels
        if model.entry is None or abs(stop - model.entry) >= 0.01
    ]
    for index, stop in enumerate(remaining_stops[:3]):
        suffix = "" if len(remaining_stops) == 1 else f" {index + 1}"
        label = ("SL ACTUAL" if model.stop_source == "actual"
                 else "SL PROVEEDOR") + suffix
        specs.append((label, stop, "stop"))
    return tuple(specs)


def _font(size: int, bold: bool = False):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise AlertGraphicUnavailable("Pillow no instalado") from exc
    names = (
        ["seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold else
        ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    )
    roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                try:
                    return ImageFont.truetype(str(candidate), size=size)
                except OSError:
                    pass
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _price_text(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def render_review_alert_png(model: ChartModel) -> bytes:
    """Render the option-A chart. A line is drawn only from real ticks."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise AlertGraphicUnavailable("Pillow no instalado") from exc

    width, height = 1080, 620
    background = "#101419"
    foreground = "#f4f7fb"
    muted = "#aeb7c2"
    grid = "#34404d"
    green = "#42d392"
    red = "#ff6b6b"
    cyan = "#56b4e9"
    gold = "#f2c94c"
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    title_font = _font(34, bold=True)
    label_font = _font(23, bold=True)
    body_font = _font(21)
    small_font = _font(18)

    draw.text((62, 30), f"{model.provider.upper()}  ·  {model.direction}",
              fill=foreground, font=title_font)
    elapsed = (f"HACE {model.elapsed_min:.0f} MIN"
               if model.elapsed_min is not None else "REVISIÓN HUMANA")
    elapsed_box = draw.textbbox((0, 0), elapsed, font=small_font)
    draw.text((width - 62 - (elapsed_box[2] - elapsed_box[0]), 40), elapsed,
              fill=muted, font=small_font)
    draw.text((62, 82), "REVISIÓN HUMANA · DATOS REALES",
              fill=gold, font=small_font)
    if len(model.prices) < 2:
        no_ticks = "SIN TRAYECTORIA RECIENTE"
        box = draw.textbbox((0, 0), no_ticks, font=small_font)
        draw.text((width - 62 - (box[2] - box[0]), 82), no_ticks,
                  fill=muted, font=small_font)

    left, top, right, bottom = 62, 135, width - 62, height - 92
    all_values = list(model.prices)
    all_values.extend(value for value in (
        model.current, model.entry, model.target) if value is not None)
    all_values.extend(model.stop_levels)
    if not all_values:
        raise AlertGraphicUnavailable("sin precios verificados para dibujar")
    low, high = min(all_values), max(all_values)
    span = max(high - low, 2.0)
    low -= span * 0.08
    high += span * 0.08

    def y_for(value: float) -> int:
        return round(bottom - ((value - low) / (high - low)) * (bottom - top))

    for index in range(5):
        y = round(top + index * (bottom - top) / 4)
        draw.line((left, y, right, y), fill=grid, width=1)

    def level(value: float | None, label: str, color: str, offset: int = 0):
        if value is None:
            return
        y = y_for(value)
        draw.line((left, y, right, y), fill=color, width=3)
        draw.text((left + 18, y - 31 + offset),
                  f"{label}  {_price_text(value)}", fill=color,
                  font=label_font)

    colors = {"target": gold, "entry": cyan, "stop": red}
    for label_text, value, kind in chart_level_specs(model):
        level(value, label_text, colors[kind])

    if len(model.prices) >= 2:
        points = []
        last = len(model.prices) - 1
        for index, price in enumerate(model.prices):
            x = round(left + index * (right - left) / last)
            points.append((x, y_for(price)))
        draw.line(points, fill=green, width=5, joint="curve")
    if model.current is not None:
        cy = y_for(model.current)
        draw.ellipse((right - 10, cy - 10, right + 10, cy + 10),
                     fill=foreground, outline=green, width=5)
        current_text = f"AHORA  {_price_text(model.current)}"
        box = draw.textbbox((0, 0), current_text, font=label_font)
        draw.text((right - (box[2] - box[0]) - 22, max(top, cy - 46)),
                  current_text, fill=foreground, font=label_font)

    pnl_color = green if model.floating_pnl >= 0 else red
    pnl = f"{model.floating_pnl:+.2f} $ FLOTANTE".replace(".", ",")
    draw.text((62, height - 58), pnl, fill=pnl_color, font=label_font)
    positions = f"{model.n_open} DE {model.n_initial} POSICIONES ABIERTAS"
    box = draw.textbbox((0, 0), positions, font=body_font)
    draw.text((width - 62 - (box[2] - box[0]), height - 56), positions,
              fill=foreground, font=body_font)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def build_live_review_image(signal, ctx, symbol: str,
                            window_minutes: float = 15.0) -> bytes:
    prices = collect_recent_prices(
        symbol, getattr(ctx, "direction", ""),
        window_minutes=window_minutes,
    )
    return render_review_alert_png(build_chart_model(ctx, prices))
