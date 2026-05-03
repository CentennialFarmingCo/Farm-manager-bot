import os
import json
import sqlite3
import re
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Default points at the live Vercel deployment. Override DASHBOARD_URL to
# point at a preview, staging, or replacement host without redeploying.
DEFAULT_DASHBOARD_URL = "https://centennial-farm-dashboard-qvytatulr.vercel.app"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", DEFAULT_DASHBOARD_URL)


def normalize_dashboard_url(raw):
    """Return an http(s) URL string, or None if the input cannot be salvaged.

    Telegram's inline URL buttons reject non-http(s) URLs and bare hostnames,
    so we coerce a missing scheme to https and reject anything still missing
    a host after that.
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

DB_FILE = os.getenv("FARM_DB_FILE", "farm_data.db")
FIELDS_FILE = os.getenv("FARM_FIELDS_FILE", "fields_map.json")

# Block labels can be plain numbers ("4", "39"), suffixed ("36A", "5B"),
# or composite ("56/58", "8/9"). Numeric ids in fields_map.json are internal
# storage keys that may NOT equal the human block label, so user-facing
# matching uses block_label (derived from "name" when not stored explicitly).
_BLOCK_LABEL_RE = re.compile(r'block\s+([0-9]+[a-z]?(?:/[0-9]+)?)', re.IGNORECASE)


def _derive_block_label(name: str):
    if not name:
        return None
    m = _BLOCK_LABEL_RE.search(name)
    return m.group(1).upper() if m else None


def init_db(db_file: str = None) -> None:
    path = db_file or DB_FILE
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        c.execute(
            '''CREATE TABLE IF NOT EXISTS harvest (
                    date TEXT, field_id TEXT, variety TEXT, bins INTEGER)'''
        )
        conn.commit()
    finally:
        conn.close()


def load_fields(fields_file: str = None):
    with open(fields_file or FIELDS_FILE, "r") as f:
        fields = json.load(f)["fields"]
    for fld in fields:
        if not fld.get("block_label"):
            derived = _derive_block_label(fld.get("name", ""))
            if derived is not None:
                fld["block_label"] = derived
        elif fld.get("block_label"):
            fld["block_label"] = str(fld["block_label"]).upper()
    return fields


def get_total_acres(fields=None):
    fields = fields if fields is not None else load_fields()
    return round(sum(float(f.get("acres", 0)) for f in fields), 1)


def _field_matches_block(field, requested):
    """Match a requested block label against a field's human block_label.

    block_label is the label printed on the map and used by foremen
    ("4", "36A", "66"). It is derived from the field name when not stored.
    Internal numeric `id` is a stable storage key that may diverge from
    block_label and MUST NOT be used for user-facing matching, since e.g.
    id=5 happens to be "Johnston Block 4". As a last-resort fallback (only
    when a field has no block_label at all, e.g. a synthetic test fixture),
    fall back to id-equality so legacy callers keep working.
    """
    requested_str = str(requested).upper()
    label = field.get("block_label")
    if label is None:
        label = _derive_block_label(field.get("name", ""))
    if label is not None:
        return str(label).upper() == requested_str
    return str(field.get("id", "")).upper() == requested_str


def get_acres_by_blocks_and_variety(block_list=None, variety_filter=None, fields=None):
    fields = fields if fields is not None else load_fields()
    total = 0
    for f in fields:
        variety = f.get("variety", "").lower()
        acres = float(f.get("acres", 0))
        block_match = True
        if block_list:
            block_match = any(_field_matches_block(f, b) for b in block_list)
        variety_match = True
        if variety_filter:
            if variety_filter == "peach":
                variety_match = "peach" in variety
            elif variety_filter == "almond":
                variety_match = "almond" in variety
        if block_match and variety_match:
            total += acres
    return round(total, 1)


_BLOCK_REF_RE = re.compile(
    r'\b(?:block|field)\s+([0-9]+[a-z]?(?:/[0-9]+)?)\b',
    re.IGNORECASE,
)


def _find_block_refs(text):
    """Find explicit block/field references in text, preserving order.

    Returns a list of normalized labels (uppercased), e.g. ["4", "36A"].
    Only matches when preceded by 'block' or 'field' (with word boundaries),
    so bare numbers like "18 bins" are never treated as block refs.
    """
    return [m.group(1).upper() for m in _BLOCK_REF_RE.finditer(text)]


def parse_message(text: str, fields):
    """Pure parser. Returns one of:
      {"kind": "acreage", "blocks": [str...], "variety": "peach"|"almond"|None}
      {"kind": "harvest", "entries": [(date, fid, variety, bins), ...]}
      {"kind": "ambiguous", "reason": str}
      {"kind": "unknown"}
    """
    text_lc = text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    is_harvest_log = bool(re.search(r'\d+\s*bin', text_lc))

    variety_filter = None
    if any(w in text_lc for w in ["peach", "peaches"]):
        variety_filter = "peach"
    elif any(w in text_lc for w in ["almond", "almonds"]):
        variety_filter = "almond"

    if is_harvest_log:
        # Span-aware scan so we can detect when the SAME number is being read
        # both as the block ref and as the bin count (e.g. "field 18 bins").
        block_ref_matches = list(_BLOCK_REF_RE.finditer(text_lc))
        bin_matches = list(re.finditer(r'(\d+)\s*bin', text_lc))

        # Multiple distinct bin counts → ambiguous. We require exactly one.
        bin_numbers = [m.group(1) for m in bin_matches]
        if len(set(bin_numbers)) > 1:
            return {
                "kind": "ambiguous",
                "reason": (
                    "Multiple bin counts found. Please send one harvest "
                    "entry per message, e.g. 'Block 4 18 bins'."
                ),
            }

        # If the only block ref's number is the same character span the
        # `\d+ bins` pattern is reading, the user almost certainly meant to
        # name a block separately, e.g. "field 18 bins" with no real label.
        ambiguous_dual_use = False
        if block_ref_matches and bin_matches:
            for bm in bin_matches:
                num_start = bm.start(1)
                for rm in block_ref_matches:
                    if rm.start(1) == num_start:
                        ambiguous_dual_use = True
                        break
                if ambiguous_dual_use:
                    break
        if ambiguous_dual_use and len(block_ref_matches) == 1:
            return {
                "kind": "ambiguous",
                "reason": (
                    "I couldn't tell which field/block this is for "
                    "(the number reads as both the block and the bin "
                    "count). Try 'Block 4 18 bins' or 'Block 36A 18 bins'."
                ),
            }

        block_refs = [m.group(1).upper() for m in block_ref_matches]
        if not block_refs:
            return {
                "kind": "ambiguous",
                "reason": (
                    "I couldn't tell which field/block this is for. Please "
                    "include a block label, e.g. 'Block 4 18 bins' or "
                    "'Block 36A 18 bins'."
                ),
            }

        bins = int(bin_numbers[0]) if bin_numbers else 0
        # Resolve every referenced label against fields by block_label or id.
        entries = []
        unresolved = []
        seen_ids = set()
        for ref in block_refs:
            matched = None
            for fld in fields:
                if _field_matches_block(fld, ref):
                    matched = fld
                    break
            if matched is None:
                unresolved.append(ref)
                continue
            fid = str(matched["id"])
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            entries.append((today, fid, matched.get("variety", ""), bins))

        if unresolved:
            return {
                "kind": "ambiguous",
                "reason": (
                    f"I don't recognize block label(s): {', '.join(unresolved)}. "
                    "Please double-check and resend."
                ),
            }
        if not entries:
            return {
                "kind": "ambiguous",
                "reason": "No matching fields found for the referenced blocks.",
            }
        return {"kind": "harvest", "entries": entries}

    # Non-harvest (acreage) path: tolerate bare numbers like "blocks 66,18,2".
    block_refs = _find_block_refs(text_lc)
    if not block_refs:
        # Allow bare-number block lists only when the message is clearly an
        # acreage query (mentions "block(s)" or a variety or "acre").
        if "block" in text_lc:
            # Strip word "block(s)" prefix and read numbers/labels after.
            tail = re.search(r'blocks?\s+([0-9a-z,\s/]+)', text_lc)
            if tail:
                block_refs = [
                    tok.strip().upper()
                    for tok in re.split(r'[,\s]+', tail.group(1))
                    if tok.strip() and re.match(r'^[0-9]+[a-z]?(?:/[0-9]+)?$', tok.strip())
                ]

    if block_refs or variety_filter or "acre" in text_lc:
        return {
            "kind": "acreage",
            "blocks": block_refs,
            "variety": variety_filter,
        }
    return {"kind": "unknown"}


def insert_harvest(entries, db_file: str = None) -> None:
    if not entries:
        return
    conn = sqlite3.connect(db_file or DB_FILE)
    try:
        c = conn.cursor()
        c.executemany("INSERT INTO harvest VALUES (?,?,?,?)", entries)
        conn.commit()
    finally:
        conn.close()


def total_bins(db_file: str = None) -> int:
    conn = sqlite3.connect(db_file or DB_FILE)
    try:
        c = conn.cursor()
        c.execute("SELECT SUM(bins) FROM harvest")
        return c.fetchone()[0] or 0
    finally:
        conn.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Centennial Farming Advanced Bot** is LIVE!\n\n"
        "Commands:\n"
        "/dashboard → Client map\n"
        "/payroll → Full cost & payroll breakdown\n\n"
        "Natural examples:\n"
        "“tell me how many acres of peaches and almonds are in blocks 66,77,18,2”\n"
        "“Block 4 18 bins” or “Block 36A 18 bins”"
    )


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = normalize_dashboard_url(DASHBOARD_URL)
    if url is None:
        await update.message.reply_text(
            "⚠️ Dashboard link is not configured.\n\n"
            "Ask an admin to set the DASHBOARD_URL environment variable on "
            "the bot service (e.g. https://your-dashboard.example.com)."
        )
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🌳 Open Centennial Farm Dashboard", url=url)]]
    )
    await update.message.reply_text(
        "🍑 *Centennial Farming Company Dashboard*\n\n"
        "Live field map, varieties, and harvest snapshot.\n"
        "Tap the button below to open it in your browser.\n\n"
        f"Direct link: {url}",
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )


async def payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bins = total_bins()
    worker_pay = bins * 30
    your_cost = round(worker_pay * 1.35, 2)
    tons = bins / 2.0  # 1000 lbs per bin = 0.5 ton
    cost_per_ton = round(your_cost / tons, 2) if tons > 0 else 0

    await update.message.reply_text(
        f"💰 **Payroll & Cost Snapshot**\n\n"
        f"Total bins logged: {bins}\n"
        f"Worker piece-rate pay: **${worker_pay:,}**\n"
        f"Your total cost (35% commission): **${your_cost:,}**\n"
        f"**Cost per ton: ${cost_per_ton}**\n\n"
        f"Updated live as you log bins."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = load_fields()
    parsed = parse_message(update.message.text, fields)

    if parsed["kind"] == "acreage":
        block_list = parsed["blocks"]
        variety_filter = parsed["variety"]
        acres = get_acres_by_blocks_and_variety(block_list, variety_filter, fields=fields)
        if block_list:
            blocks_str = ", ".join(str(b) for b in block_list)
            variety_str = f" of {variety_filter}s" if variety_filter else ""
            await update.message.reply_text(
                f"🌳 **Blocks {blocks_str}**{variety_str}: **{acres} acres**"
            )
        else:
            await update.message.reply_text(f"🌳 Total requested acres: **{acres} acres**")
        return

    if parsed["kind"] == "harvest":
        insert_harvest(parsed["entries"])
        await update.message.reply_text(
            f"✅ **Logged!** {len(parsed['entries'])} harvest entry(ies) saved."
        )
        return

    if parsed["kind"] == "ambiguous":
        await update.message.reply_text(f"⚠️ {parsed['reason']}")
        return

    await update.message.reply_text(
        "Got it! Try /payroll, /dashboard, or log harvest like 'Block 4 18 bins'."
    )


def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and set the token."
        )
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("payroll", payroll))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Centennial Farming Bot with cost-per-ton payroll is running!")
    app.run_polling()


if __name__ == "__main__":
    main()
