"""On-demand seasonal phenology summary for the farm bot.

Fetches the dashboard's published phenology JSON (chill portions + insect
degree-day models) and renders a concise Telegram summary. Designed to be
run on demand via /seasonal — no scheduler, no DB, no secrets. Mirrors the
shape of weather.py so failures are surfaced as a one-line operator message
rather than a stack trace.

The dashboard publishes a static JSON at `/phenology-summary.json` that is
regenerated when its model run finishes. We default to deriving the seasonal
URL from the bot's existing DASHBOARD_URL (so an op only has to set the host
in one place) and allow a dedicated SEASONAL_URL override when needed (e.g.
to point at a Vercel preview or a different file path).
"""

from __future__ import annotations

import json
import os
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen, Request


DEFAULT_DASHBOARD_URL = "https://centennial-farm-dashboard-five.vercel.app"
SEASONAL_PATH = "/phenology-summary.json"
DEFAULT_TIMEOUT_S = 8.0

# Telegram allows up to 4096 chars per message. Keep the rendered summary
# short enough to read on one phone screen — block highlights are capped.
MAX_BLOCK_HIGHLIGHTS = 6


def _normalize_base_url(raw: Optional[str]) -> Optional[str]:
    """Coerce a dashboard URL into a clean http(s) origin or return None.

    The bot has a similar helper for /dashboard but we keep this self-contained
    so seasonal stays runnable even if bot.py's helper changes shape. Anything
    we can't safely turn into an http(s) origin returns None — the caller then
    surfaces a setup hint instead of trying to fetch garbage.
    """
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme:
        parsed = urlparse("https://" + candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return urlunparse(parsed)


def resolve_seasonal_url(
    dashboard_url: Optional[str] = None,
    seasonal_url: Optional[str] = None,
) -> Optional[str]:
    """Decide which URL to fetch the seasonal JSON from.

    Precedence:
      1. Explicit SEASONAL_URL (full URL, including path) wins.
      2. Otherwise derive from DASHBOARD_URL by appending SEASONAL_PATH.
      3. Otherwise derive from the built-in default dashboard host.

    Returns None only if all candidates are unparseable, which the caller
    treats as a configuration error rather than a transient outage.
    """
    if seasonal_url is None:
        seasonal_url = os.getenv("SEASONAL_URL")
    if seasonal_url:
        normalized = _normalize_base_url(seasonal_url)
        if normalized:
            return normalized

    if dashboard_url is None:
        dashboard_url = os.getenv("DASHBOARD_URL", DEFAULT_DASHBOARD_URL)

    base = _normalize_base_url(dashboard_url) or _normalize_base_url(DEFAULT_DASHBOARD_URL)
    if base is None:
        return None
    parsed = urlparse(base)
    return urlunparse((parsed.scheme, parsed.netloc, SEASONAL_PATH, "", "", ""))


def _parse_float_env(name: str, default: float) -> float:
    """Read a float env var, falling back to default on missing/garbage.

    Mirrors weather._parse_float_env — bad values must never crash the bot.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def get_config() -> dict:
    """Resolve seasonal-fetch config from env."""
    return {
        "url": resolve_seasonal_url(),
        "timeout_s": _parse_float_env("SEASONAL_API_TIMEOUT", DEFAULT_TIMEOUT_S),
        "dashboard_url": _normalize_base_url(
            os.getenv("DASHBOARD_URL", DEFAULT_DASHBOARD_URL)
        ),
    }


def fetch_seasonal(url: str, timeout_s: float = DEFAULT_TIMEOUT_S, opener=None) -> dict:
    """Fetch and parse the dashboard's phenology JSON.

    `opener` exists so tests can inject a fake `urlopen`. Production passes
    None and uses the stdlib opener directly. Returns the decoded JSON; the
    caller decides how to interpret missing fields.
    """
    req = Request(url, headers={"User-Agent": "centennial-farm-bot/1.0"})
    open_fn = opener or urlopen
    with open_fn(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


# --- query helpers ---------------------------------------------------------


def _normalize_block_query(query: str) -> str:
    """Lowercase and squash whitespace so 'Johnston  Block  1' matches 'johnston block 1'."""
    return " ".join((query or "").lower().split())


def find_block(payload: dict, query: str) -> Optional[dict]:
    """Locate a single block matching the user's free-form query.

    Match priority (each step short-circuits):
      1. Exact (case-insensitive) match on `block` name.
      2. Suffix match on `block` name — "Block 1" matches "Johnston Block 1"
         iff exactly one block ends with that suffix. Ambiguous → None,
         which the caller turns into a "be more specific" hint.
      3. fieldId match (numeric only) — last-ditch power-user lookup.
    """
    if not isinstance(payload, dict):
        return None
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None

    q = _normalize_block_query(query)
    if not q:
        return None

    # 1. Exact case-insensitive match on the full block name.
    for b in blocks:
        if isinstance(b, dict) and _normalize_block_query(b.get("block", "")) == q:
            return b

    # 2. Suffix match — "block 4" → "Johnston Block 4". Skip the leading
    #    ranch name on the user's side; require uniqueness on the data side.
    suffix_hits = [
        b for b in blocks
        if isinstance(b, dict)
        and _normalize_block_query(b.get("block", "")).endswith(q)
    ]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    # If the suffix is ambiguous we deliberately bail — better to ask the
    # operator to disambiguate than to silently pick the wrong block.

    # 3. Numeric fieldId fallback. Only fires for pure-digit queries so a
    #    user typing "block 1" doesn't accidentally hit fieldId=1.
    if q.isdigit():
        try:
            target = int(q)
        except ValueError:
            target = None
        if target is not None:
            for b in blocks:
                if isinstance(b, dict) and b.get("fieldId") == target:
                    return b
    return None


# --- formatting ------------------------------------------------------------


def _fmt_num(v, suffix: str = "", decimals: int = 1) -> str:
    if isinstance(v, (int, float)):
        return f"{v:.{decimals}f}{suffix}"
    return "—"


def _crop_pest_summary(blocks: list, degree_days: dict) -> list:
    """Group blocks by (crop, pestModelKey) and emit one summary line each.

    Each line shows the crop (with block count + acres) and the cumulative
    DDF for that pest model. This collapses 45 blocks into 2-4 readable
    lines — usually one peach line and one almond line.
    """
    if not isinstance(blocks, list):
        return []
    groups: dict = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        crop = b.get("crop") or "—"
        pest_key = b.get("pestModelKey") or ""
        key = (crop, pest_key)
        bucket = groups.setdefault(
            key,
            {"count": 0, "acres": 0.0, "pest_name": None, "ddf": None},
        )
        bucket["count"] += 1
        try:
            bucket["acres"] += float(b.get("acres") or 0)
        except (TypeError, ValueError):
            pass
        pm = b.get("pestModel") or {}
        if bucket["pest_name"] is None and isinstance(pm, dict):
            bucket["pest_name"] = pm.get("pest")
            bucket["ddf"] = pm.get("cumulativeDDF")

    # Fall back to the top-level degreeDays section if a block was missing
    # its embedded pestModel (defensive — real payloads include it).
    dd = degree_days if isinstance(degree_days, dict) else {}

    lines = []
    for (crop, pest_key), v in sorted(groups.items(), key=lambda kv: -kv[1]["acres"]):
        pest_name = v["pest_name"]
        ddf = v["ddf"]
        if pest_name is None and pest_key in dd and isinstance(dd[pest_key], dict):
            pest_name = dd[pest_key].get("pest")
            ddf = dd[pest_key].get("cumulativeDDF")
        pest_label = pest_name or pest_key or "—"
        lines.append(
            f"• {crop}: {v['count']} blocks ({_fmt_num(v['acres'], ' ac', 0)})"
            f" — {pest_label}: {_fmt_num(ddf, ' DDF')}"
        )
    return lines


def _block_highlight_lines(blocks: list, limit: int = MAX_BLOCK_HIGHLIGHTS) -> list:
    """Pick a few representative blocks (largest by acres) and one-line each.

    Largest blocks are the most operationally relevant — biggest spray
    surface, biggest harvest impact. Capped to keep the message phone-sized.
    """
    if not isinstance(blocks, list):
        return []
    valid = [b for b in blocks if isinstance(b, dict)]

    def acres_key(b):
        try:
            return -float(b.get("acres") or 0)
        except (TypeError, ValueError):
            return 0

    top = sorted(valid, key=acres_key)[:limit]
    lines = []
    for b in top:
        pm = b.get("pestModel") or {}
        ddf = pm.get("cumulativeDDF") if isinstance(pm, dict) else None
        chill = b.get("chillPortions")
        lines.append(
            f"• {b.get('block', '—')} ({_fmt_num(b.get('acres'), ' ac', 0)},"
            f" {b.get('variety', '—')}) —"
            f" chill {_fmt_num(chill, '')}, DDF {_fmt_num(ddf, '')}"
        )
    return lines


def _chill_line(payload: dict) -> str:
    """One-line chill portions summary including range across blocks."""
    chill = payload.get("chill") or {}
    season = chill.get("season") or {}
    portions = chill.get("portions")

    blocks = payload.get("blocks") or []
    block_chill = [
        b.get("chillPortions") for b in blocks
        if isinstance(b, dict) and isinstance(b.get("chillPortions"), (int, float))
    ]
    if block_chill:
        lo, hi = min(block_chill), max(block_chill)
        if abs(hi - lo) < 0.01:
            range_str = f"{lo:.2f} (uniform across {len(block_chill)} blocks)"
        else:
            range_str = f"{lo:.2f}–{hi:.2f} across {len(block_chill)} blocks"
    elif isinstance(portions, (int, float)):
        range_str = f"{portions:.2f}"
    else:
        range_str = "—"

    season_str = ""
    start, end = season.get("start"), season.get("end")
    if start and end:
        season_str = f" (season {start} → {end})"
    return f"❄️ Chill portions: {range_str}{season_str}"


def _degree_day_lines(payload: dict) -> list:
    """Render each top-level pest degree-day model as one summary line."""
    dd = payload.get("degreeDays")
    if not isinstance(dd, dict):
        return []
    out = []
    for _key, model in dd.items():
        if not isinstance(model, dict):
            continue
        pest = model.get("pest") or "—"
        ddf = model.get("cumulativeDDF")
        biofix = model.get("biofix")
        window_end = model.get("windowEnd")
        lo, hi = model.get("lowerF"), model.get("upperF")
        range_str = ""
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            range_str = f", {lo:.0f}–{hi:.0f}°F"
        window_str = ""
        if biofix and window_end:
            window_str = f" (biofix {biofix} → {window_end})"
        out.append(
            f"• {pest}: {_fmt_num(ddf, ' DDF')}{range_str}{window_str}"
        )
    return out


def format_summary(payload: dict, dashboard_url: Optional[str] = None) -> str:
    """Render a concise Telegram-friendly Markdown summary.

    Defensive against missing/extra fields — the dashboard's JSON shape may
    grow over time, and the bot must remain useful even on a partial payload.
    """
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    metadata = metadata or {}
    if metadata.get("available") is False:
        return (
            "📅 *Seasonal model* — data not yet available.\n\n"
            "The dashboard hasn't published a phenology snapshot yet. "
            "Try again later or check the dashboard directly."
        )

    station = metadata.get("station") or {}
    station_name = station.get("name") or "—"
    station_id = station.get("id")
    station_str = f"{station_name}"
    if station_id:
        station_str += f" (CIMIS #{station_id})"

    today_local = metadata.get("todayLocal")
    generated_at = metadata.get("generatedAt")
    when_bits = []
    if today_local:
        when_bits.append(f"local {today_local}")
    if generated_at:
        when_bits.append(f"generated {generated_at}")
    when_str = " · ".join(when_bits) if when_bits else "—"

    lines = [
        "📅 *Seasonal model — Centennial Farming*",
        f"Station: {station_str}",
        f"Updated: {when_str}",
        "",
        _chill_line(payload),
    ]

    dd_lines = _degree_day_lines(payload)
    if dd_lines:
        lines.append("")
        lines.append("🐛 *Insect degree days:*")
        lines.extend(dd_lines)

    blocks = payload.get("blocks") or []
    crop_lines = _crop_pest_summary(blocks, payload.get("degreeDays") or {})
    if crop_lines:
        lines.append("")
        lines.append("🌳 *By crop / pest:*")
        lines.extend(crop_lines)

    highlight_lines = _block_highlight_lines(blocks)
    if highlight_lines:
        lines.append("")
        lines.append(f"📍 *Block highlights (top {len(highlight_lines)} by acres):*")
        lines.extend(highlight_lines)

    if dashboard_url:
        lines.append("")
        lines.append(f"_See full dashboard: {dashboard_url}_")

    lines.append("")
    lines.append(
        "_Decision-support only — confirm with UC IPM and your PCA before "
        "scheduling sprays._"
    )
    return "\n".join(lines)


def format_block(block: dict, payload: dict, dashboard_url: Optional[str] = None) -> str:
    """Render a single-block detail card.

    Used when the user types `/seasonal Block 4` (or similar). Includes the
    same caveat footer as the full summary so a single-block reply is never
    less safe than the all-blocks reply.
    """
    if not isinstance(block, dict):
        return "⚠️ No matching block found."
    name = block.get("block") or "—"
    ranch = block.get("ranch")
    crop = block.get("crop") or "—"
    variety = block.get("variety") or "—"
    acres = block.get("acres")
    chill = block.get("chillPortions")
    pm = block.get("pestModel") or {}
    pest = pm.get("pest") if isinstance(pm, dict) else None
    ddf = pm.get("cumulativeDDF") if isinstance(pm, dict) else None
    biofix = pm.get("biofix") if isinstance(pm, dict) else None
    window_end = pm.get("windowEnd") if isinstance(pm, dict) else None
    lo, hi = (
        (pm.get("lowerF"), pm.get("upperF"))
        if isinstance(pm, dict) else (None, None)
    )

    metadata = (payload or {}).get("metadata") or {}
    today_local = metadata.get("todayLocal")
    station = metadata.get("station") or {}
    station_id = station.get("id")
    station_name = station.get("name") or "—"

    lines = [
        f"📅 *{name}*",
        f"{ranch + ' · ' if ranch else ''}{crop} · {variety} · "
        f"{_fmt_num(acres, ' ac', 0)}",
        "",
        f"❄️ Chill portions: {_fmt_num(chill, '', 2)}",
    ]
    if pest:
        range_str = ""
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            range_str = f", {lo:.0f}–{hi:.0f}°F"
        window_str = ""
        if biofix and window_end:
            window_str = f" (biofix {biofix} → {window_end})"
        lines.append(f"🐛 {pest}: {_fmt_num(ddf, ' DDF')}{range_str}{window_str}")

    if today_local or station_id:
        meta_bits = []
        if station_id:
            meta_bits.append(f"CIMIS #{station_id} {station_name}")
        if today_local:
            meta_bits.append(f"local {today_local}")
        lines.append("")
        lines.append("_" + " · ".join(meta_bits) + "_")

    if dashboard_url:
        lines.append(f"_See full dashboard: {dashboard_url}_")

    lines.append(
        "_Decision-support only — confirm with UC IPM and your PCA before "
        "scheduling sprays._"
    )
    return "\n".join(lines)


# --- end-to-end -----------------------------------------------------------


def get_seasonal_text(query: Optional[str] = None, opener=None) -> str:
    """End-to-end: read config, fetch, render. Never raises.

    A seasonal command that crashes the bot is worse than one that admits
    the dashboard is down. All network and parse errors are caught and
    returned as a one-line operator message.
    """
    cfg = get_config()
    url = cfg["url"]
    if not url:
        return (
            "⚠️ Seasonal data is not configured.\n\n"
            "Ask an admin to set DASHBOARD_URL (or SEASONAL_URL) on the bot service."
        )

    try:
        payload = fetch_seasonal(url, cfg["timeout_s"], opener=opener)
    except (HTTPError, URLError, TimeoutError) as e:
        return (
            "⚠️ Seasonal data is unavailable right now "
            f"({type(e).__name__}). Try again in a minute."
        )
    except OSError as e:
        return (
            "⚠️ Seasonal data is unavailable right now "
            f"(network error: {e}). Try again in a minute."
        )
    except (ValueError, json.JSONDecodeError):
        return "⚠️ Seasonal data returned an unreadable response. Try again."

    try:
        if query and query.strip():
            block = find_block(payload, query)
            if block is None:
                return (
                    f"⚠️ No block matched '{query.strip()}'. Try the full label "
                    "(e.g. 'Johnston Block 1') or just '/seasonal' for the "
                    "all-block summary."
                )
            return format_block(block, payload, cfg.get("dashboard_url"))
        return format_summary(payload, cfg.get("dashboard_url"))
    except Exception as e:  # pragma: no cover - defensive
        return f"⚠️ Could not format seasonal summary ({type(e).__name__})."
