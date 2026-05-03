"""Irrigation tracking for the farm bot.

Stores water/pump events alongside harvest data in the same SQLite file.
Supports three event types:

  - "duration": one-shot log, e.g. "Block 4 12 hours" — total hours irrigated.
  - "start":    block began irrigating right now (open session).
  - "stop":     close the most recent open start for that block; compute hours.

Reuses bot._field_matches_block / bot._BLOCK_REF_RE so block-label semantics
(Block 36A, 5B, 56/58) match the harvest path exactly.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import bot

IRRIGATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS irrigation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    field_id TEXT NOT NULL,
    block_label TEXT,
    field_name TEXT,
    event_type TEXT NOT NULL,
    hours REAL,
    notes TEXT,
    start_event_id INTEGER,
    created_at TEXT NOT NULL
)
"""


def init_irrigation_db(db_file: Optional[str] = None) -> None:
    """Create the irrigation_events table if it doesn't exist.

    Safe to call repeatedly. Never wipes existing data.
    """
    path = db_file or bot.DB_FILE
    bot._ensure_db_parent_dir(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(IRRIGATION_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


_HOURS_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b',
    re.IGNORECASE,
)
_STARTED_RE = re.compile(r'\b(start(?:ed|ing)?|on|begin)\b', re.IGNORECASE)
_STOPPED_RE = re.compile(r'\b(stop(?:ped|ping)?|off|end(?:ed)?|done)\b', re.IGNORECASE)


def parse_irrigation_message(text: str, fields):
    """Parse an irrigation log message.

    Returns one of:
      {"kind": "irrigation_duration", "field_id", "block_label", "field_name",
        "hours": float, "notes": str}
      {"kind": "irrigation_start",    "field_id", "block_label", "field_name", "notes"}
      {"kind": "irrigation_stop",     "field_id", "block_label", "field_name", "notes"}
      {"kind": "ambiguous", "reason": str}
      {"kind": "unknown"}
    """
    if not text or not isinstance(text, str):
        return {"kind": "unknown"}

    text_lc = text.lower()
    block_refs = [m.group(1).upper() for m in bot._BLOCK_REF_RE.finditer(text_lc)]

    has_hours = bool(_HOURS_RE.search(text_lc))
    has_start = bool(_STARTED_RE.search(text_lc))
    has_stop = bool(_STOPPED_RE.search(text_lc))

    if not (has_hours or has_start or has_stop):
        return {"kind": "unknown"}

    if not block_refs:
        return {
            "kind": "ambiguous",
            "reason": (
                "I couldn't tell which block this irrigation is for. "
                "Try '/irrigation Block 4 12 hours' or '/irrigation Block 5B started'."
            ),
        }
    if len(set(block_refs)) > 1:
        return {
            "kind": "ambiguous",
            "reason": (
                "Multiple blocks found in one message. Please log irrigation "
                "for one block at a time."
            ),
        }

    ref = block_refs[0]
    matched = None
    for fld in fields:
        if bot._field_matches_block(fld, ref):
            matched = fld
            break
    if matched is None:
        return {
            "kind": "ambiguous",
            "reason": (
                f"I don't recognize block label: {ref}. "
                "Please double-check and resend."
            ),
        }

    field_id = str(matched["id"])
    block_label = matched.get("block_label") or bot._derive_block_label(matched.get("name", "")) or ref
    field_name = matched.get("name", "")

    if has_hours:
        if has_start or has_stop:
            return {
                "kind": "ambiguous",
                "reason": (
                    "Mix of duration and start/stop in one message. "
                    "Send hours OR start/stop separately."
                ),
            }
        hours_matches = _HOURS_RE.findall(text_lc)
        if len(set(hours_matches)) > 1:
            return {
                "kind": "ambiguous",
                "reason": (
                    "Multiple different hour values found. "
                    "Please send one duration per message."
                ),
            }
        try:
            hours = float(hours_matches[0])
        except (ValueError, IndexError):
            return {"kind": "ambiguous", "reason": "Could not parse hours."}
        if hours <= 0 or hours > 24 * 14:
            return {
                "kind": "ambiguous",
                "reason": (
                    "Hours must be greater than 0 and not more than 336 (14 days)."
                ),
            }
        return {
            "kind": "irrigation_duration",
            "field_id": field_id,
            "block_label": block_label,
            "field_name": field_name,
            "hours": hours,
            "notes": "",
        }

    if has_start and has_stop:
        return {
            "kind": "ambiguous",
            "reason": "Both 'start' and 'stop' detected. Please send one at a time.",
        }
    if has_start:
        return {
            "kind": "irrigation_start",
            "field_id": field_id,
            "block_label": block_label,
            "field_name": field_name,
            "notes": "",
        }
    return {
        "kind": "irrigation_stop",
        "field_id": field_id,
        "block_label": block_label,
        "field_name": field_name,
        "notes": "",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today_iso_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def insert_duration_event(field_id, block_label, field_name, hours, notes="",
                          db_file: Optional[str] = None) -> int:
    """Insert a duration event. Returns row id."""
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO irrigation_events "
            "(date, field_id, block_label, field_name, event_type, hours, notes, "
            "start_event_id, created_at) "
            "VALUES (?, ?, ?, ?, 'duration', ?, ?, NULL, ?)",
            (_today_iso_date(), str(field_id), block_label, field_name,
             float(hours), notes, _now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def insert_start_event(field_id, block_label, field_name, notes="",
                       db_file: Optional[str] = None):
    """Insert a start event. Returns (row_id, already_running_id_or_None)."""
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    conn = sqlite3.connect(path)
    try:
        existing = conn.execute(
            "SELECT id FROM irrigation_events "
            "WHERE field_id = ? AND event_type = 'start' "
            "  AND id NOT IN (SELECT start_event_id FROM irrigation_events "
            "                 WHERE event_type = 'stop' AND start_event_id IS NOT NULL) "
            "ORDER BY id DESC LIMIT 1",
            (str(field_id),),
        ).fetchone()
        if existing:
            return None, existing[0]
        cur = conn.execute(
            "INSERT INTO irrigation_events "
            "(date, field_id, block_label, field_name, event_type, hours, notes, "
            "start_event_id, created_at) "
            "VALUES (?, ?, ?, ?, 'start', NULL, ?, NULL, ?)",
            (_today_iso_date(), str(field_id), block_label, field_name,
             notes, _now_iso()),
        )
        conn.commit()
        return cur.lastrowid, None
    finally:
        conn.close()


def insert_stop_event(field_id, block_label, field_name, notes="",
                      db_file: Optional[str] = None):
    """Close the latest open start for this field.

    Returns (stop_id, hours, start_iso) on success, or (None, None, None)
    if no matching open start exists.
    """
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    conn = sqlite3.connect(path)
    try:
        open_row = conn.execute(
            "SELECT id, created_at FROM irrigation_events "
            "WHERE field_id = ? AND event_type = 'start' "
            "  AND id NOT IN (SELECT start_event_id FROM irrigation_events "
            "                 WHERE event_type = 'stop' AND start_event_id IS NOT NULL) "
            "ORDER BY id DESC LIMIT 1",
            (str(field_id),),
        ).fetchone()
        if not open_row:
            return None, None, None
        start_id, start_iso = open_row
        try:
            start_dt = datetime.fromisoformat(start_iso)
        except ValueError:
            start_dt = None
        now_dt = datetime.now(timezone.utc).astimezone()
        if start_dt is not None:
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now_dt.tzinfo)
            delta = now_dt - start_dt
            hours = round(delta.total_seconds() / 3600.0, 2)
            if hours < 0:
                hours = 0.0
        else:
            hours = 0.0

        cur = conn.execute(
            "INSERT INTO irrigation_events "
            "(date, field_id, block_label, field_name, event_type, hours, notes, "
            "start_event_id, created_at) "
            "VALUES (?, ?, ?, ?, 'stop', ?, ?, ?, ?)",
            (_today_iso_date(), str(field_id), block_label, field_name,
             hours, notes, start_id, now_dt.isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid, hours, start_iso
    finally:
        conn.close()


def list_open_sessions(db_file: Optional[str] = None):
    """Return a list of dicts for currently-running (open start) sessions."""
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, field_id, block_label, field_name, created_at "
            "FROM irrigation_events "
            "WHERE event_type = 'start' "
            "  AND id NOT IN (SELECT start_event_id FROM irrigation_events "
            "                 WHERE event_type = 'stop' AND start_event_id IS NOT NULL) "
            "ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    now_dt = datetime.now(timezone.utc).astimezone()
    for r in rows:
        start_iso = r[4]
        elapsed_hours = None
        try:
            start_dt = datetime.fromisoformat(start_iso)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=now_dt.tzinfo)
            elapsed_hours = round((now_dt - start_dt).total_seconds() / 3600.0, 2)
        except ValueError:
            pass
        out.append({
            "id": r[0],
            "field_id": r[1],
            "block_label": r[2],
            "field_name": r[3],
            "started_at": start_iso,
            "elapsed_hours": elapsed_hours,
        })
    return out


def summarize_today(db_file: Optional[str] = None):
    """Return today's irrigation summary by block.

    Counts: duration events' hours + closed stop events' hours, grouped by
    field_id (using whatever block_label was logged with that event).
    """
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    today = _today_iso_date()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT field_id, COALESCE(block_label, ''), COALESCE(field_name, ''), "
            "       COALESCE(SUM(hours), 0.0) "
            "FROM irrigation_events "
            "WHERE date = ? AND event_type IN ('duration', 'stop') "
            "GROUP BY field_id, block_label, field_name "
            "ORDER BY block_label, field_id",
            (today,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"field_id": r[0], "block_label": r[1], "field_name": r[2],
         "hours": round(float(r[3]), 2)}
        for r in rows
    ]


def summarize_recent(days: int = 7, db_file: Optional[str] = None):
    """Return totals by block for the last `days` days (inclusive of today)."""
    if days <= 0:
        days = 1
    path = db_file or bot.DB_FILE
    init_irrigation_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT field_id, COALESCE(block_label, ''), COALESCE(field_name, ''), "
            "       COALESCE(SUM(hours), 0.0) "
            "FROM irrigation_events "
            "WHERE event_type IN ('duration', 'stop') "
            "  AND date >= date('now', ?) "
            "GROUP BY field_id, block_label, field_name "
            "ORDER BY block_label, field_id",
            (f"-{days - 1} days",),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"field_id": r[0], "block_label": r[1], "field_name": r[2],
         "hours": round(float(r[3]), 2)}
        for r in rows
    ]


def format_open_sessions(sessions) -> str:
    if not sessions:
        return "💧 No blocks are currently irrigating."
    lines = ["💧 *Currently irrigating:*"]
    for s in sessions:
        label = s["block_label"] or s["field_name"] or s["field_id"]
        elapsed = s["elapsed_hours"]
        elapsed_str = f"{elapsed}h" if elapsed is not None else "?"
        lines.append(
            f"• Block {label} — started {s['started_at']} (elapsed {elapsed_str})"
        )
    return "\n".join(lines)


def format_summary(rows, header: str) -> str:
    if not rows:
        return f"{header}\n(no irrigation logged)"
    total = round(sum(r["hours"] for r in rows), 2)
    lines = [header]
    for r in rows:
        label = r["block_label"] or r["field_name"] or r["field_id"]
        lines.append(f"• Block {label}: {r['hours']}h")
    lines.append(f"*Total: {total}h*")
    return "\n".join(lines)
