# telegram-signal-copier

Telegram signal copier for MetaTrader 5.

## Local Setup

1. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Create a local `.env` file:

   ```powershell
   python tools/setup_env.py
   ```

   The script asks for Telegram, Gemini and MT5 credentials and writes them to
   `.env`. That file is ignored by Git and must not be committed.

3. Run the bot:

   ```powershell
   python main.py
   ```

## Security Notes

- Never commit `.env`, `*.session`, API keys, phone numbers or MT5 credentials.
- `.env.example` is intentionally blank for sensitive values.
- `tools/parse_export.py` reads channel IDs from `.env` or from
  `--canal1-id` / `--canal2-id`; channel IDs are not hardcoded in source.

## Tests

```powershell
python -m pytest -q
```

