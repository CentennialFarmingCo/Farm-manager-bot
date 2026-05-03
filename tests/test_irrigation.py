import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import irrigation  # noqa: E402


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


def _use_db(monkeypatch, tmp_path):
    db = tmp_path / "farm.db"
    monkeypatch.setattr(bot, "DB_FILE", str(db))
    irrigation.init_irrigation_db(str(db))
    return str(db)


# --- parser tests ---

def test_parse_duration_basic():
    parsed = irrigation.parse_irrigation_message("Block 4 12 hours", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["field_id"] == "5"
    assert parsed["block_label"] == "4"
    assert parsed["hours"] == 12.0


def test_parse_duration_hrs_abbrev():
    parsed = irrigation.parse_irrigation_message("Block 36A 8h", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["field_id"] == "9"
    assert parsed["hours"] == 8.0


def test_parse_duration_decimal():
    parsed = irrigation.parse_irrigation_message("Block 4 1.5 hours", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["hours"] == 1.5


def test_parse_started():
    parsed = irrigation.parse_irrigation_message("Block 5B started", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_start"
    assert parsed["field_id"] == "7"
    assert parsed["block_label"] == "5B"


def test_parse_stopped():
    parsed = irrigation.parse_irrigation_message("Block 5B stopped", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_stop"
    assert parsed["field_id"] == "7"


def test_parse_no_block_ambiguous():
    parsed = irrigation.parse_irrigation_message("12 hours", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_unknown_block_ambiguous():
    parsed = irrigation.parse_irrigation_message("Block 999 5 hours", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"
    assert "999" in parsed["reason"]


def test_parse_suffix_label_36A_does_not_match_36B():
    parsed = irrigation.parse_irrigation_message("Block 36A 5 hours", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["field_id"] == "9"


def test_parse_composite_block_56_58():
    parsed = irrigation.parse_irrigation_message("Block 56/58 6 hours", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["field_id"] == "8"


def test_parse_block_4_uses_block_label_not_id():
    # Real data: id=5 corresponds to "Block 4". Foreman texting "Block 4"
    # must log to id=5, not whatever has id=4.
    parsed = irrigation.parse_irrigation_message("Block 4 1 hour", REAL_FIELDS)
    assert parsed["kind"] == "irrigation_duration"
    assert parsed["field_id"] == "5"


def test_parse_unknown_text():
    parsed = irrigation.parse_irrigation_message("hello there", REAL_FIELDS)
    assert parsed["kind"] == "unknown"


def test_parse_multiple_blocks_ambiguous():
    parsed = irrigation.parse_irrigation_message(
        "Block 4 and Block 5B 6 hours", REAL_FIELDS,
    )
    assert parsed["kind"] == "ambiguous"


def test_parse_mix_duration_and_start_ambiguous():
    parsed = irrigation.parse_irrigation_message(
        "Block 4 started 2 hours", REAL_FIELDS,
    )
    assert parsed["kind"] == "ambiguous"


def test_parse_zero_hours_ambiguous():
    parsed = irrigation.parse_irrigation_message("Block 4 0 hours", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_huge_hours_ambiguous():
    parsed = irrigation.parse_irrigation_message("Block 4 9999 hours", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


# --- DB tests ---

def test_init_irrigation_db_creates_table(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.init_irrigation_db(str(db))
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='irrigation_events'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_does_not_wipe_existing_harvest_data(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([("2026-05-03", "5", "Almond", 18)], db_file=str(db))
    # Init a second time with irrigation table; harvest data must remain.
    bot.init_db(str(db))
    irrigation.init_irrigation_db(str(db))
    assert bot.total_bins(db_file=str(db)) == 18


def test_init_db_creates_irrigation_alongside_harvest(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "harvest" in names
    assert "irrigation_events" in names


def test_insert_duration_event_round_trip(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_duration_event(
        "5", "4", "Johnston Block 4", 12.0, db_file=str(db),
    )
    rows = irrigation.summarize_today(db_file=str(db))
    assert len(rows) == 1
    assert rows[0]["block_label"] == "4"
    assert rows[0]["hours"] == 12.0


def test_start_then_stop_computes_duration(tmp_path, monkeypatch):
    db = tmp_path / "farm.db"
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    # Backdate the start by 2 hours so stop_event computes a useful duration.
    backdated = (
        datetime.now(timezone.utc).astimezone() - timedelta(hours=2)
    ).isoformat(timespec="seconds")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE irrigation_events SET created_at = ? WHERE event_type='start'",
        (backdated,),
    )
    conn.commit()
    conn.close()
    stop_id, hours, _ = irrigation.insert_stop_event(
        "7", "5B", "Johnston Block 5B", db_file=str(db),
    )
    assert stop_id is not None
    # ~2h, allow generous tolerance for clock jitter
    assert 1.9 <= hours <= 2.1


def test_stop_without_start_returns_none(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.init_irrigation_db(str(db))
    stop_id, hours, start_iso = irrigation.insert_stop_event(
        "7", "5B", "Johnston Block 5B", db_file=str(db),
    )
    assert stop_id is None and hours is None and start_iso is None


def test_double_start_returns_existing(tmp_path):
    db = tmp_path / "farm.db"
    row1, _ = irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    row2, already = irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    assert row2 is None
    assert already == row1


def test_list_open_sessions(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    irrigation.insert_start_event("9", "36A", "Johnston Block 36A", db_file=str(db))
    sessions = irrigation.list_open_sessions(db_file=str(db))
    labels = sorted(s["block_label"] for s in sessions)
    assert labels == ["36A", "5B"]


def test_list_open_sessions_excludes_closed(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    irrigation.insert_stop_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    sessions = irrigation.list_open_sessions(db_file=str(db))
    assert sessions == []


def test_summarize_today_includes_duration_and_stop_only(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_duration_event("5", "4", "Johnston Block 4", 3.0, db_file=str(db))
    # An open start (no stop) should not contribute to today's hours.
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=str(db))
    rows = irrigation.summarize_today(db_file=str(db))
    assert len(rows) == 1
    assert rows[0]["block_label"] == "4"
    assert rows[0]["hours"] == 3.0


def test_summarize_recent_last_7_days(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_duration_event("5", "4", "Johnston Block 4", 5.0, db_file=str(db))
    irrigation.insert_duration_event("9", "36A", "Johnston Block 36A", 7.0, db_file=str(db))
    rows = irrigation.summarize_recent(days=7, db_file=str(db))
    by_label = {r["block_label"]: r["hours"] for r in rows}
    assert by_label == {"4": 5.0, "36A": 7.0}


def test_summarize_recent_excludes_older_than_window(tmp_path):
    db = tmp_path / "farm.db"
    irrigation.insert_duration_event("5", "4", "Johnston Block 4", 9.0, db_file=str(db))
    # Backdate to 30 days ago.
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE irrigation_events SET date = date('now', '-30 days')"
    )
    conn.commit()
    conn.close()
    rows = irrigation.summarize_recent(days=7, db_file=str(db))
    assert rows == []


def test_format_open_sessions_empty():
    txt = irrigation.format_open_sessions([])
    assert "No blocks" in txt


def test_format_summary_includes_total():
    rows = [
        {"field_id": "5", "block_label": "4", "field_name": "Johnston Block 4", "hours": 3.0},
        {"field_id": "9", "block_label": "36A", "field_name": "Johnston Block 36A", "hours": 4.5},
    ]
    text = irrigation.format_summary(rows, "💧 today")
    assert "Block 4" in text and "Block 36A" in text
    assert "7.5" in text


# --- Regression: harvest behavior unchanged ---

def test_harvest_parsing_unchanged_after_irrigation_module(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    parsed = bot.parse_message("Block 36A 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    bot.insert_harvest(parsed["entries"], db_file=str(db))
    assert bot.total_bins(db_file=str(db)) == 18


def test_harvest_unknown_message_still_unknown():
    parsed = bot.parse_message("Block 4 12 hours", [
        {"id": "5", "name": "Johnston Block 4", "block_label": "4",
         "variety": "Peach", "acres": 13}
    ])
    # Harvest parser should treat irrigation phrasing as unknown/acreage —
    # it must not log bins on a "hours" message.
    assert parsed["kind"] != "harvest"
