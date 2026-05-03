import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402
import spray  # noqa: E402


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

def test_parse_basic_with_rei_phi():
    parsed = spray.parse_spray_message(
        "Block 5B copper 80 gal rei 12h phi 0d", REAL_FIELDS,
    )
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "7"
    assert parsed["block_label"] == "5B"
    assert parsed["product"].lower() == "copper"
    assert "80 gal" in parsed["details"].lower()
    assert parsed["rei_hours"] == 12.0
    assert parsed["phi_days"] == 0.0


def test_parse_with_notes_tail():
    parsed = spray.parse_spray_message(
        "Block 36A sulfur rei 24h phi 1d notes mildew pressure",
        REAL_FIELDS,
    )
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "9"
    assert parsed["product"].lower() == "sulfur"
    assert parsed["rei_hours"] == 24.0
    assert parsed["phi_days"] == 1.0
    assert "mildew" in parsed["notes"].lower()


def test_parse_omitted_rei_phi():
    parsed = spray.parse_spray_message("Block 4 nutrient foliar", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "5"
    assert parsed["rei_hours"] is None
    assert parsed["phi_days"] is None


def test_parse_rei_in_days_converts_to_hours():
    parsed = spray.parse_spray_message("Block 4 product rei 1d", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["rei_hours"] == 24.0


def test_parse_phi_in_hours_converts_to_days():
    parsed = spray.parse_spray_message("Block 4 product phi 48h", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["phi_days"] == 2.0


def test_parse_bare_rei_number_assumed_hours():
    parsed = spray.parse_spray_message("Block 4 prod rei 12 phi 7", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["rei_hours"] == 12.0
    assert parsed["phi_days"] == 7.0


def test_parse_suffix_label_36A():
    parsed = spray.parse_spray_message("Block 36A sulfur", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "9"


def test_parse_composite_block_56_58():
    parsed = spray.parse_spray_message("Block 56/58 oil rei 4h", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "8"
    assert parsed["rei_hours"] == 4.0


def test_parse_block_4_uses_block_label_not_id():
    # id=5 maps to "Block 4". Spray must respect block_label semantics.
    parsed = spray.parse_spray_message("Block 4 sulfur rei 12h", REAL_FIELDS)
    assert parsed["kind"] == "spray"
    assert parsed["field_id"] == "5"


def test_parse_unknown_block_ambiguous():
    parsed = spray.parse_spray_message("Block 999 copper", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"
    assert "999" in parsed["reason"]


def test_parse_no_block_ambiguous():
    parsed = spray.parse_spray_message("copper rei 12h", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_multiple_blocks_ambiguous():
    parsed = spray.parse_spray_message(
        "Block 4 and Block 5B copper", REAL_FIELDS,
    )
    assert parsed["kind"] == "ambiguous"


def test_parse_no_product_ambiguous():
    parsed = spray.parse_spray_message("Block 4 rei 12h phi 1d", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_unknown_text():
    parsed = spray.parse_spray_message("hello", REAL_FIELDS)
    # No block ref → ambiguous, not unknown.
    assert parsed["kind"] == "ambiguous"


def test_parse_subcommand_treated_as_unknown():
    # Command dispatcher handles "today"/"open"/"summary"; the parser must
    # not try to interpret these as spray entries.
    for sub in ("today", "open", "restrictions", "summary"):
        parsed = spray.parse_spray_message(sub, REAL_FIELDS)
        assert parsed["kind"] == "unknown", sub


def test_parse_excessive_rei_ambiguous():
    parsed = spray.parse_spray_message("Block 4 product rei 9999h", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


def test_parse_negative_phi_via_excessive_phi_ambiguous():
    parsed = spray.parse_spray_message("Block 4 product phi 9999d", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"


# --- DB tests ---

def test_init_spray_db_creates_table(tmp_path):
    db = tmp_path / "farm.db"
    spray.init_spray_db(str(db))
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='spray_events'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_does_not_wipe_harvest_or_irrigation(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([("2026-05-03", "5", "Almond", 12)], db_file=str(db))
    # Re-init should be safe.
    bot.init_db(str(db))
    spray.init_spray_db(str(db))
    assert bot.total_bins(db_file=str(db)) == 12


def test_init_db_creates_spray_alongside_others(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {"harvest", "irrigation_events", "spray_events"}.issubset(names)


def test_insert_with_rei_phi_round_trip(tmp_path):
    db = tmp_path / "farm.db"
    row_id, rei_end, phi_end = spray.insert_spray_event(
        "7", "5B", "Johnston Block 5B", "copper", "80 gal",
        rei_hours=12.0, phi_days=0.0, db_file=str(db),
    )
    assert row_id is not None
    assert rei_end is not None
    assert phi_end is not None
    rows = spray.list_today(db_file=str(db))
    assert len(rows) == 1
    assert rows[0]["product"] == "copper"
    assert rows[0]["rei_hours"] == 12.0
    assert rows[0]["phi_days"] == 0.0
    assert rows[0]["rei_end_at"] is not None
    assert rows[0]["phi_end_at"] is not None


def test_insert_without_rei_phi_stores_nulls(tmp_path):
    db = tmp_path / "farm.db"
    row_id, rei_end, phi_end = spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "nutrient", "", db_file=str(db),
    )
    assert row_id is not None
    assert rei_end is None
    assert phi_end is None
    rows = spray.list_today(db_file=str(db))
    assert rows[0]["rei_hours"] is None
    assert rows[0]["phi_days"] is None
    assert rows[0]["rei_end_at"] is None
    assert rows[0]["phi_end_at"] is None


def test_list_active_restrictions_includes_active(tmp_path):
    db = tmp_path / "farm.db"
    spray.insert_spray_event(
        "7", "5B", "Johnston Block 5B", "copper", "",
        rei_hours=24.0, phi_days=1.0, db_file=str(db),
    )
    active = spray.list_active_restrictions(db_file=str(db))
    assert len(active) == 1
    assert active[0]["block_label"] == "5B"


def test_list_active_restrictions_excludes_expired(tmp_path):
    db = tmp_path / "farm.db"
    spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "old", "",
        rei_hours=12.0, phi_days=1.0, db_file=str(db),
    )
    # Backdate end timestamps to the past.
    past = (datetime.now(timezone.utc).astimezone() - timedelta(days=2)
            ).isoformat(timespec="seconds")
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE spray_events SET rei_end_at = ?, phi_end_at = ?",
        (past, past),
    )
    conn.commit()
    conn.close()
    active = spray.list_active_restrictions(db_file=str(db))
    assert active == []


def test_list_active_restrictions_skips_events_without_windows(tmp_path):
    db = tmp_path / "farm.db"
    spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "nutrient", "", db_file=str(db),
    )
    active = spray.list_active_restrictions(db_file=str(db))
    assert active == []


def test_list_recent_last_7_days(tmp_path):
    db = tmp_path / "farm.db"
    spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "copper", "", rei_hours=12.0,
        db_file=str(db),
    )
    spray.insert_spray_event(
        "9", "36A", "Johnston Block 36A", "sulfur", "", rei_hours=24.0,
        db_file=str(db),
    )
    rows = spray.list_recent(days=7, db_file=str(db))
    products = sorted(r["product"] for r in rows)
    assert products == ["copper", "sulfur"]


def test_list_recent_excludes_older_than_window(tmp_path):
    db = tmp_path / "farm.db"
    spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "old", "", db_file=str(db),
    )
    conn = sqlite3.connect(db)
    conn.execute("UPDATE spray_events SET date = date('now', '-30 days')")
    conn.commit()
    conn.close()
    assert spray.list_recent(days=7, db_file=str(db)) == []


# --- formatter tests ---

def test_format_logged_with_rei_phi():
    event = {
        "block_label": "5B", "field_name": "Johnston Block 5B", "field_id": "7",
        "product": "copper", "details": "80 gal", "notes": "",
        "rei_hours": 12.0, "phi_days": 0.0,
    }
    text = spray.format_logged(event, "2026-05-04T08:00:00-07:00", "2026-05-03T22:00:00-07:00")
    assert "Block 5B" in text
    assert "copper" in text
    assert "REI" in text and "PHI" in text


def test_format_logged_without_rei_phi_says_label_governs():
    event = {
        "block_label": "4", "field_name": "Johnston Block 4", "field_id": "5",
        "product": "nutrient", "details": "", "notes": "",
        "rei_hours": None, "phi_days": None,
    }
    text = spray.format_logged(event, None, None)
    assert "label" in text.lower() or "regulation" in text.lower()


def test_format_active_restrictions_empty():
    text = spray.format_active_restrictions([])
    assert "No active" in text
    assert "label" in text.lower()


def test_format_today_empty():
    text = spray.format_today([])
    assert "none" in text.lower()


def test_format_summary_empty():
    text = spray.format_summary([], days=7)
    assert "none" in text.lower()


# --- regression: pre-existing bot/irrigation behavior unchanged ---

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


def test_today_summary_unaffected_by_spray(tmp_path):
    import daily_summary
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([(datetime.now().strftime("%Y-%m-%d"), "5", "Peach", 4)],
                       db_file=str(db))
    spray.insert_spray_event(
        "5", "4", "Johnston Block 4", "copper", "", rei_hours=12.0,
        db_file=str(db),
    )
    snap = daily_summary.collect_summary(db_file=str(db), fields=REAL_FIELDS)
    assert snap["harvest_total_bins"] == 4
    # /today should not reference spray fields (kept unaffected for now).
    assert "spray" not in snap
