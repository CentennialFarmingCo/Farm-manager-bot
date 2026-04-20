import os
import json
from datetime import datetime, time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Load field map
FIELDS_MAP = "fields_map.json"
DATA_FILE = "harvest_data.json"

def load_fields():
    if os.path.exists(FIELDS_MAP):
        with open(FIELDS_MAP, "r") as f:
            return json.load(f)["fields"]
    return []

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"season": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_total_acres():
    fields = load_fields()
    total = sum(float(field.get("acres", 0)) for field in fields)
    return round(total, 1)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌳 Full-Season Peach Bot is LIVE!\n\n"
        "Commands:\n"
        "/map → see all your 45 fields\n"
        "/report → season totals\n"
        "/acres → total acres you farm\n\n"
        "Send daily notes like: 'Field 5 12 bins' or 'Block 3 8 bins'"
    )

async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = load_fields()
    msg = "📍 YOUR FIELD MAP\n\n"
    for f in fields:
        msg += f"Field {f['id']} — {f['name']} — {f['variety']} — {f.get('acres', 0)} acres\n"
    await update.message.reply_text(msg)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    total_bins = sum(e.get("bins", 0) for day in data["season"] for e in day["entries"])
    await update.message.reply_text(f"📊 Season total so far: {total_bins} bins")

async def total_acres_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acres = get_total_acres()
    await update.message.reply_text(f"🌳 You currently farm **{acres} acres** across all 45 fields.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    fields = load_fields()

    # Try to log a harvest note
    entries = []
    for field in fields:
        fid = field["id"]
        if f"field {fid}" in text or f"block {fid}" in text or fid in text:
            import re
            bins_match = re.search(r'(\d+)\s*bin', text)
            bins = int(bins_match.group(1)) if bins_match else 0
            entries.append({
                "field_id": fid,
                "variety": field["variety"],
                "bins": bins
            })
    
    if entries:
        data = load_data()
        data["season"].append({"date": today, "entries": entries})
        save_data(data)
        await update.message.reply_text(f"✅ Saved! {len(entries)} entries logged for today.")
        return

    # Simple answers for common questions
    if "acre" in text or "how many acres" in text:
        acres = get_total_acres()
        await update.message.reply_text(f"🌳 You currently farm **{acres} acres**.")
    else:
        await update.message.reply_text("Got it! Try /acres, /map, or send a daily note like 'Field 5 12 bins'.")

# Automatic daily reminders
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text="🌅 Good morning! Ready for today's harvest? Send your daily note anytime.")

async def evening_summary(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text="🌙 Evening check-in: Bot is still running 24/7.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("map", show_map))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("acres", total_acres_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    job_queue: JobQueue = app.job_queue
    job_queue.run_daily(morning_reminder, time=time(7, 0))
    job_queue.run_daily(evening_summary, time=time(20, 0))
    
    print("Simple & stable peach bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
