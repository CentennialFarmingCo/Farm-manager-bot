"""Tests for the /today daily farm summary."""
import asyncio
import importlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import irrigation  # noqa: E402
import daily_summary  # noqa: E402


REAL_FIELDS = [
    {"id": "5", "name": "Johnston Block 4", "block_label": "4",
     "variety": "Parade Freestone Peach", "acres": 13},
    {"id": "7", "name": "Johnston Block 5B", "block_label": "5B",
     "variety": "Angelus Freestone Peach", "acres": 15},
    {"id": "9", "name": "Johnston Block 36A", "block_label": "36A",
     "variety": "Carnival Freestone Peach", "acres": 18.5},
]


def _fresh_db(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    return str(db)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


# --- collect_summary ---

def test_collect_summary_empty(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 0
    assert snap["irrigation_total_hours"] == 0
    assert snap["open_irrigation_sessions"] == []
    assert snap["labor"]["bins"] == 0
    assert snap["labor"]["worker_pay"] == 0


def test_collect_summary_harvest_only(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    bot.insert_harvest(
        [(_today(), "5", "Parade Freestone Peach", 18),
         (_today(), "9", "Carnival Freestone Peach", 7)],
        db_file=db,
    )
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 25
    labels = sorted(item["block_label"] for item in snap["harvest"])
    assert labels == ["36A", "4"]
    assert snap["labor"]["worker_pay"] == 25 * 30
    assert snap["labor"]["total_cost"] == round(25 * 30 * 1.35, 2)
    assert snap["irrigation_total_hours"] == 0


def test_collect_summary_irrigation_only(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    irrigation.insert_duration_event(
        "5", "4", "Johnston Block 4", 6.0, db_file=db,
    )
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 0
    assert snap["irrigation_total_hours"] == 6.0
    assert snap["open_irrigation_sessions"] == []


def test_collect_summary_mixed_harvest_and_irrigation(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    bot.insert_harvest([(_today(), "5", "Peach", 12)], db_file=db)
    irrigation.insert_duration_event(
        "9", "36A", "Johnston Block 36A", 4.5, db_file=db,
    )
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 12
    assert snap["irrigation_total_hours"] == 4.5


def test_collect_summary_includes_open_sessions(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert len(snap["open_irrigation_sessions"]) == 1
    assert snap["open_irrigation_sessions"][0]["block_label"] == "5B"
    # An open start with no stop must NOT count toward today's hours.
    assert snap["irrigation_total_hours"] == 0


def test_collect_summary_excludes_yesterday(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # Yesterday's harvest must not appear in today's summary.
    bot.insert_harvest([(yesterday, "5", "Peach", 100)], db_file=db)
    irrigation.insert_duration_event("5", "4", "Johnston Block 4", 9.0, db_file=db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE irrigation_events SET date = ? WHERE event_type='duration'",
        (yesterday,),
    )
    conn.commit()
    conn.close()
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 0
    assert snap["irrigation_total_hours"] == 0


def test_collect_summary_resolves_block_label_from_field_id(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    # field_id=5 corresponds to human "Block 4". Summary must surface "4".
    bot.insert_harvest([(_today(), "5", "Peach", 3)], db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    assert snap["harvest"][0]["block_label"] == "4"


# --- format_summary ---

def test_format_empty_summary_has_examples(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    text = daily_summary.format_summary(snap)
    assert "Daily farm summary" in text
    assert "Block 4 18 bins" in text  # harvest example
    assert "/irrigation" in text


def test_format_harvest_only_summary(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    bot.insert_harvest([(_today(), "5", "Peach", 18)], db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    text = daily_summary.format_summary(snap)
    assert "Block 4" in text
    assert "18 bins" in text
    assert "Total: 18 bins" in text
    assert "no completed hours" in text.lower()
    # Labor section shows for non-zero bins
    assert "Worker pay" in text
    assert "$540" in text  # 18 * 30


def test_format_irrigation_only_summary(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    irrigation.insert_duration_event("5", "4", "Johnston Block 4", 6.0, db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    text = daily_summary.format_summary(snap)
    assert "none logged today" in text
    assert "Block 4: 6.0h" in text
    assert "Total: 6.0h" in text
    assert "no bins logged" in text.lower()


def test_format_mixed_summary(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    bot.insert_harvest([(_today(), "5", "Peach", 10)], db_file=db)
    irrigation.insert_duration_event("9", "36A", "Johnston Block 36A", 4.0, db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    text = daily_summary.format_summary(snap)
    assert "Block 4" in text and "10 bins" in text
    assert "Block 36A" in text and "4.0h" in text
    assert "/dashboard" in text


def test_format_open_session_listed(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setattr(bot, "DB_FILE", db)
    irrigation.insert_start_event("7", "5B", "Johnston Block 5B", db_file=db)
    snap = daily_summary.collect_summary(db_file=db, fields=REAL_FIELDS)
    text = daily_summary.format_summary(snap)
    assert "Currently irrigating" in text
    assert "Block 5B" in text


# --- handler / command registration ---

def _fake_update():
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = "/today"
    return update


def _run(coro):
    return asyncio.run(coro)


def test_today_handler_replies_with_markdown(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setenv("FARM_DB_FILE", db)
    importlib.reload(bot)
    importlib.reload(daily_summary)
    update = _fake_update()
    _run(bot.today_command(update, MagicMock()))
    update.message.reply_text.assert_called_once()
    args, kwargs = update.message.reply_text.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "Daily farm summary" in text
    assert kwargs.get("parse_mode") == "Markdown"


def test_today_handler_with_real_data(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    monkeypatch.setenv("FARM_DB_FILE", db)
    importlib.reload(bot)
    importlib.reload(daily_summary)
    bot.insert_harvest([(_today(), "5", "Peach", 7)], db_file=db)
    update = _fake_update()
    _run(bot.today_command(update, MagicMock()))
    args, _ = update.message.reply_text.call_args
    assert "7 bins" in args[0]


def test_today_command_handler_registered():
    """Ensure /today is wired into the Application's command handlers."""
    import inspect
    src = inspect.getsource(bot.main)
    assert 'CommandHandler("today"' in src
    assert "today_command" in src
