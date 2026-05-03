"""Tests for the on-demand weather/alerts utility.

The Open-Meteo HTTP call is mocked at the `urlopen` boundary so tests stay
hermetic and never touch the network. Each scenario builds a payload that
mimics the real Open-Meteo response shape and asserts on the alert lines
and rendered summary.
"""

import json
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import weather  # noqa: E402


# --- helpers ---------------------------------------------------------------


def make_payload(
    temp=78,
    wind=5,
    gust=8,
    precip_now=0.0,
    high=85,
    low=55,
    rain_sum=0.0,
    rain_prob=10,
    wind_max=8,
):
    return {
        "current": {
            "temperature_2m": temp,
            "wind_speed_10m": wind,
            "wind_gusts_10m": gust,
            "precipitation": precip_now,
        },
        "daily": {
            "temperature_2m_max": [high],
            "temperature_2m_min": [low],
            "precipitation_sum": [rain_sum],
            "precipitation_probability_max": [rain_prob],
            "wind_speed_10m_max": [wind_max],
        },
    }


class FakeResponse:
    """Context manager mimicking what urlopen returns."""

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


def fake_opener(payload):
    def _opener(req, timeout=None):
        return FakeResponse(payload=payload)
    return _opener


def raising_opener(exc):
    def _opener(req, timeout=None):
        raise exc
    return _opener


def default_cfg(**overrides):
    cfg = {
        "lat": 37.30,
        "lon": -120.48,
        "location_name": "Merced County, CA",
        "wind_mph": 10.0,
        "heat_f": 95.0,
        "frost_f": 34.0,
        "rain_prob_pct": 50.0,
        "rain_amount_in": 0.10,
        "timeout_s": 8.0,
    }
    cfg.update(overrides)
    return cfg


# --- alert evaluation ------------------------------------------------------


def test_no_alerts_on_normal_day():
    alerts = weather.evaluate_alerts(make_payload(), default_cfg())
    assert alerts == []


def test_wind_alert_fires_above_threshold():
    payload = make_payload(wind=4, gust=6, wind_max=18)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    assert any("Spray caution" in a for a in alerts)
    assert any("18 mph" in a for a in alerts)


def test_wind_alert_uses_current_gust_signal():
    payload = make_payload(wind=4, gust=22, wind_max=6)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    assert any("22 mph" in a for a in alerts)


def test_heat_alert_fires_at_or_above_threshold():
    payload = make_payload(high=98, rain_sum=0.0)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    assert any("Heat caution" in a for a in alerts)
    # Hot + dry should also surface the irrigation note.
    assert any("Irrigation note" in a for a in alerts)


def test_frost_alert_fires_at_or_below_threshold():
    payload = make_payload(high=50, low=32)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    assert any("Frost caution" in a for a in alerts)


def test_rain_alert_fires_on_high_probability():
    payload = make_payload(rain_prob=80, rain_sum=0.05)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    rain = [a for a in alerts if "Rain caution" in a]
    assert rain, alerts
    assert "80%" in rain[0]


def test_rain_alert_fires_on_high_accumulation():
    payload = make_payload(rain_prob=20, rain_sum=0.40)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    rain = [a for a in alerts if "Rain caution" in a]
    assert rain, alerts
    assert "0.40" in rain[0]


def test_irrigation_note_only_when_hot_and_dry():
    # Hot but rain expected -> no irrigation note.
    payload = make_payload(high=100, rain_sum=0.5, rain_prob=80)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    assert not any("Irrigation note" in a for a in alerts)


def test_irrigation_note_marks_wind_when_present():
    payload = make_payload(high=100, rain_sum=0.0, wind_max=20)
    alerts = weather.evaluate_alerts(payload, default_cfg())
    note = [a for a in alerts if "Irrigation note" in a]
    assert note and "plus wind" in note[0]


def test_thresholds_can_be_tightened():
    # Same payload, stricter thresholds -> alerts fire that wouldn't by default.
    payload = make_payload(high=80, low=40, wind_max=6, rain_prob=30, rain_sum=0.05)
    cfg = default_cfg(wind_mph=5, heat_f=75, frost_f=42, rain_prob_pct=25)
    alerts = weather.evaluate_alerts(payload, cfg)
    kinds = " | ".join(alerts)
    assert "Spray caution" in kinds
    assert "Heat caution" in kinds
    assert "Frost caution" in kinds
    assert "Rain caution" in kinds


def test_missing_fields_do_not_raise():
    # API returned an unexpected shape (e.g. partial outage) — we just emit
    # no alerts rather than crashing the bot.
    alerts = weather.evaluate_alerts({"current": {}, "daily": {}}, default_cfg())
    assert alerts == []


# --- summary rendering -----------------------------------------------------


def test_format_summary_includes_location_and_no_alerts_marker():
    payload = make_payload()
    text = weather.format_summary(payload, default_cfg())
    assert "Merced County, CA" in text
    assert "No operational alerts" in text


def test_format_summary_lists_alerts_as_bullets():
    payload = make_payload(high=100, rain_sum=0.0)
    text = weather.format_summary(payload, default_cfg())
    assert "*Alerts:*" in text
    assert "• " in text


# --- end-to-end with mocked HTTP -------------------------------------------


def test_get_weather_text_happy_path(monkeypatch):
    monkeypatch.setattr(weather, "urlopen", fake_opener(make_payload()), raising=True)
    text = weather.get_weather_text()
    assert "Weather" in text
    assert "Merced County" in text or "Now:" in text


def test_get_weather_text_handles_url_error(monkeypatch):
    monkeypatch.setattr(
        weather, "urlopen", raising_opener(URLError("boom")), raising=True,
    )
    text = weather.get_weather_text()
    assert text.startswith("⚠️")
    assert "unavailable" in text


def test_get_weather_text_handles_timeout(monkeypatch):
    monkeypatch.setattr(
        weather, "urlopen", raising_opener(TimeoutError("slow")), raising=True,
    )
    text = weather.get_weather_text()
    assert text.startswith("⚠️")


def test_get_weather_text_handles_garbage_json(monkeypatch):
    def opener(req, timeout=None):
        return FakeResponse(raw=b"not json at all")
    monkeypatch.setattr(weather, "urlopen", opener, raising=True)
    text = weather.get_weather_text()
    assert text.startswith("⚠️")
    assert "unreadable" in text


def test_get_weather_text_uses_injected_opener():
    # Sanity check the explicit opener parameter (also used elsewhere).
    text = weather.get_weather_text(opener=fake_opener(make_payload(high=100, rain_sum=0)))
    assert "Heat caution" in text
    assert "Irrigation note" in text


# --- env var parsing -------------------------------------------------------


def test_get_config_uses_defaults_when_env_unset(monkeypatch):
    for v in (
        "FARM_LAT", "FARM_LON", "FARM_LOCATION_NAME",
        "WIND_ALERT_MPH", "HEAT_ALERT_F", "FROST_ALERT_F",
        "RAIN_PROB_ALERT_PCT", "RAIN_AMOUNT_ALERT_IN", "WEATHER_API_TIMEOUT",
    ):
        monkeypatch.delenv(v, raising=False)
    cfg = weather.get_config()
    assert cfg["lat"] == pytest.approx(weather.DEFAULT_LAT)
    assert cfg["lon"] == pytest.approx(weather.DEFAULT_LON)
    assert cfg["location_name"] == weather.DEFAULT_LOCATION
    assert cfg["wind_mph"] == weather.DEFAULT_WIND_ALERT_MPH


def test_get_config_parses_env(monkeypatch):
    monkeypatch.setenv("FARM_LAT", "36.95")
    monkeypatch.setenv("FARM_LON", "-120.10")
    monkeypatch.setenv("FARM_LOCATION_NAME", "Test Ranch")
    monkeypatch.setenv("WIND_ALERT_MPH", "12.5")
    monkeypatch.setenv("HEAT_ALERT_F", "100")
    monkeypatch.setenv("FROST_ALERT_F", "30")
    monkeypatch.setenv("RAIN_PROB_ALERT_PCT", "60")
    cfg = weather.get_config()
    assert cfg["lat"] == pytest.approx(36.95)
    assert cfg["lon"] == pytest.approx(-120.10)
    assert cfg["location_name"] == "Test Ranch"
    assert cfg["wind_mph"] == pytest.approx(12.5)
    assert cfg["heat_f"] == pytest.approx(100.0)
    assert cfg["frost_f"] == pytest.approx(30.0)
    assert cfg["rain_prob_pct"] == pytest.approx(60.0)


def test_get_config_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("FARM_LAT", "not-a-number")
    monkeypatch.setenv("WIND_ALERT_MPH", "")
    cfg = weather.get_config()
    assert cfg["lat"] == pytest.approx(weather.DEFAULT_LAT)
    assert cfg["wind_mph"] == pytest.approx(weather.DEFAULT_WIND_ALERT_MPH)


# --- URL construction ------------------------------------------------------


def test_build_url_contains_imperial_units_and_coords():
    url = weather.build_url(36.95, -120.10)
    assert "latitude=36.9500" in url
    assert "longitude=-120.1000" in url
    assert "temperature_unit=fahrenheit" in url
    assert "wind_speed_unit=mph" in url
    assert "precipitation_unit=inch" in url
