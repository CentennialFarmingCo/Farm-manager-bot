"""
Harvest snapshot exporter.

Reads the bot's SQLite `harvest` table, builds a JSON snapshot, and (optionally)
commits it to the centennial-farm-dashboard repo so the dashboard's Harvest tab
can render up-to-date numbers.

Design notes:
- Pushing is best-effort: failures are logged but never raised back into the
  Telegram handler. The user's harvest log always succeeds first; the push is
  a side-effect run on a background thread.
- The push is rate-limited (one push per N seconds, default 30) to avoid
  hammering GitHub when several entries land back-to-back. If a push is
  skipped due to rate-limiting, the *next* push will include all the data
  accumulated in between.
- Credentials come from environment variables only. No PAT is ever logged.

Env vars (all optional except the PAT — without it, export silently no-ops):

    HARVEST_EXPORT_PAT          GitHub fine-grained PAT with Contents:Write
                                on the dashboard repo only.
    HARVEST_EXPORT_REPO         "owner/repo", default
                                "CentennialFarmingCo/centennial-farm-dashboard"
    HARVEST_EXPORT_PATH         Path in the repo, default "public/harvest.json"
    HARVEST_EXPORT_BRANCH       Branch to commit to, default "main"
    HARVEST_EXPORT_MIN_INTERVAL Seconds between pushes, default "30"
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_REPO = "CentennialFarmingCo/centennial-farm-dashboard"
DEFAULT_PATH = "public/harvest.json"
DEFAULT_BRANCH = "main"
DEFAULT_MIN_INTERVAL_SECONDS = 30

# Module-level rate-limit state. The lock protects both fields.
_push_lock = threading.Lock()
_last_push_at: float = 0.0


def _load_fields_map(fields_file: str) -> Dict[str, Dict[str, Any]]:
    """Return {field_id: {name, variety, acres, ...}} for joining."""
    try:
        with open(fields_file, "r") as f:
            data = json.load(f)
        return {str(f["id"]): f for f in data.get("fields", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning("harvest_export: could not load %s: %s", fields_file, e)
        return {}


def build_snapshot(db_file: str, fields_file: str = "fields_map.json") -> Dict[str, Any]:
    """Build the harvest snapshot dict from SQLite.

    Shape:
    {
      "generated_at": "2026-05-29T23:42:00Z",
      "entries": [
        {"date": "2026-05-29", "field_id": "5", "block": "Johnston Block 4",
         "variety": "Parade", "acres": 13, "bins": 18}, ...
      ],
      "per_day":   [{"date": "2026-05-29", "bins": 142}, ...],   # sorted asc
      "per_block": [{"field_id": "5", "block": "...", "acres": 13,
                     "bins": 36, "bins_per_acre": 2.77}, ...],   # sorted desc by bins
      "totals":    {"bins": 142, "entries": 8, "blocks": 5,
                    "first_date": "2026-05-29", "last_date": "2026-05-29"}
    }
    """
    fields = _load_fields_map(fields_file)
    rows: List[tuple] = []
    try:
        conn = sqlite3.connect(db_file)
        try:
            c = conn.cursor()
            c.execute(
                "SELECT date, field_id, variety, bins "
                "FROM harvest ORDER BY date ASC, rowid ASC"
            )
            rows = c.fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("harvest_export: SQLite read failed: %s", e)
        rows = []

    entries: List[Dict[str, Any]] = []
    per_day: Dict[str, int] = defaultdict(int)
    per_block_bins: Dict[str, int] = defaultdict(int)

    for date, field_id, variety, bins in rows:
        fid = str(field_id) if field_id is not None else ""
        bins_int = int(bins or 0)
        field = fields.get(fid, {})
        entries.append({
            "date": date,
            "field_id": fid,
            "block": field.get("name", ""),
            "variety": variety or field.get("variety", ""),
            "acres": field.get("acres"),
            "bins": bins_int,
        })
        if date:
            per_day[date] += bins_int
        if fid:
            per_block_bins[fid] += bins_int

    per_day_list = [
        {"date": d, "bins": per_day[d]} for d in sorted(per_day.keys())
    ]

    per_block_list: List[Dict[str, Any]] = []
    for fid, bins in per_block_bins.items():
        field = fields.get(fid, {})
        acres = field.get("acres")
        try:
            acres_f = float(acres) if acres is not None else None
        except (TypeError, ValueError):
            acres_f = None
        bins_per_acre = (bins / acres_f) if acres_f and acres_f > 0 else None
        per_block_list.append({
            "field_id": fid,
            "block": field.get("name", ""),
            "variety": field.get("variety", ""),
            "acres": acres_f,
            "bins": bins,
            "bins_per_acre": round(bins_per_acre, 2) if bins_per_acre is not None else None,
        })
    per_block_list.sort(key=lambda r: r["bins"], reverse=True)

    dates_seen = sorted(per_day.keys())
    snapshot = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": entries,
        "per_day": per_day_list,
        "per_block": per_block_list,
        "totals": {
            "bins": sum(per_day.values()),
            "entries": len(entries),
            "blocks": len(per_block_bins),
            "first_date": dates_seen[0] if dates_seen else None,
            "last_date": dates_seen[-1] if dates_seen else None,
        },
    }
    return snapshot


# --- GitHub push --------------------------------------------------------------

def _github_request(
    method: str,
    url: str,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Tiny GitHub REST helper. Raises urllib.error.HTTPError on non-2xx."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "centennial-farm-bot")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _get_existing_sha(repo: str, path: str, branch: str, token: str) -> Optional[str]:
    """Return the existing file's blob SHA, or None if the file doesn't exist."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    try:
        data = _github_request("GET", url, token)
        sha = data.get("sha")
        return sha if isinstance(sha, str) else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        # Anything else (401, 403, 5xx) — let the caller log and bail.
        raise


def push_snapshot_to_github(snapshot: Dict[str, Any]) -> bool:
    """Commit the snapshot JSON to the dashboard repo. Returns True on success.

    Never raises — all errors are logged and swallowed so the Telegram reply
    path is never affected.
    """
    token = os.getenv("HARVEST_EXPORT_PAT")
    if not token:
        logger.debug("harvest_export: HARVEST_EXPORT_PAT not set, skipping push")
        return False

    repo = os.getenv("HARVEST_EXPORT_REPO", DEFAULT_REPO)
    path = os.getenv("HARVEST_EXPORT_PATH", DEFAULT_PATH)
    branch = os.getenv("HARVEST_EXPORT_BRANCH", DEFAULT_BRANCH)

    try:
        sha = _get_existing_sha(repo, path, branch, token)
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning("harvest_export: GET contents failed: %s", _redact(e, token))
        return False

    content_bytes = json.dumps(snapshot, indent=2).encode("utf-8")
    payload: Dict[str, Any] = {
        "message": (
            f"chore(harvest): update snapshot "
            f"({snapshot['totals']['bins']} bins, "
            f"{snapshot['totals']['entries']} entries)"
        ),
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        _github_request("PUT", url, token, payload)
        logger.info(
            "harvest_export: pushed %d bins / %d entries to %s@%s:%s",
            snapshot["totals"]["bins"],
            snapshot["totals"]["entries"],
            repo, branch, path,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("harvest_export: PUT contents failed: %s", _redact(e, token))
        return False


def _redact(err: Exception, token: str) -> str:
    """Strip the PAT from error text in case it ever lands in a URL or body."""
    msg = str(err)
    if token and token in msg:
        msg = msg.replace(token, "***")
    return msg


# --- Public entry point -------------------------------------------------------

def _export_in_background(db_file: str, fields_file: str) -> None:
    """Worker run on a thread: build snapshot, respect rate-limit, push."""
    global _last_push_at
    now = time.monotonic()
    interval = float(
        os.getenv("HARVEST_EXPORT_MIN_INTERVAL", DEFAULT_MIN_INTERVAL_SECONDS)
    )
    with _push_lock:
        if now - _last_push_at < interval:
            logger.debug(
                "harvest_export: rate-limited (%.1fs since last push, need %.1fs)",
                now - _last_push_at, interval,
            )
            return
        _last_push_at = now

    try:
        snapshot = build_snapshot(db_file=db_file, fields_file=fields_file)
    except Exception as e:  # noqa: BLE001
        logger.warning("harvest_export: build_snapshot failed: %s", e)
        return

    push_snapshot_to_github(snapshot)


def export_after_harvest_log(
    db_file: str,
    fields_file: str = "fields_map.json",
) -> None:
    """Fire-and-forget: run the snapshot+push on a daemon thread.

    Safe to call from inside an async handler — never blocks, never raises.
    """
    if not os.getenv("HARVEST_EXPORT_PAT"):
        # Cheap early exit so unconfigured deployments don't spawn threads.
        return
    t = threading.Thread(
        target=_export_in_background,
        args=(db_file, fields_file),
        name="harvest-export",
        daemon=True,
    )
    t.start()
