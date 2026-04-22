import os
import json
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DASHBOARD_URL = "https://YOUR-DASHBOARD-URL.onrender.com"   # ← Replace with your real map URL

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

def get_acres_by_owner(owner):
    fields = load_fields()
    owner = owner.lower()
    filtered = [f for f in fields if owner in f["name"].lower()]
    return round(sum(float(f.get("acres", 0)) for f in filtered), 1)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Advanced Centennial Farming Bot** is LIVE!\n\n"
        "Commands:\n"
        "/dashboard → Client map\n"
        "/map → All fields\n"
        "/acres → Total or owner acres (try /acres Fagundes)\n"
        "/report → Season summary\n"
        "/payroll → Cost breakdown\n\n"
        "Just type: \"how many acres for Fagundes\" or \"Johnston total acres\""
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🍑 **Centennial Farming Company Map**\n\n{DASHBOARD_URL}")

async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = load_fields()
    msg = "📍 **Your Fields**\n\n"
    for f in fields:
        msg += f"Field {f['id']} — {f['name']} — {f['variety']} — {f.get('acres',0)} acres\n"
    await update.message.reply_text(msg)

async def total_acres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if user specified an owner
    args = context.args
    if args:
        owner = " ".join(args).lower()
        if any(x in owner for x in ["fagundes", "johnston", "blue lupin"]):
            acres = get_acres_by_owner(owner)
            await update.message.reply_text(f"🌳 **{owner.title()}** acres: **{acres} acres**")
            return
    # Default to total
    acres = get_total_acres()
    await update.message.reply_text(f"🌳 You farm **{acres} acres** total across all ownerships.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT SUM(bins) FROM harvest")
    total_bins = c.fetchone()[0] or 0
    conn.close()
    await update.message.reply_text(f"📊 **Season Summary**\nTotal bins logged: {total_bins}")

async def payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT SUM(bins) FROM harvest")
    total_bins = c.fetchone()[0] or 0
    conn.close()
    worker_pay = total_bins * 30
    your_cost = round(worker_pay * 1.35, 2)
    await update.message.reply_text(f"💰 **Payroll Snapshot**\nWorker piece-rate: ${worker_pay}\nYour total cost (with 35% commission): ${your_cost}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    fields = load_fields()

    # Smart owner detection for natural questions
    owner_keywords = {
        "fagundes": "Fagundes",
        "johnston": "Johnston",
        "blue lupin": "Blue Lupin",
        "blue lupin": "Blue Lupin"
    }
    detected_owner = None
    for key, name in owner_keywords.items():
        if key in text:
            detected_owner = name
            break

    if detected_owner and ("acre" in text or "acres" in text):
        acres = get_acres_by_owner(detected_owner)
        await update.message.reply_text(f"🌳 **{detected_owner}** ownership: **{acres} acres**")
        return

    # Harvest logging
    entries = []
    for field in fields:
        fid = field["id"]
        if f"field {fid}" in text or f"block {fid}" in text or fid in text:
            import re
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

    # Fallback
    await update.message.reply_text("Got it! Try /dashboard, /acres Fagundes, /payroll, or log harvest like 'Field 5 18 bins'.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("map", show_map))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("payroll", payroll))
    app.add_handler(CommandHandler("acres", total_acres))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Advanced Centennial Farming Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
