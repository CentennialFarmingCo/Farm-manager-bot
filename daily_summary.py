"""Daily farm summary for the bot's `/today` command.

Aggregates today's activity from the existing SQLite database (harvest +
irrigation_events) into a single Telegram-friendly digest. Reuses bot.DB_FILE
and irrigation helpers; no schema changes, no new tables, no external APIs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

import bot
import irrigation


WORKER_RATE_PER_BIN = 30
COMMISSION_MULTIPLIER = 1.35
LBS_PER_BIN = 1000


def _today_iso_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _harvest_today_rows(db_file: Optional[str] = None):
    """Return today's harvest grouped by field_id+variety.

    Each row: {"field_id", "variety", "bins"}.
    """
    path = db_file or bot.DB_FILE
    bot.init_db(path)
    today = _today_iso_date()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT field_id, COALESCE(variety, ''), COALESCE(SUM(bins), 0) "
            "FROM harvest WHERE date = ? "
            "GROUP BY field_id, variety "
            "ORDER BY field_id",
            (today,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"field_id": r[0], "variety": r[1], "bins": int(r[2] or 0)}
        for r in rows
        if int(r[2] or 0) > 0
    ]


def _resolve_block_label(field_id, fields):
    for f in fields:
        if str(f.get("id", "")) == str(field_id):
            label = f.get("block_label") or bot._derive_block_label(f.get("name", ""))
            return label, f.get("name", ""), f.get("variety", "")
    return None, "", ""


def collect_summary(db_file: Optional[str] = None, fields=None):
    """Build a structured snapshot of today's farm activity.

    Returns a dict with harvest + irrigation breakdowns and totals.
    """
    path = db_file or bot.DB_FILE
    if fields is None:
        try:
            fields = bot.load_fields()
        except (FileNotFoundError, KeyError, ValueError):
            fields = []

    harvest_rows = _harvest_today_rows(path)
    harvest_items = []
    for r in harvest_rows:
        label, name, fld_variety = _resolve_block_label(r["field_id"], fields)
        harvest_items.append({
            "field_id": r["field_id"],
            "block_label": label,
            "field_name": name,
            "variety": r["variety"] or fld_variety,
            "bins": r["bins"],
        })
    total_bins_today = sum(item["bins"] for item in harvest_items)

    irrigation_rows = irrigation.summarize_today(db_file=path)
    open_sessions = irrigation.list_open_sessions(db_file=path)
    total_hours_today = round(sum(r["hours"] for r in irrigation_rows), 2)

    worker_pay_today = total_bins_today * WORKER_RATE_PER_BIN
    cost_today = round(worker_pay_today * COMMISSION_MULTIPLIER, 2)

    return {
        "date": _today_iso_date(),
        "harvest": harvest_items,
        "harvest_total_bins": total_bins_today,
        "irrigation": irrigation_rows,
        "irrigation_total_hours": total_hours_today,
        "open_irrigation_sessions": open_sessions,
        "labor": {
            "bins": total_bins_today,
            "worker_pay": worker_pay_today,
            "total_cost": cost_today,
        },
    }


def _format_harvest_section(items):
    if not items:
        return ["🍑 *Harvest:* none logged today."]
    lines = ["🍑 *Harvest today:*"]
    total = 0
    for it in items:
        label = it["block_label"] or it["field_name"] or it["field_id"]
        variety = it["variety"]
        variety_str = f" — {variety}" if variety else ""
        lines.append(f"• Block {label}: {it['bins']} bins{variety_str}")
        total += it["bins"]
    lines.append(f"*Total: {total} bins*")
    return lines


def _format_irrigation_section(rows, open_sessions):
    lines = []
    if rows:
        lines.append("💧 *Irrigation today:*")
        total = 0.0
        for r in rows:
            label = r["block_label"] or r["field_name"] or r["field_id"]
            lines.append(f"• Block {label}: {r['hours']}h")
            total += r["hours"]
        lines.append(f"*Total: {round(total, 2)}h*")
    else:
        lines.append("💧 *Irrigation:* no completed hours logged today.")

    if open_sessions:
        lines.append("")
        lines.append("🟢 *Currently irrigating:*")
        for s in open_sessions:
            label = s["block_label"] or s["field_name"] or s["field_id"]
            elapsed = s["elapsed_hours"]
            elapsed_str = f"{elapsed}h elapsed" if elapsed is not None else "elapsed ?"
            lines.append(f"• Block {label} (since {s['started_at']}, {elapsed_str})")
    return lines


def _format_labor_section(labor):
    if labor["bins"] <= 0:
        return ["💰 *Labor today:* no bins logged."]
    return [
        "💰 *Labor today:*",
        f"• Bins: {labor['bins']}",
        f"• Worker pay: ${labor['worker_pay']:,}",
        f"• Total cost (35% commission): ${labor['total_cost']:,}",
    ]


EMPTY_HELP = (
    "_No activity logged yet today._\n\n"
    "Log a harvest with: `Block 4 18 bins`\n"
    "Log irrigation with: `/irrigation Block 4 12 hours`\n"
    "Start a pump session with: `/irrigation Block 5B started`"
)


def format_summary(snapshot) -> str:
    """Render the snapshot as a Telegram-friendly Markdown digest."""
    has_harvest = snapshot["harvest_total_bins"] > 0
    has_irrigation = (
        snapshot["irrigation_total_hours"] > 0
        or bool(snapshot["open_irrigation_sessions"])
    )

    header = f"📋 *Daily farm summary — {snapshot['date']}*"

    if not has_harvest and not has_irrigation:
        return f"{header}\n\n{EMPTY_HELP}"

    sections = [header, ""]
    sections.extend(_format_harvest_section(snapshot["harvest"]))
    sections.append("")
    sections.extend(_format_irrigation_section(
        snapshot["irrigation"], snapshot["open_irrigation_sessions"],
    ))
    sections.append("")
    sections.extend(_format_labor_section(snapshot["labor"]))
    sections.append("")
    sections.append("Tap /dashboard for the field map.")
    return "\n".join(sections).rstrip()
