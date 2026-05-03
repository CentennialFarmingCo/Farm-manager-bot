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
| `FARM_LAT`             | no       | `37.30`                                                    | Latitude used by `/weather`. Default is Merced County, CA.           |
| `FARM_LON`             | no       | `-120.48`                                                  | Longitude used by `/weather`.                                        |
| `FARM_LOCATION_NAME`   | no       | `Merced County, CA`                                        | Label printed on the `/weather` summary.                             |
| `WIND_ALERT_MPH`       | no       | `10`                                                       | Spray-caution wind threshold (mph).                                  |
| `HEAT_ALERT_F`         | no       | `95`                                                       | Heat-caution high-temp threshold (°F).                               |
| `FROST_ALERT_F`        | no       | `34`                                                       | Frost-caution low-temp threshold (°F).                               |
| `RAIN_PROB_ALERT_PCT`  | no       | `50`                                                       | Rain-caution probability threshold (%).                              |
| `RAIN_AMOUNT_ALERT_IN` | no       | `0.10`                                                     | Rain-caution accumulation threshold (inches).                        |
| `WEATHER_API_TIMEOUT`  | no       | `8`                                                        | HTTP timeout for the Open-Meteo call (seconds).                      |

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
- `/spray` — log spray/pesticide applications and view active REI/PHI
  windows. See [Spray log](#spray-log).
- `/today` — daily farm summary (harvest bins, irrigation hours, open pump
  sessions, and labor cost). See [Daily summary](#daily-summary).
- `/task` / `/tasks` — log farm/repair tasks with optional block, priority,
  and notes; list open tasks, close them by id, or pull a summary. See
  [Task / repair tracking](#task--repair-tracking).
- `/weather` (alias `/alerts`) — on-demand forecast plus operational
  alerts (spray wind, heat, frost, rain, irrigation hint). See
  [Weather alerts](#weather-alerts).

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

## Spray log

`/spray` records pesticide / foliar / nutrient applications and (optionally)
computes Re-Entry Interval (REI) and Pre-Harvest Interval (PHI) windows so
you can see what's still under restriction.

> **Important — read before using.** This is a *recordkeeping aid*. The bot
> does **not** look up product label restrictions. REI and PHI values must
> come from you (the product label / SDS / your PCA). When you omit them,
> no restriction window is computed and the bot will say so. **Always
> follow the pesticide label, the SDS, and local/state regulations** — the
> label is the law. This bot is not legal, regulatory, or agronomic advice.

### Logging applications

| Example                                                               | What it does                                                                                       |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `/spray Block 5B copper 80 gal rei 12h phi 0d`                        | Logs copper at 80 gal on Block 5B with a 12-hour REI and 0-day PHI.                                |
| `/spray Block 36A sulfur rei 24h phi 1d notes mildew pressure`        | Logs sulfur on Block 36A; "mildew pressure" is captured in notes.                                  |
| `/spray Block 4 nutrient foliar`                                      | Logs the application without REI/PHI; bot replies it cannot compute a restriction window.         |

REI accepts `rei 12h`, `rei 12 hours`, `rei 1d` (= 24h), or a bare number
(`rei 12` is treated as hours). PHI accepts `phi 0d`, `phi 7 days`, `phi 48h`
(= 2 days), or a bare number (treated as days).

### Reports

- `/spray today` — applications logged today, with REI/PHI status next to each.
- `/spray open` (or `/spray restrictions`) — every spray whose REI or PHI is
  still active, with the end timestamp and time remaining.
- `/spray summary` — last 7 days of applications.

### Notes & limitations

- One block per message. `Block 4 and Block 5B copper` is rejected as
  ambiguous — re-send each block separately.
- Block-label semantics match the rest of the bot (`Block 4`, `Block 36A`,
  `Block 5B`, `Block 56/58`).
- Spray events are stored in the **same SQLite database** as harvest and
  irrigation (`farm_data.db` locally, or `FARM_DB_FILE` on Render). The
  `spray_events` table is created on first use; existing data is never
  touched.
- No reminders, alarms, or scheduled notifications are sent. Restriction
  status is shown only when you explicitly ask via `/spray today` or
  `/spray open`.

## Task / repair tracking

`/task` records field operations and repair items so nothing falls through the
cracks. Tasks may be tied to a block (e.g. *"Block 36A repair valve"*) or
recorded as general farm tasks with no block. There are no scheduled
reminders — tasks are surfaced only when you ask.

### Logging tasks

| Example                                                  | What it does                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------ |
| `/task fix leak Block 4`                                 | Logs a task on Block 4 with normal priority.                       |
| `/task Block 36A repair valve priority high`             | Logs a high-priority task on Block 36A.                            |
| `/task order parts for tractor priority urgent`          | Logs a general (no-block) urgent task.                             |
| `/task paint shed notes weather permitting`              | Captures `weather permitting` in the notes field.                  |

Priorities: `low`, `normal` (default), `high`, `urgent`. Either `priority
high` or a bare word like `urgent` / `high priority` is recognized. `medium`
is treated as `normal`. If a message references multiple blocks, the bot
asks you to send one block per message; if it references no block, the
task is saved as a general farm task.

### Reports

| Command                | What it shows                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------- |
| `/tasks`               | All open tasks ordered by priority (urgent → high → normal → low) and then age.                   |
| `/task open`           | Same as `/tasks`.                                                                                  |
| `/task done <id>`      | Closes a task by id. Returns what was closed; warns if the id is unknown or already done.         |
| `/task Block 5B`       | Lists open + recent done tasks for that block (when the message has only a block ref, no title). |
| `/task summary`        | Open count by priority and recent (last 7 days) closed tasks.                                     |

Example:

```
/tasks
```

```
🛠 *Open tasks:*
🔴 #4 [urgent] general: order parts for tractor (2h)
🟠 #1 [high] Block 36A: repair valve (1d)
• #2 [normal] Block 4: fix leak (1d)
· #3 [low] general: paint the shed (3h)
```

### Notes & limitations

- Block-label semantics match the rest of the bot (`Block 4`, `Block 36A`,
  `Block 5B`, `Block 56/58`).
- One block per task message — `Block 4 and Block 5B fix leak` is rejected as
  ambiguous.
- Tasks are stored in the **same SQLite database** as harvest, irrigation,
  and spray (`farm_data.db` locally, or `FARM_DB_FILE` on Render). The
  `tasks` table is created on first use; existing data is never touched.
- No reminders, alarms, or scheduled notifications are sent. Tasks surface
  only when you ask via `/tasks` or `/task summary`.

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

## Weather alerts

`/weather` (alias `/alerts`) returns the current conditions and today's
forecast for the farm, plus a set of conservative operational alerts. It
is **on-demand only** — there is no scheduler, no DB write, and no
notification push. Each call hits Open-Meteo's free, key-less forecast
API and renders one short Telegram message.

### What the alerts cover

| Alert            | Default rule                              | Env var(s)                              |
| ---------------- | ----------------------------------------- | --------------------------------------- |
| Spray caution    | Current/gust/forecast wind ≥ 10 mph       | `WIND_ALERT_MPH`                        |
| Heat caution     | Today's high ≥ 95 °F                      | `HEAT_ALERT_F`                          |
| Frost caution    | Today's low ≤ 34 °F                       | `FROST_ALERT_F`                         |
| Rain caution     | Probability ≥ 50% **or** ≥ 0.10 in. rain  | `RAIN_PROB_ALERT_PCT`, `RAIN_AMOUNT_ALERT_IN` |
| Irrigation hint  | Hot **and** dry day (extra: also windy)   | combination of the above                |

Alerts only ever surface when you call `/weather` — nothing is pushed.
All thresholds are configurable via environment variables (see the table
in [Environment variables](#environment-variables)).

### Configuring location

Defaults point at Merced County, CA (37.30, -120.48). To run for another
ranch, set `FARM_LAT`, `FARM_LON`, and optionally `FARM_LOCATION_NAME` on
the bot service. No API key is needed — Open-Meteo is free and keyless.

Example:

```
/weather
```

```
🌤 Weather — Merced County, CA
Now: 86°F, wind 8 mph (gusts 12 mph)
Today: high 99°F, low 62°F, rain 0.00 in (10% chance)

*Alerts:*
• 🌬 Spray caution: wind up to 12 mph (threshold 10 mph). Hold off on spraying.
• 🥵 Heat caution: high 99°F (threshold 95°F). Start crews early; water often.
• 💧 Irrigation note: hot and dry plus wind — expect high ET, consider extending today's set.

_Source: Open-Meteo (no API key). Thresholds are configurable; always use your own judgment in the field._
```

### Notes & limitations

- If the weather API times out or returns a bad response, `/weather`
  returns a one-line "service unavailable" message — the bot does not
  crash, and no DB writes happen.
- Thresholds are intentionally conservative defaults; tune them via env
  vars if your operation runs hotter or windier.
- This is a decision-support aid. Always cross-check with on-the-ground
  observation before spraying, harvesting, or running frost protection.
