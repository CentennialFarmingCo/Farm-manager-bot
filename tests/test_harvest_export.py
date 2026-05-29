"""Tests for harvest_export.build_snapshot.

We don't test the GitHub push end-to-end here (that requires a real PAT). The
push code paths are intentionally narrow and well-isolated so they can be
covered by manual smoke tests against a throwaway repo.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

# Make the project root importable when pytest runs from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harvest_export import build_snapshot  # noqa: E402


@pytest.fixture
def fixtures():
    """Create a temp SQLite db + tiny fields_map.json and yield their paths."""
    tmp = tempfile.mkdtemp(prefix="harvest-export-")
    db_path = os.path.join(tmp, "farm.db")
    fields_path = os.path.join(tmp, "fields_map.json")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE harvest (date TEXT, field_id TEXT, variety TEXT, bins INTEGER)"
    )
    rows = [
        ("2026-05-28", "5", "Parade", 12),
        ("2026-05-28", "5", "Parade", 6),     # same block, second drop-off
        ("2026-05-28", "10", "Kaweah", 20),
        ("2026-05-29", "5", "Parade", 18),
        ("2026-05-29", "1", "Kaweah", 33),    # block with no acres in map — still counts
    ]
    c.executemany("INSERT INTO harvest VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()

    with open(fields_path, "w") as f:
        json.dump({"fields": [
            {"id": "1", "name": "Johnston Block 1", "variety": "Kaweah Peach", "acres": 33},
            {"id": "5", "name": "Johnston Block 4", "variety": "Parade Peach", "acres": 13},
            {"id": "10", "name": "Johnston Block 36B", "variety": "Kaweah Peach", "acres": 18.5},
        ]}, f)

    yield db_path, fields_path


def test_build_snapshot_totals(fixtures):
    db, fmap = fixtures
    snap = build_snapshot(db_file=db, fields_file=fmap)
    assert snap["totals"]["bins"] == 89
    assert snap["totals"]["entries"] == 5
    assert snap["totals"]["blocks"] == 3
    assert snap["totals"]["first_date"] == "2026-05-28"
    assert snap["totals"]["last_date"] == "2026-05-29"


def test_per_day_aggregation(fixtures):
    db, fmap = fixtures
    snap = build_snapshot(db_file=db, fields_file=fmap)
    by_date = {row["date"]: row["bins"] for row in snap["per_day"]}
    assert by_date == {"2026-05-28": 38, "2026-05-29": 51}
    # Dates must be sorted ascending so the chart renders left-to-right.
    assert [r["date"] for r in snap["per_day"]] == sorted(by_date.keys())


def test_per_block_rollup_and_bins_per_acre(fixtures):
    db, fmap = fixtures
    snap = build_snapshot(db_file=db, fields_file=fmap)
    by_block = {row["field_id"]: row for row in snap["per_block"]}

    # Block 5 (Johnston Block 4, 13 acres) — 12 + 6 + 18 = 36 bins
    assert by_block["5"]["bins"] == 36
    assert by_block["5"]["acres"] == 13.0
    assert by_block["5"]["bins_per_acre"] == round(36 / 13, 2)
    assert by_block["5"]["block"] == "Johnston Block 4"

    # Block 1 — 33 bins on 33 acres → exactly 1.0 bins/acre
    assert by_block["1"]["bins_per_acre"] == 1.0

    # per_block must be sorted by bins desc so the leaderboard reads top-down.
    bins_in_order = [r["bins"] for r in snap["per_block"]]
    assert bins_in_order == sorted(bins_in_order, reverse=True)


def test_entries_preserve_order_and_metadata(fixtures):
    db, fmap = fixtures
    snap = build_snapshot(db_file=db, fields_file=fmap)
    # We inserted 5 rows ordered by date asc; the loader uses
    # ORDER BY date ASC, rowid ASC so this order is stable.
    assert [e["bins"] for e in snap["entries"]] == [12, 6, 20, 18, 33]
    assert snap["entries"][0]["block"] == "Johnston Block 4"
    assert snap["entries"][0]["variety"] == "Parade"


def test_unknown_field_id_does_not_crash(fixtures):
    db, fmap = fixtures
    # Insert an entry whose field_id isn't in the fields_map.
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO harvest VALUES (?,?,?,?)", ("2026-05-29", "999", "Mystery", 4))
    conn.commit()
    conn.close()

    snap = build_snapshot(db_file=db, fields_file=fmap)
    # The unknown block still appears, just with empty name/acres and no bins/acre.
    unknown = next(r for r in snap["per_block"] if r["field_id"] == "999")
    assert unknown["bins"] == 4
    assert unknown["block"] == ""
    assert unknown["acres"] is None
    assert unknown["bins_per_acre"] is None


def test_empty_db_produces_valid_snapshot(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE harvest (date TEXT, field_id TEXT, variety TEXT, bins INTEGER)"
    )
    conn.commit()
    conn.close()

    fmap = tmp_path / "fields_map.json"
    fmap.write_text(json.dumps({"fields": []}))

    snap = build_snapshot(db_file=str(db), fields_file=str(fmap))
    assert snap["totals"] == {
        "bins": 0, "entries": 0, "blocks": 0,
        "first_date": None, "last_date": None,
    }
    assert snap["entries"] == []
    assert snap["per_day"] == []
    assert snap["per_block"] == []
    # generated_at must always be a valid ISO-8601 UTC string.
    assert snap["generated_at"].endswith("Z")
