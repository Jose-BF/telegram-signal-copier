from provider_names import provider_display_name


def test_provider_display_names_match_real_telegram_channels():
    assert provider_display_name("canal1") == "Dubai Investing"
    assert provider_display_name("canal2") == "Gold Signals"


def test_provider_display_name_preserves_unknown_channel_name():
    assert provider_display_name("mesa_privada") == "mesa_privada"
    assert provider_display_name(None) == "Proveedor desconocido"
