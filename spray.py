"""Spray/application log for the farm bot.

Records pesticide / foliar spray applications alongside harvest and
irrigation data in the same SQLite file. Optionally accepts a Re-Entry
Interval (REI, in hours) and Pre-Harvest Interval (PHI, in days) and
computes the matching restriction windows so the user can see active
re-entry or pre-harvest restrictions on demand.

This module is a *recordkeeping aid*. It does not look up label
restrictions for any product. REI/PHI must come from the user (i.e. the
product label / SDS). When omitted, no restriction window is computed and
the user is told to follow the label.

Reuses bot._field_matches_block / bot._BLOCK_REF_RE so block-label
semantics (Block 36A, 5B, 56/58) match the harvest path exactly.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import bot

SPRAY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS spray_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    field_id TEXT NOT NULL,
    block_label TEXT,
    field_name TEXT,
    product TEXT,
    details TEXT,
    rei_hours REAL,
    phi_days REAL,
    rei_end_at TEXT,
    phi_end_at TEXT,
    created_at TEXT NOT NULL
)
"""


def init_spray_db(db_file: Optional[str] = None) -> None:
    """Create the spray_events table if it doesn't exist.

    Safe to call repeatedly. Never wipes existing data.
    """
    path = db_file or bot.DB_FILE
    bot._ensure_db_parent_dir(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(SPRAY_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


# REI: re-entry interval in hours (or convertible). Accept "rei 12h",
# "rei 12 hours", "rei 24" (assume hours), "rei 1d" (1 day = 24h).
_REI_RE = re.compile(
    r'\brei\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|days?|d)?\b',
    re.IGNORECASE,
)
# PHI: pre-harvest interval in days. Accept "phi 0d", "phi 1 day",
# "phi 7 days", "phi 0" (assume days), "phi 14".
_PHI_RE = re.compile(
    r'\bphi\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|hr|h)?\b',
    re.IGNORECASE,
)
_NOTES_RE = re.compile(r'\bnotes?\s+(.+)$', re.IGNORECASE)
# Subcommand-style: starts with one of today/open/restrictions/summary/help
_SUBCMD_RE = re.compile(
    r'^\s*(today|open|restrictions|restriction|summary|recent|help)\b',
    re.IGNORECASE,
)


def _hours_from_value(value: float, unit: Optional[str]) -> float:
    """Convert a (value, unit) pair to hours."""
    if unit is None:
        return float(value)
    u = unit.lower()
    if u in ("d", "day", "days"):
        return float(value) * 24.0
    return float(value)


def _days_from_value(value: float, unit: Optional[str]) -> float:
    """Convert a (value, unit) pair to days."""
    if unit is None:
        return float(value)
    u = unit.lower()
    if u in ("h", "hr", "hrs", "hour", "hours"):
        return float(value) / 24.0
    return float(value)


def parse_spray_message(text: str, fields):
    """Parse a spray log message.

    Returns one of:
      {"kind": "spray", field_id, block_label, field_name,
        product, details, rei_hours (or None), phi_days (or None), notes}
      {"kind": "ambiguous", "reason": str}
      {"kind": "unknown"}
    """
    if not text or not isinstance(text, str):
        return {"kind": "unknown"}

    body = text.strip()
    if not body:
        return {"kind": "unknown"}
    # Subcommands are handled by the command dispatcher, not here.
    if _SUBCMD_RE.match(body):
        return {"kind": "unknown"}

    text_lc = body.lower()
    block_refs = [m.group(1).upper() for m in bot._BLOCK_REF_RE.finditer(text_lc)]

    if not block_refs:
        return {
            "kind": "ambiguous",
            "reason": (
                "I couldn't tell which block this spray is for. Try "
                "'/spray Block 5B copper rei 12h phi 0d'."
            ),
        }
    if len(set(block_refs)) > 1:
        return {
            "kind": "ambiguous",
            "reason": (
                "Multiple blocks found in one message. Please log spray "
                "applications for one block at a time."
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

    rei_hours = None
    phi_days = None
    rei_match = _REI_RE.search(text_lc)
    if rei_match:
        try:
            rei_hours = _hours_from_value(float(rei_match.group(1)), rei_match.group(2))
        except (TypeError, ValueError):
            rei_hours = None
        if rei_hours is not None and (rei_hours < 0 or rei_hours > 24 * 60):
            return {
                "kind": "ambiguous",
                "reason": "REI must be between 0 and 1440 hours (60 days).",
            }
    phi_match = _PHI_RE.search(text_lc)
    if phi_match:
        try:
            phi_days = _days_from_value(float(phi_match.group(1)), phi_match.group(2))
        except (TypeError, ValueError):
            phi_days = None
        if phi_days is not None and (phi_days < 0 or phi_days > 365):
            return {
                "kind": "ambiguous",
                "reason": "PHI must be between 0 and 365 days.",
            }

    # Strip the block ref, REI/PHI, and "notes ..." tail to derive product/details.
    cleaned = bot._BLOCK_REF_RE.sub("", body)
    cleaned = _REI_RE.sub("", cleaned)
    cleaned = _PHI_RE.sub("", cleaned)
    notes = ""
    notes_match = _NOTES_RE.search(cleaned)
    if notes_match:
        notes = notes_match.group(1).strip()
        cleaned = _NOTES_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")

    if not cleaned:
        return {
            "kind": "ambiguous",
            "reason": (
                "I couldn't tell what was sprayed. Include the product "
                "name, e.g. '/spray Block 5B copper rei 12h phi 0d'."
            ),
        }

    parts = cleaned.split(maxsplit=1)
    product = parts[0]
    details = parts[1].strip() if len(parts) > 1 else ""

    field_id = str(matched["id"])
    block_label = matched.get("block_label") or bot._derive_block_label(matched.get("name", "")) or ref
    field_name = matched.get("name", "")

    return {
        "kind": "spray",
        "field_id": field_id,
        "block_label": block_label,
        "field_name": field_name,
        "product": product,
        "details": details,
        "rei_hours": rei_hours,
        "phi_days": phi_days,
        "notes": notes,
    }


def _now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_iso() -> str:
    return _now_local().isoformat(timespec="seconds")


def _today_iso_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def insert_spray_event(
    field_id,
    block_label,
    field_name,
    product,
    details="",
    rei_hours=None,
    phi_days=None,
    notes="",
    db_file: Optional[str] = None,
):
    """Insert a spray event. Returns (row_id, rei_end_iso_or_None, phi_end_iso_or_None)."""
    path = db_file or bot.DB_FILE
    init_spray_db(path)
    now = _now_local()
    rei_end = None
    phi_end = None
    if rei_hours is not None:
        rei_end = (now + timedelta(hours=float(rei_hours))).isoformat(timespec="seconds")
    if phi_days is not None:
        phi_end = (now + timedelta(days=float(phi_days))).isoformat(timespec="seconds")
    combined_details = details
    if notes:
        combined_details = f"{details} | notes: {notes}".strip(" |")
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO spray_events "
            "(date, field_id, block_label, field_name, product, details, "
            " rei_hours, phi_days, rei_end_at, phi_end_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _today_iso_date(), str(field_id), block_label, field_name,
                product, combined_details,
                None if rei_hours is None else float(rei_hours),
                None if phi_days is None else float(phi_days),
                rei_end, phi_end, now.isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return cur.lastrowid, rei_end, phi_end
    finally:
        conn.close()


def _row_to_dict(row):
    return {
        "id": row[0],
        "date": row[1],
        "field_id": row[2],
        "block_label": row[3] or "",
        "field_name": row[4] or "",
        "product": row[5] or "",
        "details": row[6] or "",
        "rei_hours": row[7],
        "phi_days": row[8],
        "rei_end_at": row[9],
        "phi_end_at": row[10],
        "created_at": row[11],
    }


def list_today(db_file: Optional[str] = None):
    path = db_file or bot.DB_FILE
    init_spray_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, date, field_id, block_label, field_name, product, "
            "details, rei_hours, phi_days, rei_end_at, phi_end_at, created_at "
            "FROM spray_events WHERE date = ? ORDER BY created_at ASC",
            (_today_iso_date(),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_recent(days: int = 7, db_file: Optional[str] = None):
    if days <= 0:
        days = 1
    path = db_file or bot.DB_FILE
    init_spray_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, date, field_id, block_label, field_name, product, "
            "details, rei_hours, phi_days, rei_end_at, phi_end_at, created_at "
            "FROM spray_events "
            "WHERE date >= date('now', ?) "
            "ORDER BY created_at DESC",
            (f"-{days - 1} days",),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_active_restrictions(db_file: Optional[str] = None, now: Optional[datetime] = None):
    """Return spray events with at least one restriction window still in effect.

    A restriction is "active" when its end timestamp is >= now. Events with
    no REI and no PHI (both None) never appear here.
    """
    path = db_file or bot.DB_FILE
    init_spray_db(path)
    if now is None:
        now = _now_local()
    now_iso = now.isoformat(timespec="seconds")
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT id, date, field_id, block_label, field_name, product, "
            "details, rei_hours, phi_days, rei_end_at, phi_end_at, created_at "
            "FROM spray_events "
            "WHERE (rei_end_at IS NOT NULL AND rei_end_at >= ?) "
            "   OR (phi_end_at IS NOT NULL AND phi_end_at >= ?) "
            "ORDER BY created_at ASC",
            (now_iso, now_iso),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def _restriction_status_for(event, now: Optional[datetime] = None):
    """Return ('rei_active'|'phi_active'|'none', message_fragments)."""
    if now is None:
        now = _now_local()
    fragments = []
    rei_status = None
    phi_status = None
    if event.get("rei_end_at"):
        try:
            end = datetime.fromisoformat(event["rei_end_at"])
            if end.tzinfo is None:
                end = end.replace(tzinfo=now.tzinfo)
            if end >= now:
                hours_left = max(0.0, round((end - now).total_seconds() / 3600.0, 1))
                rei_status = "active"
                fragments.append(
                    f"REI active until {end.isoformat(timespec='minutes')} "
                    f"({hours_left}h left)"
                )
            else:
                rei_status = "expired"
                fragments.append("REI expired")
        except ValueError:
            pass
    if event.get("phi_end_at"):
        try:
            end = datetime.fromisoformat(event["phi_end_at"])
            if end.tzinfo is None:
                end = end.replace(tzinfo=now.tzinfo)
            if end >= now:
                days_left = max(0.0, round((end - now).total_seconds() / 86400.0, 1))
                phi_status = "active"
                fragments.append(
                    f"PHI active until {end.isoformat(timespec='minutes')} "
                    f"({days_left}d left)"
                )
            else:
                phi_status = "expired"
                fragments.append("PHI expired")
        except ValueError:
            pass
    return rei_status, phi_status, fragments


def format_logged(event, rei_end_iso, phi_end_iso) -> str:
    """Format the confirmation reply after a spray is logged."""
    label = event["block_label"] or event["field_name"] or event["field_id"]
    product = event.get("product", "")
    bits = [f"🧪 Logged spray on *Block {label}*: {product}"]
    if event.get("details"):
        bits.append(f"Details: {event['details']}")
    if event.get("notes"):
        bits.append(f"Notes: {event['notes']}")
    if rei_end_iso:
        bits.append(f"REI ends *{rei_end_iso}* ({event['rei_hours']}h).")
    if phi_end_iso:
        bits.append(f"PHI ends *{phi_end_iso}* ({event['phi_days']}d).")
    if rei_end_iso is None and phi_end_iso is None:
        bits.append(
            "_No REI/PHI provided — no restriction reminder calculated. "
            "Always follow the product label and local regulations._"
        )
    return "\n".join(bits)


def format_today(rows) -> str:
    if not rows:
        return "🧪 *Spray today:* none logged."
    now = _now_local()
    lines = ["🧪 *Spray today:*"]
    for r in rows:
        label = r["block_label"] or r["field_name"] or r["field_id"]
        product = r["product"] or "(unspecified)"
        line = f"• Block {label}: {product}"
        _, _, frags = _restriction_status_for(r, now=now)
        if frags:
            line += " — " + "; ".join(frags)
        lines.append(line)
    return "\n".join(lines)


def format_summary(rows, days: int = 7) -> str:
    if not rows:
        return f"🧪 *Spray (last {days} days):* none logged."
    lines = [f"🧪 *Spray (last {days} days):*"]
    for r in rows:
        label = r["block_label"] or r["field_name"] or r["field_id"]
        product = r["product"] or "(unspecified)"
        date = r["date"]
        line = f"• {date} Block {label}: {product}"
        if r["rei_hours"] is not None:
            line += f" (REI {r['rei_hours']}h)"
        if r["phi_days"] is not None:
            line += f" (PHI {r['phi_days']}d)"
        lines.append(line)
    return "\n".join(lines)


def format_active_restrictions(rows) -> str:
    if not rows:
        return (
            "🧪 No active spray restrictions. "
            "_Always follow the product label._"
        )
    now = _now_local()
    lines = ["🧪 *Active spray restrictions:*"]
    for r in rows:
        label = r["block_label"] or r["field_name"] or r["field_id"]
        product = r["product"] or "(unspecified)"
        _, _, frags = _restriction_status_for(r, now=now)
        active_frags = [f for f in frags if "active" in f]
        if not active_frags:
            continue
        lines.append(f"• Block {label} — {product}: " + "; ".join(active_frags))
    if len(lines) == 1:
        return (
            "🧪 No active spray restrictions. "
            "_Always follow the product label._"
        )
    return "\n".join(lines)
