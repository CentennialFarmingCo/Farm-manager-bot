import os
import json
import sqlite3
import requests
import re
from datetime import datetime, time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DASHBOARD_URL = "https://centennial-farming-map.onrender.com"   # ← Update with your real URL if different

DB_FILE = "farm_data.db"

# Three localized weather areas
WEATHER_LOCATIONS = {
    "Johnston_BlueLupin": {"lat": 36.75, "lon": -119.82, "name": "Johnston / Blue Lupin Area"},
    "Fagundes": {"lat": 36.70, "lon": -120.59, "name": "Fagundes Area"},
    "Johnston_Block35": {"lat": 37.366, "lon": -120.651, "name": "Johnston Block 35 Area"}
}

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

def get_weather(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&daily=precipitation_probability_max,temperature_2m_max&timezone=America/Los_Angeles"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        current = data["current_weather"]
        daily = data["daily"]
        return {
            "temp": round(current["temperature"]),
            "wind": round(current["windspeed"]),
            "rain_prob": daily["precipitation_probability_max"][0],
            "max_temp": round(daily["temperature_2m_max"][0])
        }
    except:
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Centennial Farming Advanced Bot** is LIVE!\n\n"
        "Try:\n"
        "/dashboard → Client map\n"
        "/weather → 3 localized reports\n"
        "/acres → Total or specific (e.g. /acres Fagundes)\n"
        "/payroll → Cost breakdown\n\n"
        "Natural examples:\n"
        "“tell me how many acres of peaches and almonds are in blocks 66,77,18,2”\n"
        "“peaches in block 35”"
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🍑 **Centennial Farming Company Map**\n\n{DASHBOARD_URL}")

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🌤️ **Localized Weather Reports (3 Areas)**\n\n"
    for key, loc in WEATHER_LOCATIONS.items():
        weather = get_weather(loc["lat"], loc["lon"])
        if weather:
            msg += f"**{loc['name']}**\n"
            msg += f"Temp: {weather['temp']}°F (high {weather['max_temp']}°F)\n"
            msg += f"Rain chance: {weather['rain_prob']}%\n"
            msg += f"Wind: {weather['wind']} mph\n\n"
    await update.message.reply_text(msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    today = datetime.now().strftime("%Y-%m-%d")
    fields = load_fields()

    # Smart acreage parsing for complex queries
    block_matches = re.findall(r'block\s*(\d+)', text) or re.findall(r'\b(\d{1,2})\b', text)
    block_list = [int(b) for b in block_matches if b.isdigit()]

    variety_filter = None
    if "peach" in text or "peaches" in text:
        variety_filter = "peach"
    elif "almond" in text or "almonds" in text:
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

    await update.message.reply_text("Got it! Try /weather, /dashboard, or log harvest like 'Field 5 18 bins'.")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Advanced Centennial Farming Bot with smart acreage parsing + 3 weather reports is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
