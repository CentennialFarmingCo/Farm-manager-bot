"""On-demand weather alerts for the farm bot.

Fetches a no-key forecast from Open-Meteo and renders a concise Telegram
summary plus operational alerts (spray wind, heat risk, frost risk, rain
risk, irrigation hint). Designed to be run on demand via /weather or
/alerts — no scheduler, no DB, no secrets.

Open-Meteo is free and key-less. Docs: https://open-meteo.com/

Configurable via environment:

  FARM_LAT                 latitude  (default 37.30, Merced County, CA)
  FARM_LON                 longitude (default -120.48)
  FARM_LOCATION_NAME       label shown in the message (default "Merced County, CA")
  WIND_ALERT_MPH           spray-caution wind threshold (default 10)
  HEAT_ALERT_F             heat-caution high threshold (default 95)
  FROST_ALERT_F            frost-caution low threshold (default 34)
  RAIN_PROB_ALERT_PCT      rain-caution probability % (default 50)
  RAIN_AMOUNT_ALERT_IN     rain-caution accumulation inches (default 0.10)
  WEATHER_API_TIMEOUT      HTTP timeout in seconds (default 8)

The forecast is requested in imperial units so thresholds line up with how
the user talks about wind (mph), temperature (°F), and rainfall (inches).
"""

from __future__ import annotations

import json
import os
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen, Request


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

DEFAULT_LAT = 37.30
DEFAULT_LON = -120.48
DEFAULT_LOCATION = "Merced County, CA"

DEFAULT_WIND_ALERT_MPH = 10.0
DEFAULT_HEAT_ALERT_F = 95.0
DEFAULT_FROST_ALERT_F = 34.0
DEFAULT_RAIN_PROB_ALERT_PCT = 50.0
DEFAULT_RAIN_AMOUNT_ALERT_IN = 0.10
DEFAULT_TIMEOUT_S = 8.0


def _parse_float_env(name: str, default: float) -> float:
    """Read a float env var, falling back to default on missing/garbage.

    We never raise here — the bot must remain runnable even if an op typo'd
    a threshold. Bad values just fall through to documented defaults.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def get_config() -> dict:
    """Resolve runtime config from env, with safe Merced-area defaults."""
    return {
        "lat": _parse_float_env("FARM_LAT", DEFAULT_LAT),
        "lon": _parse_float_env("FARM_LON", DEFAULT_LON),
        "location_name": os.getenv("FARM_LOCATION_NAME", DEFAULT_LOCATION) or DEFAULT_LOCATION,
        "wind_mph": _parse_float_env("WIND_ALERT_MPH", DEFAULT_WIND_ALERT_MPH),
        "heat_f": _parse_float_env("HEAT_ALERT_F", DEFAULT_HEAT_ALERT_F),
        "frost_f": _parse_float_env("FROST_ALERT_F", DEFAULT_FROST_ALERT_F),
        "rain_prob_pct": _parse_float_env("RAIN_PROB_ALERT_PCT", DEFAULT_RAIN_PROB_ALERT_PCT),
        "rain_amount_in": _parse_float_env("RAIN_AMOUNT_ALERT_IN", DEFAULT_RAIN_AMOUNT_ALERT_IN),
        "timeout_s": _parse_float_env("WEATHER_API_TIMEOUT", DEFAULT_TIMEOUT_S),
    }


def build_url(lat: float, lon: float) -> str:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation",
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_probability_max,"
            "wind_speed_10m_max"
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "forecast_days": "2",
    }
    return f"{OPEN_METEO_URL}?{urlencode(params)}"


def fetch_forecast(lat: float, lon: float, timeout_s: float = DEFAULT_TIMEOUT_S, opener=None) -> dict:
    """Fetch and parse Open-Meteo forecast.

    `opener` exists so tests can inject a fake `urlopen`. Production passes
    None and uses the stdlib opener directly. Returns the decoded JSON; the
    caller decides how to interpret missing fields.
    """
    url = build_url(lat, lon)
    req = Request(url, headers={"User-Agent": "centennial-farm-bot/1.0"})
    open_fn = opener or urlopen
    with open_fn(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _first(daily: dict, key: str):
    """Return today's value (index 0) for a daily array, or None."""
    arr = daily.get(key) if isinstance(daily, dict) else None
    if isinstance(arr, list) and arr:
        return arr[0]
    return None


def evaluate_alerts(payload: dict, cfg: dict) -> list:
    """Apply conservative threshold rules and return alert lines.

    Rules are deliberately simple and transparent so the user can predict
    when they fire. The irrigation hint compounds (hot AND dry/windy),
    matching how a grower actually decides to start a set.
    """
    alerts = []
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    wind_now = current.get("wind_speed_10m")
    gust_now = current.get("wind_gusts_10m")
    wind_max = _first(daily, "wind_speed_10m_max")
    high_f = _first(daily, "temperature_2m_max")
    low_f = _first(daily, "temperature_2m_min")
    precip_sum = _first(daily, "precipitation_sum")
    precip_prob = _first(daily, "precipitation_probability_max")

    # Spray caution: if either current wind, today's expected max, or current
    # gusts cross the threshold, holding off is the safer call.
    wind_signals = [w for w in (wind_now, wind_max, gust_now) if isinstance(w, (int, float))]
    if wind_signals and max(wind_signals) >= cfg["wind_mph"]:
        worst = max(wind_signals)
        alerts.append(
            f"🌬 Spray caution: wind up to {worst:.0f} mph "
            f"(threshold {cfg['wind_mph']:.0f} mph). Hold off on spraying."
        )

    if isinstance(high_f, (int, float)) and high_f >= cfg["heat_f"]:
        alerts.append(
            f"🥵 Heat caution: high {high_f:.0f}°F "
            f"(threshold {cfg['heat_f']:.0f}°F). Start crews early; water often."
        )

    if isinstance(low_f, (int, float)) and low_f <= cfg["frost_f"]:
        alerts.append(
            f"❄️ Frost caution: low {low_f:.0f}°F "
            f"(threshold {cfg['frost_f']:.0f}°F). Check frost protection."
        )

    rain_hits = []
    if isinstance(precip_prob, (int, float)) and precip_prob >= cfg["rain_prob_pct"]:
        rain_hits.append(f"chance {precip_prob:.0f}%")
    if isinstance(precip_sum, (int, float)) and precip_sum >= cfg["rain_amount_in"]:
        rain_hits.append(f"~{precip_sum:.2f} in expected")
    if rain_hits:
        alerts.append(
            "🌧 Rain caution: " + ", ".join(rain_hits)
            + ". Plan harvest/spray timing accordingly."
        )

    # Irrigation hint: hot day + dry day, with extra emphasis if also windy.
    # ET (evapotranspiration) climbs fastest under hot+dry+wind.
    hot = isinstance(high_f, (int, float)) and high_f >= cfg["heat_f"]
    dry = isinstance(precip_sum, (int, float)) and precip_sum < cfg["rain_amount_in"]
    windy = isinstance(wind_max, (int, float)) and wind_max >= cfg["wind_mph"]
    if hot and dry:
        extra = " plus wind" if windy else ""
        alerts.append(
            f"💧 Irrigation note: hot and dry{extra} — expect high ET, "
            "consider extending today's set."
        )

    return alerts


def format_summary(payload: dict, cfg: dict, alerts: Optional[list] = None) -> str:
    """Render a concise Telegram-friendly Markdown summary.

    Designed to fit comfortably in one phone screen. Numbers round to whole
    units to match how operators read a pickup-truck thermometer.
    """
    if alerts is None:
        alerts = evaluate_alerts(payload, cfg)

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    temp = current.get("temperature_2m")
    wind = current.get("wind_speed_10m")
    gust = current.get("wind_gusts_10m")
    precip_now = current.get("precipitation")
    high = _first(daily, "temperature_2m_max")
    low = _first(daily, "temperature_2m_min")
    precip_sum = _first(daily, "precipitation_sum")
    precip_prob = _first(daily, "precipitation_probability_max")

    def fmt_temp(v):
        return f"{v:.0f}°F" if isinstance(v, (int, float)) else "—"

    def fmt_wind(v):
        return f"{v:.0f} mph" if isinstance(v, (int, float)) else "—"

    def fmt_in(v):
        return f"{v:.2f} in" if isinstance(v, (int, float)) else "—"

    def fmt_pct(v):
        return f"{v:.0f}%" if isinstance(v, (int, float)) else "—"

    lines = [
        f"🌤 *Weather — {cfg['location_name']}*",
        f"Now: {fmt_temp(temp)}, wind {fmt_wind(wind)}"
        + (f" (gusts {fmt_wind(gust)})" if isinstance(gust, (int, float)) else "")
        + (f", precip {fmt_in(precip_now)}" if isinstance(precip_now, (int, float)) and precip_now > 0 else ""),
        f"Today: high {fmt_temp(high)}, low {fmt_temp(low)}, "
        f"rain {fmt_in(precip_sum)} ({fmt_pct(precip_prob)} chance)",
        "",
    ]
    if alerts:
        lines.append("*Alerts:*")
        lines.extend(f"• {a}" for a in alerts)
    else:
        lines.append("✅ No operational alerts.")
    lines.append("")
    lines.append(
        "_Source: Open-Meteo (no API key). Thresholds are configurable; "
        "always use your own judgment in the field._"
    )
    return "\n".join(lines)


def get_weather_text(opener=None) -> str:
    """End-to-end: read config, fetch, render. Never raises.

    A weather command that crashes the bot is worse than one that admits
    the API is down. All network and parse errors are caught and returned
    as a one-line operator message.
    """
    cfg = get_config()
    try:
        payload = fetch_forecast(cfg["lat"], cfg["lon"], cfg["timeout_s"], opener=opener)
    except (HTTPError, URLError, TimeoutError) as e:
        return (
            "⚠️ Weather service unavailable right now "
            f"({type(e).__name__}). Try again in a minute."
        )
    except OSError as e:
        return (
            "⚠️ Weather service unavailable right now "
            f"(network error: {e}). Try again in a minute."
        )
    except (ValueError, json.JSONDecodeError):
        return "⚠️ Weather service returned an unreadable response. Try again."

    try:
        return format_summary(payload, cfg)
    except Exception as e:  # pragma: no cover - defensive
        return f"⚠️ Could not format weather summary ({type(e).__name__})."
