"""Tests for the on-demand seasonal phenology utility.

The dashboard JSON HTTP call is mocked at the `urlopen` boundary so tests
stay hermetic and never touch the network. Each scenario builds a payload
that mimics the real `/phenology-summary.json` response shape and asserts
on the lookup, rendering, and end-to-end behavior.
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import seasonal  # noqa: E402


# --- helpers ---------------------------------------------------------------


def make_payload(**overrides):
    """Build a payload mimicking the live phenology-summary.json shape."""
    payload = {
        "metadata": {
            "generatedAt": "2026-05-04T00:14:42.921Z",
            "todayLocal": "2026-05-03",
            "available": True,
            "source": {"weather": "CIMIS"},
            "station": {
                "id": "206",
                "name": "Denair II",
                "city": "Denair",
                "county": "Stanislaus",
            },
        },
        "chill": {
            "portions": 61.81,
            "season": {"start": "2025-11-01", "end": "2026-03-01"},
        },
        "degreeDays": {
            "peachTwigBorer": {
                "pest": "Peach twig borer (Anarsia lineatella)",
                "lowerF": 50,
                "upperF": 88,
                "biofix": "2026-01-01",
                "windowEnd": "2026-05-03",
                "cumulativeDDF": 990.2,
                "method": "single-sine, horizontal cutoff",
                "sourceUrl": "https://ipm.example",
            },
            "navelOrangeworm": {
                "pest": "Navel orangeworm (Amyelois transitella)",
                "lowerF": 55,
                "upperF": 94,
                "biofix": "2026-01-01",
                "windowEnd": "2026-05-03",
                "cumulativeDDF": 640.4,
                "method": "single-sine, horizontal cutoff",
                "sourceUrl": "https://ipm.example",
            },
        },
        "blocks": [
            {
                "fieldId": 1,
                "block": "Johnston Block 1",
                "ranch": "Johnston",
                "crop": "Freestone Peach",
                "variety": "Kaweah",
                "acres": 33,
                "chillPortions": 61.81,
                "pestModelKey": "peachTwigBorer",
                "pestModel": {
                    "pest": "Peach twig borer (Anarsia lineatella)",
                    "biofix": "2026-01-01",
                    "windowEnd": "2026-05-03",
                    "cumulativeDDF": 990.2,
                    "lowerF": 50,
                    "upperF": 88,
                    "method": "single-sine, horizontal cutoff",
                    "sourceUrl": "https://ipm.example",
                },
            },
            {
                "fieldId": 2,
                "block": "Johnston Block 2",
                "ranch": "Johnston",
                "crop": "Freestone Peach",
                "variety": "Zee Lady",
                "acres": 18.5,
                "chillPortions": 61.81,
                "pestModelKey": "peachTwigBorer",
                "pestModel": {
                    "pest": "Peach twig borer (Anarsia lineatella)",
                    "biofix": "2026-01-01",
                    "windowEnd": "2026-05-03",
                    "cumulativeDDF": 990.2,
                    "lowerF": 50,
                    "upperF": 88,
                    "method": "single-sine, horizontal cutoff",
                    "sourceUrl": "https://ipm.example",
                },
            },
            {
                "fieldId": 30,
                "block": "Mello Block 12",
                "ranch": "Mello",
                "crop": "Almond",
                "variety": "Nonpareil",
                "acres": 42,
                "chillPortions": 61.81,
                "pestModelKey": "navelOrangeworm",
                "pestModel": {
                    "pest": "Navel orangeworm (Amyelois transitella)",
                    "biofix": "2026-01-01",
                    "windowEnd": "2026-05-03",
                    "cumulativeDDF": 640.4,
                    "lowerF": 55,
                    "upperF": 94,
                    "method": "single-sine, horizontal cutoff",
                    "sourceUrl": "https://ipm.example",
                },
            },
        ],
    }
    payload.update(overrides)
    return payload


class FakeResponse:
    def __init__(self, payload=None, raw=None):
        if raw is None:
            raw = json.dumps(payload).encode("utf-8")
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


def fake_opener(payload=None, raw=None):
    def _opener(req, timeout=None):
        return FakeResponse(payload=payload, raw=raw)
    return _opener


def raising_opener(exc):
    def _opener(req, timeout=None):
        raise exc
    return _opener


# --- URL resolution --------------------------------------------------------


def test_resolve_seasonal_url_uses_explicit_seasonal_url(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    url = seasonal.resolve_seasonal_url(
        seasonal_url="https://preview.example.com/some/file.json"
    )
    assert url == "https://preview.example.com/some/file.json"


def test_resolve_seasonal_url_derives_from_dashboard(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    url = seasonal.resolve_seasonal_url(
        dashboard_url="https://staging.example.com"
    )
    assert url == "https://staging.example.com/phenology-summary.json"


def test_resolve_seasonal_url_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    url = seasonal.resolve_seasonal_url()
    assert url is not None
    assert url.endswith("/phenology-summary.json")
    assert "vercel.app" in url


def test_resolve_seasonal_url_coerces_missing_scheme(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    url = seasonal.resolve_seasonal_url(dashboard_url="staging.example.com")
    assert url == "https://staging.example.com/phenology-summary.json"


def test_resolve_seasonal_url_rejects_javascript_scheme(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    # Bogus dashboard_url falls back to default — not None.
    url = seasonal.resolve_seasonal_url(dashboard_url="javascript:alert(1)")
    assert url is not None
    assert url.endswith("/phenology-summary.json")
    assert "javascript" not in url


def test_resolve_seasonal_url_env_dashboard_url(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://env.example.com")
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    url = seasonal.resolve_seasonal_url()
    assert url == "https://env.example.com/phenology-summary.json"


def test_resolve_seasonal_url_env_seasonal_url_wins(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")
    monkeypatch.setenv(
        "SEASONAL_URL", "https://other.example.com/feed.json"
    )
    url = seasonal.resolve_seasonal_url()
    assert url == "https://other.example.com/feed.json"


# --- block lookup ----------------------------------------------------------


def test_find_block_exact_match():
    p = make_payload()
    b = seasonal.find_block(p, "Johnston Block 1")
    assert b is not None
    assert b["block"] == "Johnston Block 1"


def test_find_block_case_insensitive():
    p = make_payload()
    b = seasonal.find_block(p, "johnston block 2")
    assert b is not None
    assert b["block"] == "Johnston Block 2"


def test_find_block_unique_suffix():
    p = make_payload()
    # "Block 12" appears in only one block name → unique.
    b = seasonal.find_block(p, "Block 12")
    assert b is not None
    assert b["block"] == "Mello Block 12"


def test_find_block_ambiguous_suffix_returns_none():
    p = make_payload()
    # "Block 1" suffix-matches both "Johnston Block 1" and "Mello Block 12"?
    # No — endswith("block 1") only matches "Johnston Block 1". Build an
    # explicitly ambiguous case instead.
    p["blocks"].append({
        "fieldId": 99, "block": "Other Ranch Block 1", "crop": "Almond",
        "variety": "X", "acres": 5, "chillPortions": 50.0,
        "pestModelKey": "navelOrangeworm", "pestModel": {},
    })
    assert seasonal.find_block(p, "Block 1") is None


def test_find_block_numeric_field_id_fallback():
    p = make_payload()
    b = seasonal.find_block(p, "30")
    assert b is not None
    assert b["fieldId"] == 30


def test_find_block_no_match_returns_none():
    p = make_payload()
    assert seasonal.find_block(p, "Imaginary Block 999") is None


def test_find_block_handles_empty_payload():
    assert seasonal.find_block({}, "Block 1") is None
    assert seasonal.find_block({"blocks": []}, "Block 1") is None
    assert seasonal.find_block(None, "Block 1") is None


def test_find_block_empty_query_returns_none():
    p = make_payload()
    assert seasonal.find_block(p, "") is None
    assert seasonal.find_block(p, "   ") is None


def test_find_block_normalizes_whitespace():
    p = make_payload()
    b = seasonal.find_block(p, "  Johnston   Block   1  ")
    assert b is not None
    assert b["block"] == "Johnston Block 1"


# --- summary rendering -----------------------------------------------------


def test_format_summary_includes_station_and_chill():
    text = seasonal.format_summary(make_payload())
    assert "Denair II" in text
    assert "CIMIS #206" in text
    assert "Chill portions" in text
    assert "61.81" in text


def test_format_summary_lists_both_pest_models():
    text = seasonal.format_summary(make_payload())
    assert "Peach twig borer" in text
    assert "Navel orangeworm" in text
    assert "990.2 DDF" in text
    assert "640.4 DDF" in text


def test_format_summary_groups_by_crop():
    text = seasonal.format_summary(make_payload())
    assert "By crop / pest" in text
    # Two crop groups (Freestone Peach + Almond)
    assert "Freestone Peach" in text
    assert "Almond" in text


def test_format_summary_block_highlights_capped():
    p = make_payload()
    # Inflate to many blocks; the highlight section must cap to MAX_BLOCK_HIGHLIGHTS.
    for i in range(20):
        p["blocks"].append({
            "fieldId": 100 + i,
            "block": f"Test Block {i}",
            "ranch": "Test", "crop": "Almond", "variety": "X",
            "acres": 1.0,
            "chillPortions": 61.81,
            "pestModelKey": "navelOrangeworm",
            "pestModel": p["blocks"][-1]["pestModel"],
        })
    text = seasonal.format_summary(p)
    # Count the bullet lines under the highlights heading.
    after = text.split("Block highlights", 1)[1]
    bullets_in_highlights = [
        line for line in after.splitlines()
        if line.startswith("•")
    ]
    assert len(bullets_in_highlights) <= seasonal.MAX_BLOCK_HIGHLIGHTS


def test_format_summary_handles_unavailable_payload():
    p = make_payload()
    p["metadata"]["available"] = False
    text = seasonal.format_summary(p)
    assert "not yet available" in text


def test_format_summary_handles_partial_payload():
    # Missing blocks/degreeDays sections — still renders something useful.
    text = seasonal.format_summary({"metadata": {"available": True}, "chill": {}})
    assert "Seasonal model" in text
    assert "Station" in text


def test_format_summary_includes_dashboard_link_when_provided():
    text = seasonal.format_summary(
        make_payload(), dashboard_url="https://example.com"
    )
    assert "https://example.com" in text


def test_format_summary_chill_uniform_label():
    text = seasonal.format_summary(make_payload())
    assert "uniform" in text


def test_format_summary_chill_range_label():
    p = make_payload()
    p["blocks"][0]["chillPortions"] = 60.00
    p["blocks"][1]["chillPortions"] = 65.50
    text = seasonal.format_summary(p)
    assert "60.00" in text and "65.50" in text


# --- single-block rendering ------------------------------------------------


def test_format_block_renders_block_card():
    p = make_payload()
    b = seasonal.find_block(p, "Johnston Block 1")
    text = seasonal.format_block(b, p)
    assert "Johnston Block 1" in text
    assert "Kaweah" in text
    assert "33 ac" in text
    assert "61.81" in text
    assert "Peach twig borer" in text
    assert "990.2" in text


def test_format_block_includes_dashboard_link():
    p = make_payload()
    b = seasonal.find_block(p, "Johnston Block 1")
    text = seasonal.format_block(b, p, dashboard_url="https://example.com")
    assert "https://example.com" in text


def test_format_block_handles_missing_pest_model():
    block = {
        "block": "Empty Block", "ranch": "Test", "crop": "Almond",
        "variety": "X", "acres": 5, "chillPortions": 50.0,
    }
    text = seasonal.format_block(block, {"metadata": {}})
    assert "Empty Block" in text
    assert "Decision-support only" in text


def test_format_block_none_returns_warning():
    text = seasonal.format_block(None, make_payload())
    assert text.startswith("⚠️")


# --- end-to-end with mocked HTTP -------------------------------------------


def test_get_seasonal_text_happy_path(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", fake_opener(payload=make_payload()), raising=True,
    )
    text = seasonal.get_seasonal_text()
    assert "Seasonal model" in text
    assert "Denair II" in text
    assert "Peach twig borer" in text


def test_get_seasonal_text_with_block_query(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", fake_opener(payload=make_payload()), raising=True,
    )
    text = seasonal.get_seasonal_text(query="Johnston Block 1")
    assert "Johnston Block 1" in text
    assert "Kaweah" in text


def test_get_seasonal_text_block_not_found(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", fake_opener(payload=make_payload()), raising=True,
    )
    text = seasonal.get_seasonal_text(query="Nonexistent Block 999")
    assert text.startswith("⚠️")
    assert "No block matched" in text


def test_get_seasonal_text_handles_url_error(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", raising_opener(URLError("boom")), raising=True,
    )
    text = seasonal.get_seasonal_text()
    assert text.startswith("⚠️")
    assert "unavailable" in text


def test_get_seasonal_text_handles_timeout(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", raising_opener(TimeoutError("slow")), raising=True,
    )
    text = seasonal.get_seasonal_text()
    assert text.startswith("⚠️")


def test_get_seasonal_text_handles_garbage_json(monkeypatch):
    monkeypatch.setattr(
        seasonal, "urlopen", fake_opener(raw=b"not json at all"), raising=True,
    )
    text = seasonal.get_seasonal_text()
    assert text.startswith("⚠️")
    assert "unreadable" in text


def test_get_seasonal_text_handles_unavailable_flag(monkeypatch):
    p = make_payload()
    p["metadata"]["available"] = False
    monkeypatch.setattr(
        seasonal, "urlopen", fake_opener(payload=p), raising=True,
    )
    text = seasonal.get_seasonal_text()
    assert "not yet available" in text


def test_get_seasonal_text_uses_injected_opener():
    text = seasonal.get_seasonal_text(opener=fake_opener(payload=make_payload()))
    assert "Seasonal model" in text


def test_get_seasonal_text_no_url_config(monkeypatch):
    # Both URLs unparseable → caller sees a setup hint, not a crash.
    monkeypatch.setattr(seasonal, "DEFAULT_DASHBOARD_URL", "javascript:alert(1)")
    monkeypatch.setenv("DASHBOARD_URL", "javascript:alert(1)")
    monkeypatch.setenv("SEASONAL_URL", "not-a-url")
    text = seasonal.get_seasonal_text()
    assert text.startswith("⚠️")
    assert "not configured" in text


# --- env / config ---------------------------------------------------------


def test_get_config_uses_default_timeout(monkeypatch):
    monkeypatch.delenv("SEASONAL_API_TIMEOUT", raising=False)
    cfg = seasonal.get_config()
    assert cfg["timeout_s"] == pytest.approx(seasonal.DEFAULT_TIMEOUT_S)


def test_get_config_parses_timeout_env(monkeypatch):
    monkeypatch.setenv("SEASONAL_API_TIMEOUT", "3.5")
    cfg = seasonal.get_config()
    assert cfg["timeout_s"] == pytest.approx(3.5)


def test_get_config_falls_back_on_garbage_timeout(monkeypatch):
    monkeypatch.setenv("SEASONAL_API_TIMEOUT", "not-a-number")
    cfg = seasonal.get_config()
    assert cfg["timeout_s"] == pytest.approx(seasonal.DEFAULT_TIMEOUT_S)


# --- /seasonal command handler --------------------------------------------


import bot  # noqa: E402

from telegram import InlineKeyboardMarkup  # noqa: E402


def _fake_update(text="/seasonal"):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _run(coro):
    return asyncio.run(coro)


def test_seasonal_command_no_query_calls_get_seasonal_text(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://example.com")
    importlib.reload(bot)

    captured = {}
    def fake_get(query=None, opener=None):
        captured["query"] = query
        return "📅 stub seasonal text"
    # bot.seasonal_command does `import seasonal` at call time, so patching
    # the module-level function is the right hook.
    import seasonal as seasonal_mod
    monkeypatch.setattr(seasonal_mod, "get_seasonal_text", fake_get)

    update = _fake_update("/seasonal")
    _run(bot.seasonal_command(update, MagicMock()))

    update.message.reply_text.assert_called_once()
    args, kwargs = update.message.reply_text.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "stub seasonal text" in text
    assert kwargs.get("parse_mode") == "Markdown"
    assert captured["query"] is None


def test_seasonal_command_with_block_query_passes_through(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://example.com")
    importlib.reload(bot)

    captured = {}
    def fake_get(query=None, opener=None):
        captured["query"] = query
        return "📅 stub block text"
    import seasonal as seasonal_mod
    monkeypatch.setattr(seasonal_mod, "get_seasonal_text", fake_get)

    update = _fake_update("/seasonal Johnston Block 1")
    _run(bot.seasonal_command(update, MagicMock()))

    assert captured["query"] == "Johnston Block 1"


def test_seasonal_command_includes_dashboard_button(monkeypatch):
    monkeypatch.setenv(
        "DASHBOARD_URL", "https://centennial-farm-dashboard-five.vercel.app"
    )
    importlib.reload(bot)

    import seasonal as seasonal_mod
    monkeypatch.setattr(
        seasonal_mod, "get_seasonal_text", lambda query=None, opener=None: "ok",
    )

    update = _fake_update("/seasonal")
    _run(bot.seasonal_command(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    markup = kwargs.get("reply_markup")
    assert isinstance(markup, InlineKeyboardMarkup)
    btn = markup.inline_keyboard[0][0]
    assert btn.url == "https://centennial-farm-dashboard-five.vercel.app"
    assert "Dashboard" in btn.text


def test_seasonal_command_skips_button_when_dashboard_url_invalid(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "javascript:alert(1)")
    importlib.reload(bot)

    import seasonal as seasonal_mod
    monkeypatch.setattr(
        seasonal_mod, "get_seasonal_text", lambda query=None, opener=None: "ok",
    )

    update = _fake_update("/seasonal")
    _run(bot.seasonal_command(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    assert kwargs.get("reply_markup") is None


def test_seasonal_command_button_defaults_to_public_alias(monkeypatch):
    """With no DASHBOARD_URL env override, the /seasonal button must point at
    the public production alias, not the stale protected preview host."""
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    importlib.reload(bot)

    import seasonal as seasonal_mod
    monkeypatch.setattr(
        seasonal_mod, "get_seasonal_text", lambda query=None, opener=None: "ok",
    )

    update = _fake_update("/seasonal")
    _run(bot.seasonal_command(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    markup = kwargs.get("reply_markup")
    assert isinstance(markup, InlineKeyboardMarkup)
    btn = markup.inline_keyboard[0][0]
    assert btn.url == "https://centennial-farm-dashboard-five.vercel.app"
    # And the seasonal JSON fetch derives the same host by default.
    assert seasonal.resolve_seasonal_url() == (
        "https://centennial-farm-dashboard-five.vercel.app/phenology-summary.json"
    )


def test_default_seasonal_url_uses_public_alias(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SEASONAL_URL", raising=False)
    assert seasonal.DEFAULT_DASHBOARD_URL == (
        "https://centennial-farm-dashboard-five.vercel.app"
    )
    assert seasonal.resolve_seasonal_url() == (
        "https://centennial-farm-dashboard-five.vercel.app/phenology-summary.json"
    )


# --- Markdown escaping ----------------------------------------------------


def test_md_escape_escapes_telegram_specials():
    s = seasonal._md_escape("Block_1*x[y]`z")
    # Underscore, asterisk, backtick, and opening bracket are escaped; closing
    # bracket is left alone (Telegram legacy Markdown only treats the opener).
    assert s == "Block\\_1\\*x\\[y]\\`z"


def test_md_escape_handles_none_and_non_strings():
    assert seasonal._md_escape(None) == "—"
    assert seasonal._md_escape(33) == "33"


def test_md_escape_escapes_backslashes_first():
    # A bare backslash is itself a Markdown escape character; round-trip
    # behavior must escape it to avoid swallowing the next character.
    assert seasonal._md_escape("a\\b") == "a\\\\b"


def test_format_summary_escapes_block_name_underscore():
    """Block names with `_` (or other Markdown specials) must not break the
    parse_mode='Markdown' send. Regression for the dashboard-controlled
    field injection issue."""
    p = make_payload()
    p["blocks"][0]["block"] = "Johnston_Block_1"
    p["blocks"][0]["variety"] = "Kaweah*Special"
    text = seasonal.format_summary(p)
    # Underscore in the rendered name is escaped, not interpreted as italic.
    assert "Johnston\\_Block\\_1" in text
    assert "Kaweah\\*Special" in text
    # Equal numbers of unescaped underscores → safe to send as Markdown.
    assert _unescaped_specials_balanced(text)


def test_format_summary_escapes_pest_name_specials():
    p = make_payload()
    p["degreeDays"]["peachTwigBorer"]["pest"] = "Peach_twig*borer"
    p["blocks"][0]["pestModel"]["pest"] = "Peach_twig*borer"
    text = seasonal.format_summary(p)
    assert "Peach\\_twig\\*borer" in text
    assert _unescaped_specials_balanced(text)


def test_format_summary_escapes_crop_label():
    p = make_payload()
    p["blocks"][0]["crop"] = "Free_stone Peach"
    text = seasonal.format_summary(p)
    assert "Free\\_stone Peach" in text


def test_format_block_escapes_dashboard_fields():
    p = make_payload()
    block = {
        "block": "Block_1*x",
        "ranch": "Test_Ranch",
        "crop": "Free_stone",
        "variety": "Kaweah*Code",
        "acres": 10,
        "chillPortions": 60.0,
        "pestModel": {
            "pest": "Pest_with_underscore",
            "biofix": "2026-01-01",
            "windowEnd": "2026-05-03",
            "cumulativeDDF": 100.0,
            "lowerF": 50,
            "upperF": 88,
        },
    }
    text = seasonal.format_block(block, p, dashboard_url="https://x_y.example.com")
    assert "Block\\_1\\*x" in text
    assert "Test\\_Ranch" in text
    assert "Free\\_stone" in text
    assert "Kaweah\\*Code" in text
    assert "Pest\\_with\\_underscore" in text
    assert "x\\_y.example.com" in text
    assert _unescaped_specials_balanced(text)


def _unescaped_specials_balanced(text: str) -> bool:
    """Sanity check: each Markdown delimiter (`_`, `*`, `` ` ``) must occur
    an even number of times when not preceded by a backslash, so Telegram
    can match opening and closing markers. Underscores/asterisks inside
    dashboard-controlled values should always be escaped, not raw.
    """
    for ch in ("_", "*", "`"):
        count = 0
        i = 0
        while i < len(text):
            if text[i] == ch and (i == 0 or text[i - 1] != "\\"):
                count += 1
            i += 1
        if count % 2 != 0:
            return False
    return True


# --- _block_highlight_lines sort tiebreak ---------------------------------


def test_block_highlight_lines_sinks_malformed_acres():
    """Malformed acres ('N/A', None, non-numeric) must rank below every block
    with a real positive acres value, regardless of how small."""
    blocks = [
        {"block": "Tiny", "acres": 0.1, "variety": "v"},
        {"block": "Garbage", "acres": "not-a-number", "variety": "v"},
        {"block": "Missing", "variety": "v"},  # no acres key
        {"block": "None", "acres": None, "variety": "v"},
        {"block": "Big", "acres": 50, "variety": "v"},
    ]
    lines = seasonal._block_highlight_lines(blocks, limit=10)
    assert len(lines) == 5
    # Find the index of each named block in the rendered output.
    order = [next(i for i, ln in enumerate(lines) if name in ln)
             for name in ("Big", "Tiny", "Garbage", "Missing", "None")]
    big_idx, tiny_idx, garbage_idx, missing_idx, none_idx = order
    # Valid positive-acre blocks must precede every malformed/missing one.
    assert big_idx < garbage_idx
    assert big_idx < missing_idx
    assert big_idx < none_idx
    assert tiny_idx < garbage_idx
    assert tiny_idx < missing_idx
    assert tiny_idx < none_idx


def test_block_highlight_lines_sinks_zero_acres_blocks():
    """A 0-acre or negative-acre block has nothing operationally interesting
    to highlight; it should sink below positive-acre blocks too."""
    blocks = [
        {"block": "Zero", "acres": 0, "variety": "v"},
        {"block": "Neg", "acres": -5, "variety": "v"},
        {"block": "Real", "acres": 1.5, "variety": "v"},
    ]
    lines = seasonal._block_highlight_lines(blocks, limit=10)
    real_idx = next(i for i, ln in enumerate(lines) if "Real" in ln)
    zero_idx = next(i for i, ln in enumerate(lines) if "Zero" in ln)
    neg_idx = next(i for i, ln in enumerate(lines) if "Neg" in ln)
    assert real_idx < zero_idx
    assert real_idx < neg_idx


def test_block_highlight_lines_orders_valid_blocks_by_acres_desc():
    """Pure ordering check among valid positive-acre blocks: largest first."""
    blocks = [
        {"block": "A", "acres": 5, "variety": "v"},
        {"block": "B", "acres": 50, "variety": "v"},
        {"block": "C", "acres": 20, "variety": "v"},
    ]
    lines = seasonal._block_highlight_lines(blocks, limit=10)
    # Rendered top-to-bottom should be B, C, A.
    assert "B" in lines[0]
    assert "C" in lines[1]
    assert "A" in lines[2]
