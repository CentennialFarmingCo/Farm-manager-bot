# Centennial Farming Bot

Telegram bot + Streamlit dashboard for Centennial Farming Co. The bot logs
harvest bins and reports payroll/cost-per-ton; the dashboard renders an
interactive map of fields and varieties.

## Components

- `bot.py` — Telegram polling worker. Logs harvest entries to a local SQLite
  database and answers natural-language acreage questions.
- `dashboard.py` — Streamlit map for clients (peach/almond field boundaries).
- `fields_map.json` — Source of truth for field IDs, varieties, acres, polygons.
- `farm_data.db` — Created at runtime by the bot (gitignored).

## Local development

Requires Python 3.12 (see `runtime.txt`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit and add your Telegram token
```

Run the bot:

```bash
python bot.py
```

Run the dashboard:

```bash
streamlit run dashboard.py
```

Run the tests:

```bash
pytest
```

## Environment variables

| Variable               | Required | Default                                                    | Notes                                                                |
| ---------------------- | -------- | ---------------------------------------------------------- | -------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`   | yes      | —                                                          | From @BotFather on Telegram. Store as a Render secret, never in git. |
| `DASHBOARD_URL`        | no       | `https://centennial-farm-dashboard-qvytatulr.vercel.app`   | Public URL the `/dashboard` command links to (Vercel production).    |
| `FARM_DB_FILE`         | no       | `farm_data.db`                                             | Path to SQLite database                                              |
| `FARM_FIELDS_FILE`     | no       | `fields_map.json`                                          | Path to fields source data                                           |

If `DASHBOARD_URL` is missing a scheme it is coerced to `https://`. If the
value is unparseable, `/dashboard` returns a setup hint instead of an error.

**Never commit `.env`.** Only `.env.example` (placeholders only) belongs in git.

## Deployment (Render)

The repo includes a `render.yaml` blueprint and a `Procfile` describing the
intended layout: a `web` Streamlit dashboard service plus a `worker` Telegram
bot service, both on Python 3.12.8.

If Render is already configured via the dashboard, that configuration takes
precedence — review the blueprint before applying. On the worker service set
`TELEGRAM_BOT_TOKEN` as a Render **secret** (never commit it to git). Set
`DASHBOARD_URL` only if you want to override the default Vercel URL above
(for example, to point at a Vercel preview during testing).

The bot uses long-polling, so it does not need a public port. The Streamlit
dashboard service listens on `$PORT` provided by Render. The production map
that `/dashboard` links to is hosted on Vercel separately.

Note: SQLite on Render's free/starter disks is ephemeral. For durable harvest
history, attach a persistent disk to the worker or migrate to a managed
database.

## Telegram bot commands

- `/start` — overview and examples.
- `/dashboard` — sends a tap-to-open inline button (and plain link fallback) for the Vercel-hosted client map.
- `/payroll` — total bins, worker pay, total cost, cost per ton.

Free-text messages support:

- Acreage queries: `"how many acres of peaches in blocks 1, 2, 3"`
- Harvest logging: `"Field 5 18 bins"`
