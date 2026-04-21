import os
import json
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DASHBOARD_URL = "https://centennial-farming-dashboard.onrender.com"   # ← REPLACE WITH YOUR REAL DASHBOARD URL

# SQLite for scalability (handles thousands of acres)
DB_FILE = "farm_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS harvest (
                    date TEXT, field_id TEXT, variety TEXT, bins INTEGER, worker TEXT DEFAULT "Owner")''')
    conn.commit()
    conn.close()

init_db()

def get_total_acres():
    with open("fields_map.json", "r") as f:
        fields = json.load(f)["fields"]
    return round(sum(float(f.get("acres", 0)) for f in fields), 1)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌳 **Advanced Peach & Almond Farm Manager Bot** is now ONLINE\n\n"
        "Commands:\n"
        "/dashboard → Client-ready interactive map\n"
        "/map → List all fields\n"
        "/acres → Total acreage\n"
        "/report → Season summary\n\n"
        "Just type naturally: \"Field 5 18 bins Kaweah\" or \"Block 17 picked 12 bins today\""
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🍑 **Professional Farm Map**\n\n{DASHBOARD_URL}\n\nShare this link with clients or buyers.")

async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with open("fields_map.json", "r") as f:
        fields = json.load(f)["fields"]
    msg = "📍 **Your 45 Fields**\n\n"
    for f in fields:
        msg += f"Field {f['id']} — {f['name']} — {f['variety']} — {f.get('acres',0)} acres\n"
    await update.message.reply_text(msg)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT SUM(bins) FROM harvest")
    total = c.fetchone()[0] or 0
    conn.close()
    await update.message.reply_text(f"📊 **Season Total**: {total} bins logged\nCost-per-ton calculator coming next phase.")

async def total_acres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acres = get_total_acres()
    await update.message.reply_text(f"🌳 You farm **{acres} acres** total.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Smart harvest logging
    with open("fields_map.json", "r") as f:
        fields = json.load(f)["fields"]
    
    entries = []
    lower = text.lower()
    for field in fields:
        fid = field["id"]
        if f"field {fid}" in lower or f"block {fid}" in lower or fid in lower:
            import re
            bins_match = re.search(r'(\d+)\s*bin', lower)
            bins = int(bins_match.group(1)) if bins_match else 0
            entries.append((today, fid, field["variety"], bins))
    
    if entries:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.executemany("INSERT INTO harvest VALUES (?,?,?,?)", entries)
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ **Saved!** {len(entries)} harvest entry(ies) logged.")
        return

    # Fallback
    if "acre" in lower or "how many acres" in lower:
        acres = get_total_acres()
        await update.message.reply_text(f"🌳 You currently farm **{acres} acres**.")
    else:
        await update.message.reply_text("Got it! Try /dashboard, /acres, or log a harvest like 'Field 5 18 bins'.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("map", show_map))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("acres", total_acres))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Advanced Farm Manager Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
