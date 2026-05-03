"""Tests for the /dashboard command and its URL helpers."""
import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot  # noqa: E402

from telegram import InlineKeyboardMarkup  # noqa: E402


def _reload_bot(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    importlib.reload(bot)
    return bot


# --- normalize_dashboard_url ---

def test_normalize_passes_through_https():
    assert bot.normalize_dashboard_url(
        "https://centennial-farm-dashboard-qvytatulr.vercel.app"
    ) == "https://centennial-farm-dashboard-qvytatulr.vercel.app"


def test_normalize_passes_through_http():
    assert bot.normalize_dashboard_url("http://example.com/path").startswith("http://")


def test_normalize_adds_https_when_scheme_missing():
    assert bot.normalize_dashboard_url("example.com") == "https://example.com"


def test_normalize_strips_whitespace():
    assert bot.normalize_dashboard_url("  https://example.com  ") == "https://example.com"


def test_normalize_rejects_empty():
    assert bot.normalize_dashboard_url("") is None
    assert bot.normalize_dashboard_url(None) is None
    assert bot.normalize_dashboard_url("   ") is None


def test_normalize_rejects_bad_scheme():
    assert bot.normalize_dashboard_url("javascript:alert(1)") is None
    assert bot.normalize_dashboard_url("ftp://example.com") is None


def test_normalize_rejects_no_host():
    # Bare hostname with no dot is rejected — Telegram URL buttons need a real host.
    assert bot.normalize_dashboard_url("notaurl") is None
    assert bot.normalize_dashboard_url("https://") is None


# --- DASHBOARD_URL env wiring ---

def test_default_url_is_vercel(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    importlib.reload(bot)
    assert bot.DASHBOARD_URL == bot.DEFAULT_DASHBOARD_URL
    assert "vercel.app" in bot.DEFAULT_DASHBOARD_URL


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://staging.example.com")
    importlib.reload(bot)
    assert bot.DASHBOARD_URL == "https://staging.example.com"


# --- /dashboard handler behavior ---

def _fake_update():
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_dashboard_sends_inline_keyboard_with_url(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "https://centennial-farm-dashboard-qvytatulr.vercel.app")
    importlib.reload(bot)
    update = _fake_update()
    _run(bot.dashboard(update, MagicMock()))

    update.message.reply_text.assert_called_once()
    args, kwargs = update.message.reply_text.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "Centennial" in text
    assert "https://centennial-farm-dashboard-qvytatulr.vercel.app" in text  # plain-URL fallback present
    markup = kwargs.get("reply_markup")
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = markup.inline_keyboard
    assert len(buttons) == 1 and len(buttons[0]) == 1
    btn = buttons[0][0]
    assert btn.url == "https://centennial-farm-dashboard-qvytatulr.vercel.app"
    assert "Open" in btn.text and "Dashboard" in btn.text


def test_dashboard_normalizes_missing_scheme(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "centennial-farm-dashboard-qvytatulr.vercel.app")
    importlib.reload(bot)
    update = _fake_update()
    _run(bot.dashboard(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    markup = kwargs.get("reply_markup")
    assert isinstance(markup, InlineKeyboardMarkup)
    btn = markup.inline_keyboard[0][0]
    assert btn.url.startswith("https://")
    assert "vercel.app" in btn.url


def test_dashboard_invalid_url_falls_back_to_setup_message(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "javascript:alert(1)")
    importlib.reload(bot)
    update = _fake_update()
    _run(bot.dashboard(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "DASHBOARD_URL" in text
    assert kwargs.get("reply_markup") is None  # no broken button


def test_dashboard_does_not_leak_token(monkeypatch):
    secret = "1234567890:VERY-SECRET-TOKEN-DO-NOT-LEAK"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setenv("DASHBOARD_URL", "https://centennial-farm-dashboard-qvytatulr.vercel.app")
    importlib.reload(bot)
    update = _fake_update()
    _run(bot.dashboard(update, MagicMock()))

    args, kwargs = update.message.reply_text.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert secret not in text
    markup = kwargs.get("reply_markup")
    if markup is not None:
        for row in markup.inline_keyboard:
            for btn in row:
                assert secret not in (btn.url or "")
                assert secret not in (btn.text or "")
