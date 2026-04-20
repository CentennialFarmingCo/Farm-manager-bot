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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌳 Full-Season Peach Bot is now LIVE!\n\n"
        "Send daily notes like:\n"
        "Field 1 15 bins\n"
        "Block 2 8 bins Redhaven\n\n"
        "Commands:\n"
        "/map → see your fields\n"
        "/report → season totals"
    )

async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fields = load_fields()
    if not fields:
        await update.message.reply_text("No field map yet. We'll create one next!")
        return
    msg = "📍 YOUR FIELD MAP\n\n"
    for f in fields:
        msg += f"Field {f['id']} ({f.get('name','')}) — {f['variety']} — {f.get('acres', '?')} acres\n"
    await update.message.reply_text(msg)

async def handle_daily_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    fields = load_fields()
    
    # Simple auto-routing (looks for Field X or Block X)
    entries = []
    for field in fields:
        fid = field["id"].lower()
        if f"field {fid}" in text or f"block {fid}" in text or fid in text:
            # Try to grab number of bins
            import re
            bins_match = re.search(r'(\d+)\s*bin', text)
            bins = int(bins_match.group(1)) if bins_match else 0
            entries.append({
                "field_id": field["id"],
                "variety": field["variety"],
                "bins": bins
            })
    
    if entries:
        data = load_data()
        data["season"].append({"date": today, "entries": entries})
        save_data(data)
        await update.message.reply_text(f"✅ Auto-routed and saved for today! {len(entries)} entries logged.")
    else:
        await update.message.reply_text("🤔 I couldn't match that to a field. Try 'Field 1 15 bins' or send /map to see your fields.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["season"]:
        await update.message.reply_text("No harvest data yet. Send a daily note first!")
        return
    total_bins = sum(e.get("bins", 0) for day in data["season"] for e in day["entries"])
    await update.message.reply_text(f"📊 Season total so far: {total_bins} bins\nType /map to see fields.")

# Automatic daily texts
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="🌅 Good morning! Ready for today's harvest? Send your daily note anytime."
    )

async def evening_summary(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="🌙 Evening check-in: Bot is still running. Send today's note if you haven't yet!"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("map", show_map))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_daily_note))
    
    # Daily automatic messages (7am and 8pm)
    job_queue: JobQueue = app.job_queue
    job_queue.run_daily(morning_reminder, time=time(7, 0))
    job_queue.run_daily(evening_summary, time=time(20, 0))
    
    print("Full peach bot is running 24/7!")
    app.run_polling()

if __name__ == "__main__":
    main()
