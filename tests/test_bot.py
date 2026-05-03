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

# Realistic fields where internal id != human block label, mirroring fields_map.json.
REAL_FIELDS = [
    {"id": "5", "name": "Johnston Block 4", "block_label": "4",
     "variety": "Parade Freestone Peach", "acres": 13},
    {"id": "9", "name": "Johnston Block 36A", "block_label": "36A",
     "variety": "Carnival Freestone Peach", "acres": 18.5},
    {"id": "10", "name": "Johnston Block 36B", "block_label": "36B",
     "variety": "Kaweah Freestone Peach", "acres": 18.5},
    {"id": "36", "name": "Fagundes Block 66", "block_label": "66",
     "variety": "Independence Almond", "acres": 27},
    {"id": "18", "name": "Blue Lupin Block 10", "block_label": "10",
     "variety": "Fairtime Freestone Peach", "acres": 30},
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
    assert parsed["blocks"] == ["66", "18", "2"]
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
    # ID "1" must NOT match inside "block 18" — the original substring bug.
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


# --- New tests for block-label matching (id vs visible block label divergence) ---

def test_harvest_block_4_matches_human_label_not_id():
    # In real data, "Block 4" is internal id=5. The foreman texts the human
    # label, which must win — log to id 5, NOT to the field whose id is 4.
    parsed = bot.parse_message("Block 4 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["5"]
    assert parsed["entries"][0][3] == 18


def test_harvest_suffix_label_36A():
    parsed = bot.parse_message("Block 36A 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["9"]
    assert parsed["entries"][0][3] == 18


def test_harvest_suffix_label_case_insensitive():
    parsed = bot.parse_message("block 36a 12 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["9"]


def test_harvest_36A_does_not_match_36B():
    # 36A and 36B share digits — word-boundary-ish matching must not bleed.
    parsed = bot.parse_message("Block 36A 5 bins", REAL_FIELDS)
    fids = [e[1] for e in parsed["entries"]]
    assert "10" not in fids  # 36B's id
    assert fids == ["9"]


def test_harvest_block_66_label_wins_over_unrelated_id():
    # Human "Block 66" → id=36 (Fagundes). Must NOT log to whichever field has id=66.
    parsed = bot.parse_message("Block 66 25 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["36"]


def test_harvest_ambiguous_no_block_ref():
    # "field 18 bins" — bare 18 must NOT be parsed as field=18.
    parsed = bot.parse_message("field 18 bins", FIELDS)
    assert parsed["kind"] == "ambiguous"
    assert "block" in parsed["reason"].lower() or "field" in parsed["reason"].lower()


def test_harvest_ambiguous_multiple_bin_counts():
    parsed = bot.parse_message("Field 1 5 bins and field 2 10 bins", FIELDS)
    assert parsed["kind"] == "ambiguous"
    assert "bin" in parsed["reason"].lower()


def test_harvest_unrecognized_block_label():
    parsed = bot.parse_message("Block 999 5 bins", FIELDS)
    assert parsed["kind"] == "ambiguous"
    assert "999" in parsed["reason"]


def test_harvest_substring_false_positive():
    # "Block 1" should not also log to whichever field has id=18 just because
    # "1" appears as a substring of "18". Word boundaries handle this.
    parsed = bot.parse_message("Block 1 7 bins", FIELDS)
    assert parsed["kind"] == "harvest"
    fids = [e[1] for e in parsed["entries"]]
    assert fids == ["1"]


def test_harvest_shared_bin_count_across_blocks():
    # One bin number, multiple labels → both blocks share the count.
    parsed = bot.parse_message("Block 1 and block 2 10 bins", FIELDS)
    assert parsed["kind"] == "harvest"
    assert sorted(e[1] for e in parsed["entries"]) == ["1", "2"]
    for e in parsed["entries"]:
        assert e[3] == 10


def test_acreage_query_still_works():
    # Pre-existing payroll/non-harvest acreage path must remain intact.
    parsed = bot.parse_message("how many acres in blocks 66,18,2", FIELDS)
    assert parsed["kind"] == "acreage"
    assert parsed["blocks"] == ["66", "18", "2"]


def test_acreage_matches_by_block_label_in_real_data():
    # User says "Block 4" — acreage should resolve to the field whose human
    # label is "4" (internal id=5), so 13 acres, not whatever id=4 has.
    acres = bot.get_acres_by_blocks_and_variety(
        block_list=["4"], fields=REAL_FIELDS
    )
    assert acres == 13.0


def test_load_fields_derives_block_label_from_name(tmp_path, monkeypatch):
    f = tmp_path / "fields.json"
    f.write_text(
        '{"fields": [{"id": "5", "name": "Johnston Block 4", '
        '"variety": "Peach", "acres": 13}]}'
    )
    monkeypatch.setenv("FARM_FIELDS_FILE", str(f))
    import importlib
    importlib.reload(bot)
    fields = bot.load_fields()
    assert fields[0]["block_label"] == "4"


def test_harvest_no_id_fallback_when_block_label_present():
    # Real data: id=5 corresponds to "Johnston Block 4". A foreman texting
    # "Block 5" must NOT silently log to id=5 — there is no Block 5 in this
    # subset (only 5A/5B exist in production), so the message is ambiguous.
    parsed = bot.parse_message("Block 5 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "ambiguous"
    # And critically, the matcher does not return id=5.
    assert parsed.get("entries") is None or all(
        e[1] != "5" for e in parsed.get("entries", [])
    )


def test_payroll_total_bins_logging_unchanged(tmp_path):
    # Confirms harvest write path → payroll readback path is untouched.
    db = tmp_path / "farm.db"
    bot.init_db(str(db))
    parsed = bot.parse_message("Block 36A 18 bins", REAL_FIELDS)
    assert parsed["kind"] == "harvest"
    bot.insert_harvest(parsed["entries"], db_file=str(db))
    assert bot.total_bins(db_file=str(db)) == 18
