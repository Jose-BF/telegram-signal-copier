"""Human-facing provider names for Telegram alerts."""

PROVIDER_DISPLAY_NAMES = {
    "canal1": "Dubai Investing",
    "canal2": "Gold Signals",
}


def provider_display_name(channel: str | None) -> str:
    """Return the public provider name without changing internal channel IDs."""
    key = str(channel or "").lower()
    return PROVIDER_DISPLAY_NAMES.get(key, str(channel or "Proveedor desconocido"))
