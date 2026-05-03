import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import tasks  # noqa: E402


REAL_FIELDS = [
    {"id": "5", "name": "Johnston Block 4", "block_label": "4",
     "variety": "Parade Freestone Peach", "acres": 13},
    {"id": "6", "name": "Johnston Block 5A", "block_label": "5A",
     "variety": "Tra Zee Freestone Peach", "acres": 15},
    {"id": "7", "name": "Johnston Block 5B", "block_label": "5B",
     "variety": "Angelus Freestone Peach", "acres": 15},
    {"id": "8", "name": "Johnston Block 56/58", "block_label": "56/58",
     "variety": "Autumn Flame Freestone Peach", "acres": 30},
    {"id": "9", "name": "Johnston Block 36A", "block_label": "36A",
     "variety": "Carnival Freestone Peach", "acres": 18.5},
    {"id": "10", "name": "Johnston Block 36B", "block_label": "36B",
     "variety": "Kaweah Freestone Peach", "acres": 18.5},
    {"id": "36", "name": "Fagundes Block 66", "block_label": "66",
     "variety": "Independence Almond", "acres": 27},
]


# --- parser tests ---

def test_parse_general_task_no_block():
    parsed = tasks.parse_task_message("order tractor parts", REAL_FIELDS)
    assert parsed["kind"] == "task"
    assert parsed["field_id"] is None
    assert parsed["block_label"] is None
    assert "tractor" in parsed["title"]
    assert parsed["priority"] == "normal"


def test_parse_block_task_basic():
    parsed = tasks.parse_task_message("fix leak Block 4", REAL_FIELDS)
    assert parsed["kind"] == "task"
    assert parsed["field_id"] == "5"
    assert parsed["block_label"] == "4"
    assert "fix leak" in parsed["title"].lower()
    assert parsed["priority"] == "normal"


def test_parse_block_task_priority_high():
    parsed = tasks.parse_task_message(
        "Block 36A repair valve priority high", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["field_id"] == "9"
    assert parsed["block_label"] == "36A"
    assert "repair valve" in parsed["title"].lower()
    assert parsed["priority"] == "high"


def test_parse_priority_urgent_bare_word():
    parsed = tasks.parse_task_message(
        "order parts for tractor urgent", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["priority"] == "urgent"
    assert "urgent" not in parsed["title"].lower()


def test_parse_priority_low():
    parsed = tasks.parse_task_message(
        "paint the shed priority low", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["priority"] == "low"
    assert parsed["field_id"] is None


def test_parse_priority_normal_default():
    parsed = tasks.parse_task_message("mow the lawn", REAL_FIELDS)
    assert parsed["priority"] == "normal"


def test_parse_priority_medium_normalizes_to_normal():
    parsed = tasks.parse_task_message(
        "mow the lawn priority medium", REAL_FIELDS,
    )
    assert parsed["priority"] == "normal"


def test_parse_composite_block_label():
    parsed = tasks.parse_task_message(
        "Block 56/58 inspect drip line", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["field_id"] == "8"
    assert parsed["block_label"] == "56/58"


def test_parse_suffix_block_label():
    parsed = tasks.parse_task_message(
        "Block 5B replace filter", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["field_id"] == "7"
    assert parsed["block_label"] == "5B"


def test_parse_block_label_not_internal_id():
    # id=5 is internally "Johnston Block 4"; user typed "Block 4".
    parsed = tasks.parse_task_message("Block 4 prune limbs", REAL_FIELDS)
    assert parsed["kind"] == "task"
    assert parsed["field_id"] == "5"


def test_parse_multiple_blocks_ambiguous():
    parsed = tasks.parse_task_message(
        "Block 4 and Block 5B fix leak", REAL_FIELDS,
    )
    assert parsed["kind"] == "ambiguous"
    assert "one block" in parsed["reason"].lower() or "multiple" in parsed["reason"].lower()


def test_parse_unknown_block_ambiguous():
    parsed = tasks.parse_task_message(
        "Block 999 fix leak", REAL_FIELDS,
    )
    assert parsed["kind"] == "ambiguous"
    assert "999" in parsed["reason"]


def test_parse_block_only_lists_block():
    parsed = tasks.parse_task_message("Block 5B", REAL_FIELDS)
    assert parsed["kind"] == "list_for_block"
    assert parsed["field_id"] == "7"
    assert parsed["block_label"] == "5B"


def test_parse_empty_ambiguous_after_strip():
    parsed = tasks.parse_task_message("priority high", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_subcommand_unknown():
    for sub in ("open", "list", "all", "summary", "recent", "help",
                "done", "close", "complete"):
        parsed = tasks.parse_task_message(sub, REAL_FIELDS)
        assert parsed["kind"] == "unknown", sub


def test_parse_notes_tail():
    parsed = tasks.parse_task_message(
        "Block 4 fix leak priority high notes saw drip overnight",
        REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["priority"] == "high"
    assert "drip" in parsed["notes"].lower()
    assert "notes" not in parsed["title"].lower()


def test_parse_high_priority_compound_word():
    parsed = tasks.parse_task_message(
        "Block 4 fix leak high priority", REAL_FIELDS,
    )
    assert parsed["kind"] == "task"
    assert parsed["priority"] == "high"


# --- DB / CRUD tests ---

def test_init_tasks_db_creates_table(tmp_path):
    db = tmp_path / "farm.db"
    tasks.init_tasks_db(str(db))
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_creates_tasks_alongside_others(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {"harvest", "irrigation_events", "spray_events", "tasks"}.issubset(names)


def test_init_db_does_not_wipe_data(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([("2026-05-03", "5", "Almond", 12)], db_file=str(db))
    tasks.insert_task("survive me", db_file=str(db))
    # Re-init should be safe.
    bot.init_db(str(db))
    tasks.init_tasks_db(str(db))
    assert bot.total_bins(db_file=str(db)) == 12
    assert len(tasks.list_open(db_file=str(db))) == 1


def test_insert_general_task(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task("buy nitrogen", priority="high", db_file=str(db))
    assert tid is not None
    t = tasks.get_task(tid, db_file=str(db))
    assert t["title"] == "buy nitrogen"
    assert t["priority"] == "high"
    assert t["field_id"] is None
    assert t["block_label"] is None
    assert t["status"] == "open"
    assert t["completed_at"] is None


def test_insert_block_task(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task(
        "fix leak", field_id="5", block_label="4",
        field_name="Johnston Block 4", db_file=str(db),
    )
    t = tasks.get_task(tid, db_file=str(db))
    assert t["field_id"] == "5"
    assert t["block_label"] == "4"


def test_close_task_marks_done(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task("fix gate", db_file=str(db))
    status, closed = tasks.close_task(tid, db_file=str(db))
    assert status == "closed"
    assert closed["status"] == "done"
    assert closed["completed_at"] is not None
    # Re-fetch confirms persistence.
    t = tasks.get_task(tid, db_file=str(db))
    assert t["status"] == "done"
    assert t["completed_at"] is not None


def test_close_task_already_done(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task("fix gate", db_file=str(db))
    tasks.close_task(tid, db_file=str(db))
    status, t = tasks.close_task(tid, db_file=str(db))
    assert status == "already_done"
    assert t["status"] == "done"


def test_close_task_not_found(tmp_path):
    db = tmp_path / "farm.db"
    tasks.init_tasks_db(str(db))
    status, t = tasks.close_task(99999, db_file=str(db))
    assert status == "not_found"
    assert t is None


def test_close_task_non_numeric_id_not_found(tmp_path):
    db = tmp_path / "farm.db"
    tasks.init_tasks_db(str(db))
    status, _ = tasks.close_task("abc", db_file=str(db))
    assert status == "not_found"


def test_list_open_orders_by_priority_then_age(tmp_path):
    db = tmp_path / "farm.db"
    a = tasks.insert_task("low one", priority="low", db_file=str(db))
    b = tasks.insert_task("urgent one", priority="urgent", db_file=str(db))
    c = tasks.insert_task("normal one", priority="normal", db_file=str(db))
    d = tasks.insert_task("high one", priority="high", db_file=str(db))
    items = tasks.list_open(db_file=str(db))
    ids = [t["id"] for t in items]
    assert ids == [b, d, c, a]


def test_list_open_excludes_done(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task("done me", db_file=str(db))
    tasks.insert_task("still open", db_file=str(db))
    tasks.close_task(tid, db_file=str(db))
    open_items = tasks.list_open(db_file=str(db))
    assert len(open_items) == 1
    assert open_items[0]["title"] == "still open"


def test_list_for_field(tmp_path):
    db = tmp_path / "farm.db"
    tasks.insert_task(
        "fix leak", field_id="5", block_label="4", db_file=str(db),
    )
    tasks.insert_task(
        "general thing", db_file=str(db),
    )
    items = tasks.list_for_field("5", db_file=str(db))
    assert len(items) == 1
    assert items[0]["title"] == "fix leak"


def test_summary_counts_and_recent(tmp_path):
    db = tmp_path / "farm.db"
    tasks.insert_task("a", priority="urgent", db_file=str(db))
    tasks.insert_task("b", priority="high", db_file=str(db))
    tasks.insert_task("c", priority="normal", db_file=str(db))
    done_id = tasks.insert_task("d", priority="low", db_file=str(db))
    tasks.close_task(done_id, db_file=str(db))
    snap = tasks.summary(db_file=str(db))
    assert snap["open_total"] == 3
    assert snap["open_by_priority"]["urgent"] == 1
    assert snap["open_by_priority"]["high"] == 1
    assert snap["open_by_priority"]["normal"] == 1
    assert snap["open_by_priority"]["low"] == 0
    assert len(snap["recent_completed"]) == 1


def test_recent_completed_excludes_old(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task("old one", db_file=str(db))
    tasks.close_task(tid, db_file=str(db))
    # Backdate completed_at far in the past.
    past = (datetime.now(timezone.utc).astimezone() - timedelta(days=30)
            ).isoformat(timespec="seconds")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (past, tid))
    conn.commit()
    conn.close()
    recent = tasks.list_recent_completed(days=7, db_file=str(db))
    assert recent == []


# --- formatter tests ---

def test_format_open_list_empty():
    text = tasks.format_open_list([])
    assert "No open tasks" in text


def test_format_open_list_groups_priority(tmp_path):
    db = tmp_path / "farm.db"
    tasks.insert_task("low one", priority="low", db_file=str(db))
    tasks.insert_task("urgent one", priority="urgent", db_file=str(db))
    items = tasks.list_open(db_file=str(db))
    text = tasks.format_open_list(items)
    # Urgent should appear before low in the rendered output.
    assert text.index("urgent one") < text.index("low one")


def test_format_block_list_empty():
    text = tasks.format_block_list([], "5B")
    assert "5B" in text
    assert "No tasks" in text


def test_format_close_result_messages(tmp_path):
    db = tmp_path / "farm.db"
    tid = tasks.insert_task(
        "fix leak", field_id="5", block_label="4", db_file=str(db),
    )
    status, t = tasks.close_task(tid, db_file=str(db))
    msg = tasks.format_close_result(status, t)
    assert "Closed" in msg
    assert "Block 4" in msg
    # Already done.
    status2, t2 = tasks.close_task(tid, db_file=str(db))
    msg2 = tasks.format_close_result(status2, t2)
    assert "already" in msg2.lower()
    # Not found.
    msg3 = tasks.format_close_result("not_found", None)
    assert "couldn't find" in msg3.lower() or "not" in msg3.lower()


def test_format_logged_general_vs_block():
    general = {
        "id": 1, "title": "buy fuel", "priority": "normal",
        "field_id": None, "block_label": None, "field_name": None,
        "notes": "",
    }
    blocky = {
        "id": 2, "title": "fix leak", "priority": "high",
        "field_id": "5", "block_label": "4", "field_name": "Johnston Block 4",
        "notes": "",
    }
    g = tasks.format_logged(general)
    b = tasks.format_logged(blocky)
    assert "general" in g.lower()
    assert "Block 4" in b
    assert "high" in b.lower()


def test_format_summary_with_no_open_no_recent():
    snap = {
        "open_total": 0,
        "open_by_priority": {p: 0 for p in tasks.VALID_PRIORITIES},
        "open_items": [],
        "recent_completed": [],
        "recent_days": 7,
    }
    text = tasks.format_summary(snap)
    assert "Open: *0*" in text


# --- regression: existing commands unaffected ---

def test_harvest_parsing_still_works(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    parsed = bot.parse_message("Block 36A 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    bot.insert_harvest(parsed["entries"], db_file=str(db))
    assert bot.total_bins(db_file=str(db)) == 18


def test_irrigation_parsing_still_works():
    import irrigation
    parsed = irrigation.parse_irrigation_message("Block 4 12 hours", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["hours"] == 12.0


def test_spray_parsing_still_works():
    import spray
    parsed = spray.parse_spray_message(
        "Block 5B copper rei 12h phi 0d", REAL_FIELDS,
    )
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "7"


def test_today_summary_unaffected_by_tasks(tmp_path):
    import daily_summary
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([(datetime.now().strftime("%Y-%m-%d"), "5", "Peach", 4)],
                       db_file=str(db))
    tasks.insert_task("a task", priority="urgent", db_file=str(db))
    snap = daily_summary.collect_summary(db_file=str(db), fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 4
    assert "tasks" not in snap


def test_task_dispatch_done_via_bot_helper(tmp_path, monkeypatch):
    db = tmp_path / "farm.db"
    monkeypatch.setattr(bot, "DB_FILE", str(db))
    monkeypatch.setattr(bot, "load_fields", lambda: REAL_FIELDS)

    out = bot._task_dispatch("fix leak Block 4")
    assert "Logged task" in out
    # Pull the id back out.
    items = tasks.list_open(db_file=str(db))
    assert len(items) == 1
    tid = items[0]["id"]

    out_done = bot._task_dispatch(f"done {tid}")
    assert "Closed" in out_done

    out_unknown = bot._task_dispatch("done 9999")
    assert "couldn't find" in out_unknown.lower() or "not" in out_unknown.lower()


def test_task_dispatch_open_summary_via_bot_helper(tmp_path, monkeypatch):
    db = tmp_path / "farm.db"
    monkeypatch.setattr(bot, "DB_FILE", str(db))
    monkeypatch.setattr(bot, "load_fields", lambda: REAL_FIELDS)

    bot._task_dispatch("Block 4 fix leak priority urgent")
    bot._task_dispatch("buy nitrogen")
    open_text = bot._task_dispatch("open")
    assert "fix leak" in open_text
    assert "buy nitrogen" in open_text
    summary_text = bot._task_dispatch("summary")
    assert "Open: *2*" in summary_text
