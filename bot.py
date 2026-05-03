import os
import json
import sqlite3
import re
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DASHBOARD_URL = os.getenv(
    "DASHBOARD_URL",
    "https://centennial-farming-map.onrender.com",
)

DB_FILE = os.getenv("FARM_DB_FILE", "farm_data.db")
FIELDS_FILE = os.getenv("FARM_FIELDS_FILE", "fields_map.json")


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
        return json.load(f)["fields"]


def get_total_acres(fields=None):
    fields = fields if fields is not None else load_fields()
    return round(sum(float(f.get("acres", 0)) for f in fields), 1)


def get_acres_by_blocks_and_variety(block_list=None, variety_filter=None, fields=None):
    fields = fields if fields is not None else load_fields()
    total = 0
    for f in fields:
        fid = f["id"]
        variety = f.get("variety", "").lower()
        acres = float(f.get("acres", 0))
        block_match = True
        if block_list:
            block_match = any(str(fid) == str(b) for b in block_list)
        variety_match = True
        if variety_filter:
            if variety_filter == "peach":
                variety_match = "peach" in variety
            elif variety_filter == "almond":
                variety_match = "almond" in variety
        if block_match and variety_match:
            total += acres
    return round(total, 1)


def parse_message(text: str, fields):
    """Pure parser. Returns one of:
      {"kind": "acreage", "blocks": [int...], "variety": "peach"|"almond"|None}
      {"kind": "harvest", "entries": [(date, fid, variety, bins), ...]}
      {"kind": "unknown"}
    """
    text = text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    is_harvest_log = bool(re.search(r'\d+\s*bin', text))

    if is_harvest_log:
        block_matches = re.findall(r'block\s*(\d+)', text) + re.findall(r'field\s*(\d+)', text)
    else:
        block_matches = re.findall(r'block\s*(\d+)', text) or re.findall(r'\b(\d{1,2})\b', text)
    block_list = [int(b) for b in block_matches if b.isdigit()]

    variety_filter = None
    if any(w in text for w in ["peach", "peaches"]):
        variety_filter = "peach"
    elif any(w in text for w in ["almond", "almonds"]):
        variety_filter = "almond"

    if not is_harvest_log and (block_list or variety_filter or "acre" in text):
        return {"kind": "acreage", "blocks": block_list, "variety": variety_filter}

    entries = []
    bins_match = re.search(r'(\d+)\s*bin', text)
    bins = int(bins_match.group(1)) if bins_match else 0
    for field in fields:
        fid = str(field["id"])
        if re.search(rf'\b(?:field|block)\s*{re.escape(fid)}\b', text):
            entries.append((today, fid, field["variety"], bins))
    if entries:
        return {"kind": "harvest", "entries": entries}
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
        "“Field 5 18 bins”"
    )


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🍑 **Centennial Farming Company Map**\n\n{DASHBOARD_URL}")


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

    await update.message.reply_text(
        "Got it! Try /payroll, /dashboard, or log harvest like 'Field 5 18 bins'."
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
