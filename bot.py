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
DASHBOARD_URL = "https://centennial-farming-map.onrender.com"   # ← your real map URL

DB_FILE = "farm_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS harvest (
                    date TEXT, field_id TEXT, variety TEXT, bins INTEGER)''')
    conn.commit()
    conn.close()

init_db()

def load_fields():
    with open("fields_map.json", "r") as f:
        return json.load(f)["fields"]

def get_total_acres():
    fields = load_fields()
    return round(sum(float(f.get("acres", 0)) for f in fields), 1)

def get_acres_by_blocks_and_variety(block_list=None, variety_filter=None):
    fields = load_fields()
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Centennial Farming Advanced Bot** is LIVE!\n\n"
        "Commands:\n"
        "/dashboard → Client map\n"
        "/weather → 3 localized reports\n"
        "/acres → Total or specific blocks\n"
        "/payroll → Full cost & payroll breakdown\n"
        "/report → Season summary\n\n"
        "Natural examples:\n"
        "“tell me how many acres of peaches and almonds are in blocks 66,77,18,2”\n"
        "“Field 5 18 bins”"
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🍑 **Centennial Farming Company Map**\n\n{DASHBOARD_URL}")

async def payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT SUM(bins) FROM harvest")
    total_bins = c.fetchone()[0] or 0
    conn.close()
    
    worker_pay = total_bins * 30
    your_cost = round(worker_pay * 1.35, 2)
    tons = total_bins / 2.0  # assuming 1000 lbs per bin = 0.5 ton
    cost_per_ton = round(your_cost / tons, 2) if tons > 0 else 0
    
    await update.message.reply_text(
        f"💰 **Payroll & Cost Snapshot**\n\n"
        f"Total bins logged: {total_bins}\n"
        f"Worker piece-rate pay: **${worker_pay:,}**\n"
        f"Your total cost (35% commission): **${your_cost:,}**\n"
        f"**Cost per ton: ${cost_per_ton}**\n\n"
        f"Updated live as you log bins."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    fields = load_fields()

    # Smart acreage parsing
    block_matches = re.findall(r'block\s*(\d+)', text) or re.findall(r'\b(\d{1,2})\b', text)
    block_list = [int(b) for b in block_matches if b.isdigit()]

    variety_filter = None
    if any(w in text for w in ["peach", "peaches"]):
        variety_filter = "peach"
    elif any(w in text for w in ["almond", "almonds"]):
        variety_filter = "almond"

    if block_list or variety_filter or "acre" in text or "acres" in text:
        acres = get_acres_by_blocks_and_variety(block_list, variety_filter)
        if block_list:
            blocks_str = ", ".join(str(b) for b in block_list)
            variety_str = f" of {variety_filter}s" if variety_filter else ""
            await update.message.reply_text(f"🌳 **Blocks {blocks_str}**{variety_str}: **{acres} acres**")
        else:
            await update.message.reply_text(f"🌳 Total requested acres: **{acres} acres**")
        return

    # Harvest logging
    entries = []
    for field in fields:
        fid = field["id"]
        if f"field {fid}" in text or f"block {fid}" in text or fid in text:
            bins_match = re.search(r'(\d+)\s*bin', text)
            bins = int(bins_match.group(1)) if bins_match else 0
            entries.append((today, fid, field["variety"], bins))

    if entries:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.executemany("INSERT INTO harvest VALUES (?,?,?,?)", entries)
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ **Logged!** {len(entries)} harvest entry(ies) saved.")
        return

    await update.message.reply_text("Got it! Try /payroll, /dashboard, or log harvest like 'Field 5 18 bins'.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("payroll", payroll))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Centennial Farming Bot with cost-per-ton payroll is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
