import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402


FIELDS = [
    {"id": "1", "name": "Block 1", "variety": "Kaweah Freestone Peach", "acres": 33},
    {"id": "2", "name": "Block 2", "variety": "Zee Lady Freestone Peach", "acres": 18.5},
    {"id": "5", "name": "Block 5", "variety": "Nonpareil Almond", "acres": 20},
    {"id": "18", "name": "Block 18", "variety": "Carmel Almond", "acres": 12.3},
    {"id": "66", "name": "Block 66", "variety": "Independence Almond", "acres": 25},
]


def test_module_import_does_not_create_db(tmp_path, monkeypatch):
    db = tmp_path / "should_not_exist.db"
    monkeypatch.setenv("FARM_DB_FILE", str(db))
    # Re-import to confirm import alone never touches the DB file.
    import importlib
    importlib.reload(bot)
    assert not db.exists()


def test_init_db_creates_table(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    assert db.exists()
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='harvest'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_get_total_acres():
    assert bot.get_total_acres(FIELDS) == round(33 + 18.5 + 20 + 12.3 + 25, 1)


def test_get_acres_filters_by_blocks_and_variety():
    # Only peach blocks among 1, 2, 5
    acres = bot.get_acres_by_blocks_and_variety(
        block_list=[1, 2, 5], variety_filter="peach", fields=FIELDS
    )
    assert acres == round(33 + 18.5, 1)

    # Only almond among same blocks
    acres = bot.get_acres_by_blocks_and_variety(
        block_list=[1, 2, 5], variety_filter="almond", fields=FIELDS
    )
    assert acres == 20.0


def test_parse_acreage_natural_language():
    parsed = bot.parse_message(
        "tell me how many acres of peaches and almonds are in blocks 66,18,2",
        FIELDS,
    )
    assert parsed["kind"] == "acreage"
    assert parsed["blocks"] == [66, 18, 2]
    assert parsed["variety"] == "peach"  # peach mentioned first


def test_parse_harvest_log_does_not_treat_bins_as_block_id():
    # "Field 5 18 bins" must log to field 5 only — not also to block 18.
    parsed = bot.parse_message("Field 5 18 bins", FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["5"]
    assert parsed["entries"][0][3] == 18


def test_parse_harvest_log_multiple_blocks():
    parsed = bot.parse_message("Block 1 and block 2 10 bins", FIELDS)
    assert parsed["kind"] == "harvest"
    assert sorted(e[1] for e in parsed["entries"]) == ["1", "2"]
    for e in parsed["entries"]:
        assert e[3] == 10


def test_parse_unknown_message():
    parsed = bot.parse_message("hello there", FIELDS)
    assert parsed["kind"] == "unknown"


def test_parse_block_id_word_boundary():
    # ID "1" must NOT match inside "block 18" — that was the original bug.
    parsed = bot.parse_message("block 18 5 bins", FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert "1" not in fids
    assert "18" in fids


def test_insert_and_total_bins(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest(
        [("2026-05-03", "5", "Almond", 18), ("2026-05-03", "1", "Peach", 4)],
        db_file=str(db),
    )
    assert bot.total_bins(db_file=str(db)) == 22


def test_insert_harvest_empty_is_noop(tmp_path):
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    bot.insert_harvest([], db_file=str(db))
    assert bot.total_bins(db_file=str(db)) == 0


def test_load_fields_uses_env(tmp_path, monkeypatch):
    f = tmp_path / "fields.json"
    f.write_text('{"fields": [{"id": "9", "variety": "Peach", "acres": 1}]}')
    monkeypatch.setenv("FARM_FIELDS_FILE", str(f))
    import importlib
    importlib.reload(bot)
    fields = bot.load_fields()
    assert len(fields) == 1
    assert fields[0]["id"] == "9"
