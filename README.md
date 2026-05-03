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

### Make harvest logs survive redeploys (persistent disk)

By default, Render's filesystem is **ephemeral** — every redeploy or restart
wipes `farm_data.db`, so all logged bins disappear. To keep harvest history,
attach a Render **persistent disk** to the bot worker.

The `render.yaml` blueprint in this repo already declares the disk. If your
bot service was created from this blueprint, redeploying the latest commit
attaches the disk automatically. If the service was created manually in the
Render dashboard, follow these click-by-click steps once:

1. Open <https://dashboard.render.com> and sign in.
2. Click the **centennial-bot** worker service (the Telegram bot).
3. In the left sidebar of the service, click **Disks**.
4. Click **Add Disk**. Fill in:
   - **Name:** `farm-data`
   - **Mount Path:** `/var/data`
   - **Size (GB):** `1` (you can grow it later, but you cannot shrink it)
5. Click **Save**. Render will offer to redeploy — accept.
6. In the same service, click **Environment** in the sidebar.
7. Under **Environment Variables**, click **Add Environment Variable**:
   - **Key:** `FARM_DB_FILE`
   - **Value:** `/var/data/farm_data.db`
8. Confirm `TELEGRAM_BOT_TOKEN` is still set as a secret here (do **not**
   put it in `render.yaml` or commit it to git).
9. Click **Save Changes**. Render redeploys.
10. After the deploy finishes, click **Logs** in the sidebar. You should
    see `🚀 Centennial Farming Bot ... is running!`. Send `/payroll` to
    the bot in Telegram, log a test bin (e.g. `Block 4 1 bin`), then in
    the Render dashboard click **Manual Deploy → Deploy latest commit**.
    When the bot comes back, `/payroll` should still show that bin —
    that confirms the disk is working.

**Cost note:** persistent disks on Render require a paid plan (Starter and
above). Disks are billed per GB per month on top of the service plan. See
Render's pricing page for the current rate. The `1 GB` size in
`render.yaml` is the minimum and is plenty for SQLite harvest logs.

If you ever need to disable the disk, remove the `disk:` block from
`render.yaml` (or detach the disk from the dashboard) and unset
`FARM_DB_FILE` so the bot falls back to the local `farm_data.db` path.
Detaching the disk **deletes** all data on it.

## Telegram bot commands

- `/start` — overview and examples.
- `/dashboard` — sends a tap-to-open inline button (and plain link fallback) for the Vercel-hosted client map.
- `/payroll` — total bins, worker pay, total cost, cost per ton.
- `/irrigation` (alias `/water`) — log or check irrigation. See below.
- `/today` — daily farm summary (harvest bins, irrigation hours, open pump
  sessions, and labor cost). See [Daily summary](#daily-summary).

Free-text messages support:

- Acreage queries: `"how many acres of peaches in blocks 1, 2, 3"`
- Harvest logging: `"Field 5 18 bins"`

## Irrigation tracking

Log how long each block is irrigated so you can see water/pump efficiency
across the season. Three input styles:

| Style                   | Example                            | What it does                                          |
| ----------------------- | ---------------------------------- | ----------------------------------------------------- |
| One-shot **duration**   | `/irrigation Block 4 12 hours`     | Records 12 hours of irrigation on Block 4 today.      |
| Pump **start**          | `/irrigation Block 5B started`     | Marks Block 5B as actively irrigating.                |
| Pump **stop**           | `/irrigation Block 5B stopped`     | Closes the open session and computes hours from start.|

Reports:

- `/irrigation status` — blocks currently irrigating, with elapsed hours.
- `/irrigation today` — total hours by block for today.
- `/irrigation summary` — total hours by block over the last 7 days.

Beginner notes:

- The block label must match the human label on the field map (e.g. `Block 4`,
  `Block 36A`, `Block 5B`, `Block 56/58`). Internal IDs are not exposed.
- One block per message. `/irrigation Block 4 and Block 5B 6 hours` is rejected
  as ambiguous — re-send each block separately.
- Hours can be a decimal: `/water Block 4 1.5h` works.
- Irrigation events are stored in the **same SQLite database** as harvest data
  (`farm_data.db` locally, or the path you set in `FARM_DB_FILE` on Render).
  Existing harvest data is never touched. The `irrigation_events` table is
  created on first use and migrated forward safely on every start.

## Daily summary

`/today` reads the same SQLite database and returns a single digest of
today's farm activity. It does not write data, does not call any external
service, and does not schedule anything — it is a pull-only command.

The digest includes, when data is available for today:

- **Harvest** — bins per block (with variety) and a daily total.
- **Irrigation** — completed hours per block plus any *currently running*
  pump sessions (started but not yet stopped).
- **Labor cost** — bins × $30 worker pay × 1.35 commission, mirroring
  `/payroll` but scoped to today only.

Example:

```
/today
```

```
📋 Daily farm summary — 2026-05-03

🍑 Harvest today:
• Block 4: 18 bins — Parade Freestone Peach
Total: 18 bins

💧 Irrigation today:
• Block 36A: 4.0h
Total: 4.0h

🟢 Currently irrigating:
• Block 5B (since 2026-05-03T08:12:00-07:00, 1.5h elapsed)

💰 Labor today:
• Bins: 18
• Worker pay: $540
• Total cost (35% commission): $729.0

Tap /dashboard for the field map.
```

If nothing has been logged yet today, `/today` returns a friendly empty
state with example commands for harvest and irrigation logging.
