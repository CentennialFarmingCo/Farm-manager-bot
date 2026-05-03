"""Task / repair tracking for the farm bot.

Records general farm tasks and field-scoped repair items (e.g. "fix leak
Block 4", "Block 36A repair valve priority high"). Stored alongside
harvest, irrigation, and spray data in the same SQLite file.

Reuses bot._field_matches_block / bot._BLOCK_REF_RE so block-label
semantics (Block 36A, 5B, 56/58) match the rest of the bot exactly. Tasks
without a block reference are saved as general farm tasks (field_id NULL).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import bot

TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'normal',
    field_id TEXT,
    block_label TEXT,
    field_name TEXT,
    title TEXT NOT NULL,
    notes TEXT
)
"""

VALID_PRIORITIES = ("low", "normal", "high", "urgent")
_PRIORITY_ORDER = {p: i for i, p in enumerate(("urgent", "high", "normal", "low"))}

_PRIORITY_RE = re.compile(
    r'\bpriority\s*[:=]?\s*(urgent|high|normal|low|med(?:ium)?)\b',
    re.IGNORECASE,
)
# Bare priority words ("urgent", "high priority", "high-priority"). Only
# treated as priority when standalone — never strip a real product/title word.
_BARE_PRIORITY_RE = re.compile(
    r'\b(urgent|high\s*priority|high-priority|low\s*priority|low-priority)\b',
    re.IGNORECASE,
)
_NOTES_RE = re.compile(r'\bnotes?\s+(.+)$', re.IGNORECASE)
# Subcommands handled by the dispatcher, not by the parser.
_SUBCMD_RE = re.compile(
    r'^\s*(open|list|all|done|close|complete|completed|summary|recent|help)\b',
    re.IGNORECASE,
)


def init_tasks_db(db_file: Optional[str] = None) -> None:
    """Create the tasks table if it doesn't exist.

    Safe to call repeatedly. Never wipes existing data.
    """
    path = db_file or bot.DB_FILE
    bot._ensure_db_parent_dir(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(TASKS_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def _normalize_priority(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().lower()
    s = re.sub(r"\s+", " ", s)
    if s in ("urgent",):
        return "urgent"
    if s in ("high", "high priority", "high-priority"):
        return "high"
    if s in ("normal", "med", "medium"):
        return "normal"
    if s in ("low", "low priority", "low-priority"):
        return "low"
    return None


def _now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _now_iso() -> str:
    return _now_local().isoformat(timespec="seconds")


def parse_task_message(text: str, fields):
    """Parse a task creation message.

    Returns one of:
      {"kind": "task", "field_id"|None, "block_label"|None, "field_name"|None,
        "title": str, "priority": str, "notes": str}
      {"kind": "ambiguous", "reason": str}
      {"kind": "list_for_block", "block_label": str, "field_id": str, "field_name": str}
      {"kind": "unknown"}
    """
    if not text or not isinstance(text, str):
        return {"kind": "unknown"}

    body = text.strip()
    if not body:
        return {"kind": "unknown"}
    if _SUBCMD_RE.match(body):
        return {"kind": "unknown"}

    text_lc = body.lower()
    block_refs = [m.group(1).upper() for m in bot._BLOCK_REF_RE.finditer(text_lc)]
    distinct_refs = list(dict.fromkeys(block_refs))

    if len(distinct_refs) > 1:
        return {
            "kind": "ambiguous",
            "reason": (
                "Multiple blocks found in one message. Please log tasks for "
                "one block at a time, or omit the block for a general task."
            ),
        }

    matched = None
    if distinct_refs:
        ref = distinct_refs[0]
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

    priority = None
    pmatch = _PRIORITY_RE.search(text_lc)
    if pmatch:
        priority = _normalize_priority(pmatch.group(1))
    cleaned = bot._BLOCK_REF_RE.sub("", body)
    cleaned = _PRIORITY_RE.sub("", cleaned)
    if priority is None:
        bp = _BARE_PRIORITY_RE.search(cleaned)
        if bp:
            priority = _normalize_priority(bp.group(1))
            cleaned = _BARE_PRIORITY_RE.sub("", cleaned)
    else:
        cleaned = _BARE_PRIORITY_RE.sub("", cleaned)

    notes = ""
    nmatch = _NOTES_RE.search(cleaned)
    if nmatch:
        notes = nmatch.group(1).strip()
        cleaned = _NOTES_RE.sub("", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")

    # Special case: user typed only "Block 5B" with nothing else → treat as
    # a request to list tasks for that block, not a task creation.
    if matched is not None and not cleaned:
        block_label = (
            matched.get("block_label")
            or bot._derive_block_label(matched.get("name", ""))
            or distinct_refs[0]
        )
        return {
            "kind": "list_for_block",
            "block_label": block_label,
            "field_id": str(matched["id"]),
            "field_name": matched.get("name", ""),
        }

    if not cleaned:
        return {
            "kind": "ambiguous",
            "reason": (
                "I couldn't tell what the task is. Try '/task fix leak Block 4' "
                "or '/task Block 36A repair valve priority high'."
            ),
        }

    if priority is None:
        priority = "normal"

    if matched is None:
        return {
            "kind": "task",
            "field_id": None,
            "block_label": None,
            "field_name": None,
            "title": cleaned,
            "priority": priority,
            "notes": notes,
        }

    block_label = (
        matched.get("block_label")
        or bot._derive_block_label(matched.get("name", ""))
        or distinct_refs[0]
    )
    return {
        "kind": "task",
        "field_id": str(matched["id"]),
        "block_label": block_label,
        "field_name": matched.get("name", ""),
        "title": cleaned,
        "priority": priority,
        "notes": notes,
    }


def insert_task(
    title: str,
    field_id: Optional[str] = None,
    block_label: Optional[str] = None,
    field_name: Optional[str] = None,
    priority: str = "normal",
    notes: str = "",
    db_file: Optional[str] = None,
) -> int:
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    now = _now_iso()
    pri = _normalize_priority(priority) or "normal"
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO tasks "
            "(created_at, updated_at, completed_at, status, priority, "
            " field_id, block_label, field_name, title, notes) "
            "VALUES (?, ?, NULL, 'open', ?, ?, ?, ?, ?, ?)",
            (
                now, now, pri,
                None if field_id is None else str(field_id),
                block_label, field_name, title, notes,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row_to_dict(row):
    return {
        "id": row[0],
        "created_at": row[1],
        "updated_at": row[2],
        "completed_at": row[3],
        "status": row[4],
        "priority": row[5],
        "field_id": row[6],
        "block_label": row[7],
        "field_name": row[8],
        "title": row[9],
        "notes": row[10] or "",
    }


_SELECT_COLS = (
    "id, created_at, updated_at, completed_at, status, priority, "
    "field_id, block_label, field_name, title, notes"
)


def list_open(db_file: Optional[str] = None):
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM tasks "
            "WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()
    items = [_row_to_dict(r) for r in rows]
    items.sort(key=lambda t: (
        _PRIORITY_ORDER.get(t["priority"], 99),
        t["created_at"] or "",
    ))
    return items


def list_for_field(field_id, include_done: bool = False,
                   db_file: Optional[str] = None):
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    conn = sqlite3.connect(path)
    try:
        if include_done:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM tasks "
                "WHERE field_id = ? ORDER BY status='open' DESC, created_at ASC",
                (str(field_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM tasks "
                "WHERE field_id = ? AND status = 'open' ORDER BY created_at ASC",
                (str(field_id),),
            ).fetchall()
    finally:
        conn.close()
    items = [_row_to_dict(r) for r in rows]
    items.sort(key=lambda t: (
        0 if t["status"] == "open" else 1,
        _PRIORITY_ORDER.get(t["priority"], 99),
        t["created_at"] or "",
    ))
    return items


def get_task(task_id, db_file: Optional[str] = None):
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def close_task(task_id, db_file: Optional[str] = None):
    """Close a task. Returns ('closed', task_dict) | ('already_done', task_dict)
    | ('not_found', None)."""
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return "not_found", None
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM tasks WHERE id = ?",
            (tid,),
        ).fetchone()
        if row is None:
            return "not_found", None
        task = _row_to_dict(row)
        if task["status"] == "done":
            return "already_done", task
        now = _now_iso()
        conn.execute(
            "UPDATE tasks SET status='done', completed_at=?, updated_at=? "
            "WHERE id = ?",
            (now, now, tid),
        )
        conn.commit()
        task["status"] = "done"
        task["completed_at"] = now
        task["updated_at"] = now
        return "closed", task
    finally:
        conn.close()


def list_recent_completed(days: int = 7, db_file: Optional[str] = None):
    if days <= 0:
        days = 1
    path = db_file or bot.DB_FILE
    init_tasks_db(path)
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM tasks "
            "WHERE status = 'done' AND completed_at IS NOT NULL "
            "  AND date(completed_at) >= date('now', ?) "
            "ORDER BY completed_at DESC",
            (f"-{days - 1} days",),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def summary(db_file: Optional[str] = None, recent_days: int = 7):
    open_items = list_open(db_file=db_file)
    recent_done = list_recent_completed(days=recent_days, db_file=db_file)
    counts = {p: 0 for p in VALID_PRIORITIES}
    for t in open_items:
        counts[t["priority"]] = counts.get(t["priority"], 0) + 1
    return {
        "open_total": len(open_items),
        "open_by_priority": counts,
        "open_items": open_items,
        "recent_completed": recent_done,
        "recent_days": recent_days,
    }


# ---- formatters --------------------------------------------------------

_PRIORITY_BADGE = {
    "urgent": "🔴",
    "high": "🟠",
    "normal": "•",
    "low": "·",
}


def _label_of(t):
    return t.get("block_label") or t.get("field_name") or "(general)"


def _age_str(created_at: str, now: Optional[datetime] = None) -> str:
    if not created_at:
        return ""
    if now is None:
        now = _now_local()
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo)
    except ValueError:
        return ""
    delta = now - dt
    days = delta.days
    if days >= 1:
        return f"{days}d"
    hours = int(delta.total_seconds() // 3600)
    if hours >= 1:
        return f"{hours}h"
    mins = max(1, int(delta.total_seconds() // 60))
    return f"{mins}m"


def format_logged(task) -> str:
    label = _label_of(task)
    badge = _PRIORITY_BADGE.get(task["priority"], "•")
    where = "(general farm task)" if not task.get("field_id") else f"Block {label}"
    bits = [
        f"{badge} Logged task #{task['id']} — *{where}*: {task['title']}",
        f"Priority: {task['priority']}",
    ]
    if task.get("notes"):
        bits.append(f"Notes: {task['notes']}")
    return "\n".join(bits)


def format_open_list(items, header: str = "🛠 *Open tasks:*") -> str:
    if not items:
        return "🛠 No open tasks. ✅"
    lines = [header]
    for t in items:
        badge = _PRIORITY_BADGE.get(t["priority"], "•")
        label = _label_of(t)
        where = f"Block {label}" if t.get("field_id") else "general"
        age = _age_str(t["created_at"])
        age_str = f" ({age})" if age else ""
        lines.append(
            f"{badge} #{t['id']} [{t['priority']}] {where}: {t['title']}{age_str}"
        )
    return "\n".join(lines)


def format_block_list(items, block_label: str) -> str:
    if not items:
        return f"🛠 No tasks for *Block {block_label}*."
    open_items = [t for t in items if t["status"] == "open"]
    done_items = [t for t in items if t["status"] != "open"]
    lines = [f"🛠 *Tasks for Block {block_label}:*"]
    if open_items:
        lines.append("_Open:_")
        for t in open_items:
            badge = _PRIORITY_BADGE.get(t["priority"], "•")
            age = _age_str(t["created_at"])
            age_str = f" ({age})" if age else ""
            lines.append(f"{badge} #{t['id']} [{t['priority']}] {t['title']}{age_str}")
    if done_items:
        lines.append("_Recently done:_")
        for t in done_items[:5]:
            lines.append(f"✅ #{t['id']} {t['title']}")
    return "\n".join(lines)


def format_close_result(status: str, task) -> str:
    if status == "not_found":
        return (
            "⚠️ I couldn't find a task with that id. Use `/tasks` to see "
            "open tasks."
        )
    if status == "already_done":
        completed = task.get("completed_at") or "earlier"
        return (
            f"ℹ️ Task #{task['id']} was already closed ({completed}).\n"
            f"Title: {task['title']}"
        )
    label = _label_of(task)
    where = f"Block {label}" if task.get("field_id") else "general"
    return (
        f"✅ Closed task #{task['id']} — *{where}*: {task['title']}"
    )


def format_summary(snap) -> str:
    counts = snap["open_by_priority"]
    urgent = counts.get("urgent", 0)
    high = counts.get("high", 0)
    normal = counts.get("normal", 0)
    low = counts.get("low", 0)
    lines = [
        "🛠 *Task summary*",
        f"Open: *{snap['open_total']}* "
        f"(🔴 {urgent} urgent, 🟠 {high} high, • {normal} normal, · {low} low)",
    ]
    if snap["open_items"]:
        # Show top 3 by priority/age for quick glance.
        lines.append("_Top open:_")
        for t in snap["open_items"][:3]:
            badge = _PRIORITY_BADGE.get(t["priority"], "•")
            label = _label_of(t)
            where = f"Block {label}" if t.get("field_id") else "general"
            age = _age_str(t["created_at"])
            age_str = f" ({age})" if age else ""
            lines.append(
                f"{badge} #{t['id']} [{t['priority']}] {where}: "
                f"{t['title']}{age_str}"
            )
    if snap["recent_completed"]:
        lines.append(f"_Closed in last {snap['recent_days']}d:_")
        for t in snap["recent_completed"][:5]:
            label = _label_of(t)
            where = f"Block {label}" if t.get("field_id") else "general"
            lines.append(f"✅ #{t['id']} {where}: {t['title']}")
    elif snap["open_total"] == 0:
        lines.append("_No tasks closed recently._")
    return "\n".join(lines)
