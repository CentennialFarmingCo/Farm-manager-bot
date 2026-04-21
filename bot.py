import os
import json
import requests
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import sqlite3
import asyncio
from typing import Dict, List, Optional

load_dotenv()

TOKEN = os.getenv(“TELEGRAM_BOT_TOKEN”)
OPENWEATHER_API_KEY = os.getenv(“OPENWEATHER_API_KEY”)  # You’ll need to add this to your .env

# === CONFIGURATION ===

DASHBOARD_URL = “https://centennial-farming-dashboard.onrender.com”
FIELDS_MAP = “fields_map.json”
DATABASE = “farm_data.db”

# Farm coordinates for weather (roughly center of your operation)

FARM_LAT = 37.41
FARM_LON = -120.77

class FarmDatabase:
def **init**(self, db_path: str):
self.db_path = db_path
self.init_database()

```
def init_database(self):
    """Initialize database with all required tables"""
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    
    # Harvest data (existing)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS harvest_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            field_id TEXT NOT NULL,
            variety TEXT,
            bins INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Weather data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS weather_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            temp_high REAL,
            temp_low REAL,
            temp_avg REAL,
            humidity REAL,
            wind_speed REAL,
            precipitation REAL,
            conditions TEXT,
            soil_temp REAL,
            uv_index REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Cost tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            field_id TEXT,
            category TEXT NOT NULL,  -- 'spray', 'fertilizer', 'labor', 'equipment'
            item_name TEXT NOT NULL,
            quantity REAL,
            unit TEXT,
            cost_per_unit REAL,
            total_cost REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Application records
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            field_id TEXT NOT NULL,
            application_type TEXT NOT NULL,  -- 'herbicide', 'insecticide', 'fungicide', 'fertilizer'
            product_name TEXT NOT NULL,
            rate REAL,
            rate_unit TEXT,
            acres_treated REAL,
            conditions TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Field observations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            field_id TEXT NOT NULL,
            observation_type TEXT,  -- 'pest', 'disease', 'growth', 'irrigation', 'general'
            severity TEXT,  -- 'low', 'medium', 'high'
            description TEXT,
            action_needed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def add_harvest_data(self, date: str, field_id: str, variety: str, bins: int, notes: str = ""):
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO harvest_data (date, field_id, variety, bins, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (date, field_id, variety, bins, notes))
    conn.commit()
    conn.close()

def add_weather_data(self, date: str, weather_data: Dict):
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO weather_data 
        (date, temp_high, temp_low, temp_avg, humidity, wind_speed, precipitation, conditions, soil_temp, uv_index)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        date,
        weather_data.get('temp_high'),
        weather_data.get('temp_low'),
        weather_data.get('temp_avg'),
        weather_data.get('humidity'),
        weather_data.get('wind_speed'),
        weather_data.get('precipitation'),
        weather_data.get('conditions'),
        weather_data.get('soil_temp'),
        weather_data.get('uv_index')
    ))
    conn.commit()
    conn.close()

def add_cost(self, date: str, field_id: str, category: str, item_name: str, 
            quantity: float, unit: str, cost_per_unit: float, notes: str = ""):
    total_cost = quantity * cost_per_unit
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO costs (date, field_id, category, item_name, quantity, unit, cost_per_unit, total_cost, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date, field_id, category, item_name, quantity, unit, cost_per_unit, total_cost, notes))
    conn.commit()
    conn.close()

def add_application(self, date: str, field_id: str, app_type: str, product_name: str,
                   rate: float, rate_unit: str, acres: float, conditions: str = "", notes: str = ""):
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO applications (date, field_id, application_type, product_name, rate, rate_unit, acres_treated, conditions, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date, field_id, app_type, product_name, rate, rate_unit, acres, conditions, notes))
    conn.commit()
    conn.close()

def add_observation(self, date: str, field_id: str, obs_type: str, severity: str,
                   description: str, action_needed: bool = False):
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO observations (date, field_id, observation_type, severity, description, action_needed)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (date, field_id, obs_type, severity, description, action_needed))
    conn.commit()
    conn.close()

def get_harvest_totals(self) -> int:
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(bins) FROM harvest_data')
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0

def get_field_costs(self, field_id: str = None, days: int = 30) -> List[Dict]:
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    
    since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    if field_id:
        cursor.execute('''
            SELECT * FROM costs 
            WHERE field_id = ? AND date >= ?
            ORDER BY date DESC
        ''', (field_id, since_date))
    else:
        cursor.execute('''
            SELECT * FROM costs 
            WHERE date >= ?
            ORDER BY date DESC
        ''', (since_date,))
    
    columns = [description[0] for description in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return results

def get_recent_weather(self, days: int = 7) -> List[Dict]:
    conn = sqlite3.connect(self.db_path)
    cursor = conn.cursor()
    
    since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cursor.execute('''
        SELECT * FROM weather_data 
        WHERE date >= ?
        ORDER BY date DESC
    ''', (since_date,))
    
    columns = [description[0] for description in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return results
```

class WeatherService:
def **init**(self, api_key: str):
self.api_key = api_key
self.base_url = “https://api.openweathermap.org/data/2.5”
self.agro_url = “https://api.openweathermap.org/data/2.5/agro/1.0”

```
async def get_current_weather(self, lat: float, lon: float) -> Dict:
    """Get current weather conditions"""
    try:
        url = f"{self.base_url}/weather"
        params = {
            'lat': lat,
            'lon': lon,
            'appid': self.api_key,
            'units': 'imperial'
        }
        
        response = requests.get(url, params=params)
        data = response.json()
        
        return {
            'temp_current': data['main']['temp'],
            'temp_high': data['main'].get('temp_max'),
            'temp_low': data['main'].get('temp_min'),
            'humidity': data['main']['humidity'],
            'wind_speed': data['wind']['speed'],
            'conditions': data['weather'][0]['description'],
            'uv_index': None  # Need separate call for UV
        }
    except Exception as e:
        print(f"Weather API error: {e}")
        return {}

async def get_forecast(self, lat: float, lon: float, days: int = 5) -> List[Dict]:
    """Get weather forecast"""
    try:
        url = f"{self.base_url}/forecast"
        params = {
            'lat': lat,
            'lon': lon,
            'appid': self.api_key,
            'units': 'imperial'
        }
        
        response = requests.get(url, params=params)
        data = response.json()
        
        forecasts = []
        for item in data['list'][:days*8]:  # 8 forecasts per day (3-hour intervals)
            forecasts.append({
                'datetime': item['dt_txt'],
                'temp': item['main']['temp'],
                'humidity': item['main']['humidity'],
                'conditions': item['weather'][0]['description'],
                'wind_speed': item['wind']['speed'],
                'precipitation': item.get('rain', {}).get('3h', 0)
            })
        
        return forecasts
    except Exception as e:
        print(f"Forecast API error: {e}")
        return []
```

class AgriculturalAdvisor:
def **init**(self):
self.peach_varieties = {
‘Kaweah Freestone Peach’: {‘harvest_start’: ‘July 15’, ‘harvest_end’: ‘August 5’},
‘Zee Lady Freestone Peach’: {‘harvest_start’: ‘July 20’, ‘harvest_end’: ‘August 10’},
‘Angelus Freestone Peach’: {‘harvest_start’: ‘August 1’, ‘harvest_end’: ‘August 20’},
‘Parade Freestone Peach’: {‘harvest_start’: ‘August 10’, ‘harvest_end’: ‘August 30’},
‘Tra Zee Freestone Peach’: {‘harvest_start’: ‘July 25’, ‘harvest_end’: ‘August 15’},
‘Autumn Flame Freestone Peach’: {‘harvest_start’: ‘August 20’, ‘harvest_end’: ‘September 10’},
‘Carnival Freestone Peach’: {‘harvest_start’: ‘July 10’, ‘harvest_end’: ‘July 30’},
‘Fairtime Freestone Peach’: {‘harvest_start’: ‘August 5’, ‘harvest_end’: ‘August 25’},
‘Late Ross Cling Peach’: {‘harvest_start’: ‘August 25’, ‘harvest_end’: ‘September 15’},
‘Klamath Cling Peach’: {‘harvest_start’: ‘September 1’, ‘harvest_end’: ‘September 20’},
‘July Flame Freestone Peach’: {‘harvest_start’: ‘July 1’, ‘harvest_end’: ‘July 20’},
‘Fay Elberta Freestone Peach’: {‘harvest_start’: ‘August 15’, ‘harvest_end’: ‘September 5’},
‘Elegant Lady Freestone Peach’: {‘harvest_start’: ‘September 10’, ‘harvest_end’: ‘September 30’}
}

```
    self.almond_varieties = {
        'Butte/Padre Almond': {'harvest_start': 'August 15', 'harvest_end': 'September 15'},
        'Nonpareil Almond': {'harvest_start': 'August 10', 'harvest_end': 'September 5'},
        'Independence Almond': {'harvest_start': 'September 1', 'harvest_end': 'September 25'},
        'Nonpareil/Monterey Almond': {'harvest_start': 'August 10', 'harvest_end': 'September 10'},
        'Nonpareil/Monterey/Carmel Almond': {'harvest_start': 'August 10', 'harvest_end': 'September 15'}
    }

def get_daily_recommendations(self, current_weather: Dict, forecast: List[Dict], current_date: str) -> List[str]:
    """Generate daily recommendations based on weather and calendar"""
    recommendations = []
    current_month = datetime.strptime(current_date, '%Y-%m-%d').month
    
    # Temperature-based recommendations
    if current_weather.get('temp_current', 0) > 95:
        recommendations.append("🌡️ High heat warning! Consider early morning or evening applications only.")
        recommendations.append("💧 Monitor irrigation closely - trees will need extra water.")
    
    # Wind recommendations
    if current_weather.get('wind_speed', 0) > 10:
        recommendations.append("💨 High winds detected - avoid spray applications today.")
    
    # Seasonal recommendations
    if current_month in [6, 7, 8]:  # Summer harvest season
        recommendations.append("🍑 Harvest season active - check fruit maturity daily in early fields.")
        recommendations.append("🚜 Coordinate bin placement and harvest crews.")
    
    if current_month in [11, 12, 1, 2]:  # Dormant season
        recommendations.append("✂️ Pruning season - ideal weather for orchard maintenance.")
        recommendations.append("🧊 Monitor chill hour accumulation for spring bloom timing.")
    
    if current_month in [3, 4]:  # Bloom season
        recommendations.append("🌸 Bloom season - monitor for frost protection needs.")
        recommendations.append("🐛 Scout for pest emergence with warming temperatures.")
    
    # Forecast-based recommendations
    if forecast:
        temps = [f.get('temp', 0) for f in forecast[:8]]  # Next 24 hours
        if any(temp < 32 for temp in temps):
            recommendations.append("❄️ FROST WARNING! Prepare frost protection systems immediately.")
    
    return recommendations

def get_field_priority(self, field_data: Dict, current_date: str) -> str:
    """Determine field priority for daily checks"""
    variety = field_data.get('variety', '')
    
    # Check if in harvest window
    variety_info = self.peach_varieties.get(variety) or self.almond_varieties.get(variety)
    if variety_info:
        # Simplified harvest window check (you'd want more sophisticated date parsing)
        return "High - Harvest Window" if "July" in variety_info.get('harvest_start', '') or "August" in variety_info.get('harvest_start', '') else "Medium"
    
    return "Low"
```

# Initialize global objects

db = FarmDatabase(DATABASE)
weather_service = WeatherService(OPENWEATHER_API_KEY) if OPENWEATHER_API_KEY else None
advisor = AgriculturalAdvisor()

def load_fields():
“”“Load field data from JSON”””
if os.path.exists(FIELDS_MAP):
with open(FIELDS_MAP, “r”) as f:
return json.load(f)[“fields”]
return []

def get_total_acres():
“”“Calculate total acres from field data”””
fields = load_fields()
total = sum(float(field.get(“acres”, 0)) for field in fields)
return round(total, 1)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Welcome message with enhanced commands”””
await update.message.reply_text(
“🌳 Enhanced Farm Management Bot is LIVE!\n\n”
“📊 **Data & Reports:**\n”
“/map → view all 45 fields\n”
“/report → season harvest totals\n”
“/weather → current conditions & forecast\n”
“/costs → recent expenses\n”
“/dashboard → interactive farm map\n\n”
“📝 **Quick Logging:**\n”
“/log → log applications, costs, observations\n”
“/harvest → log harvest data\n\n”
“🎯 **Smart Features:**\n”
“/recommendations → daily farm recommendations\n”
“/priorities → today’s field priorities\n”
“/acres → total farm acreage\n\n”
“💬 **Natural Language:**\n”
“Text: ‘Field 5 12 bins’ or ‘Sprayed Block 1 with fungicide’”
)

async def show_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Display field map with enhanced info”””
fields = load_fields()
msg = “📍 **FIELD MAP & STATUS**\n\n”

```
for f in fields:
    priority = advisor.get_field_priority(f, datetime.now().strftime('%Y-%m-%d'))
    msg += f"**{f['name']}** (Field {f['id']})\n"
    msg += f"🌿 {f['variety']} • {f.get('acres', 0)} acres\n"
    msg += f"📋 Priority: {priority}\n\n"

if len(msg) > 4000:  # Telegram message limit
    # Split into multiple messages
    messages = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    for message in messages:
        await update.message.reply_text(message, parse_mode='Markdown')
else:
    await update.message.reply_text(msg, parse_mode='Markdown')
```

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Enhanced harvest and farm report”””
total_bins = db.get_harvest_totals()
total_acres = get_total_acres()

```
# Get recent costs
recent_costs = db.get_field_costs(days=30)
total_cost = sum(cost['total_cost'] for cost in recent_costs)
cost_per_acre = total_cost / total_acres if total_acres > 0 else 0

msg = f"📊 **FARM REPORT**\n\n"
msg += f"🍑 **Harvest:** {total_bins} total bins\n"
msg += f"🌳 **Acreage:** {total_acres} acres\n"
msg += f"💰 **30-Day Costs:** ${total_cost:,.2f} (${cost_per_acre:.2f}/acre)\n"

if recent_costs:
    msg += f"\n**Recent Expenses:**\n"
    for cost in recent_costs[-5:]:  # Last 5 entries
        msg += f"• {cost['date']}: {cost['item_name']} - ${cost['total_cost']:.2f}\n"

await update.message.reply_text(msg, parse_mode='Markdown')
```

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Get current weather and forecast”””
if not weather_service:
await update.message.reply_text(“⚠️ Weather service not configured. Add OPENWEATHER_API_KEY to .env file.”)
return

```
current = await weather_service.get_current_weather(FARM_LAT, FARM_LON)
forecast = await weather_service.get_forecast(FARM_LAT, FARM_LON, 3)

msg = "🌤️ **WEATHER REPORT**\n\n"

if current:
    msg += f"**Current Conditions:**\n"
    msg += f"🌡️ {current.get('temp_current', 'N/A')}°F\n"
    msg += f"💧 Humidity: {current.get('humidity', 'N/A')}%\n"
    msg += f"💨 Wind: {current.get('wind_speed', 'N/A')} mph\n"
    msg += f"☁️ {current.get('conditions', 'N/A').title()}\n\n"

if forecast:
    msg += f"**3-Day Forecast:**\n"
    daily_forecasts = {}
    for f in forecast[:24]:  # Next 24 3-hour periods = 3 days
        date = f['datetime'][:10]  # Extract date
        if date not in daily_forecasts:
            daily_forecasts[date] = {
                'temps': [],
                'conditions': f['conditions'],
                'precipitation': f['precipitation']
            }
        daily_forecasts[date]['temps'].append(f['temp'])
    
    for date, data in list(daily_forecasts.items())[:3]:
        high = max(data['temps'])
        low = min(data['temps'])
        msg += f"**{date}:** {high:.0f}°/{low:.0f}°F • {data['conditions'].title()}\n"

await update.message.reply_text(msg, parse_mode='Markdown')
```

async def recommendations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Get daily agricultural recommendations”””
if not weather_service:
await update.message.reply_text(“⚠️ Weather service needed for recommendations. Add OPENWEATHER_API_KEY to .env file.”)
return

```
current = await weather_service.get_current_weather(FARM_LAT, FARM_LON)
forecast = await weather_service.get_forecast(FARM_LAT, FARM_LON, 1)

recommendations = advisor.get_daily_recommendations(
    current, forecast, datetime.now().strftime('%Y-%m-%d')
)

msg = "🎯 **TODAY'S RECOMMENDATIONS**\n\n"

if recommendations:
    for i, rec in enumerate(recommendations, 1):
        msg += f"{i}. {rec}\n\n"
else:
    msg += "✅ No specific alerts today. Standard field monitoring recommended."

await update.message.reply_text(msg, parse_mode='Markdown')
```

async def costs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“View recent costs and expenses”””
costs = db.get_field_costs(days=30)

```
if not costs:
    await update.message.reply_text("📊 No costs recorded in the last 30 days.")
    return

# Group costs by category
by_category = {}
total = 0

for cost in costs:
    category = cost['category']
    if category not in by_category:
        by_category[category] = []
    by_category[category].append(cost)
    total += cost['total_cost']

msg = f"💰 **30-DAY COST SUMMARY**\n\n"
msg += f"**Total: ${total:,.2f}**\n\n"

for category, items in by_category.items():
    category_total = sum(item['total_cost'] for item in items)
    msg += f"**{category.title()}:** ${category_total:,.2f}\n"
    
    # Show recent items in this category
    for item in items[-3:]:  # Last 3 items
        msg += f"• {item['date']}: {item['item_name']} (Field {item['field_id']}) - ${item['total_cost']:.2f}\n"
    msg += "\n"

await update.message.reply_text(msg, parse_mode='Markdown')
```

async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Interactive logging menu”””
keyboard = [
[InlineKeyboardButton(“🌾 Application”, callback_data=“log_application”)],
[InlineKeyboardButton(“💰 Cost/Expense”, callback_data=“log_cost”)],
[InlineKeyboardButton(“👁️ Field Observation”, callback_data=“log_observation”)],
[InlineKeyboardButton(“🍑 Harvest Data”, callback_data=“log_harvest”)]
]

```
reply_markup = InlineKeyboardMarkup(keyboard)
await update.message.reply_text(
    "📝 **What would you like to log?**",
    reply_markup=reply_markup
)
```

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle button presses”””
query = update.callback_query
await query.answer()

```
if query.data == "log_application":
    await query.edit_message_text(
        "🌾 **Log Application**\n\n"
        "Format: `/app [field_id] [type] [product] [rate] [rate_unit] [acres]`\n\n"
        "Example: `/app 5 fungicide Propiconazole 6 oz_acre 18.5`\n\n"
        "Types: herbicide, insecticide, fungicide, fertilizer"
    )
elif query.data == "log_cost":
    await query.edit_message_text(
        "💰 **Log Cost/Expense**\n\n"
        "Format: `/cost [field_id] [category] [item] [quantity] [unit] [cost_per_unit]`\n\n"
        "Example: `/cost 5 spray Fungicide 2 gallons 45.50`\n\n"
        "Categories: spray, fertilizer, labor, equipment"
    )
elif query.data == "log_observation":
    await query.edit_message_text(
        "👁️ **Log Field Observation**\n\n"
        "Format: `/obs [field_id] [type] [severity] [description]`\n\n"
        "Example: `/obs 5 pest medium Found aphids on lower branches`\n\n"
        "Types: pest, disease, growth, irrigation, general\n"
        "Severity: low, medium, high"
    )
elif query.data == "log_harvest":
    await query.edit_message_text(
        "🍑 **Log Harvest Data**\n\n"
        "Format: `/harvest [field_id] [bins] [notes]`\n\n"
        "Example: `/harvest 5 12 Good quality fruit`\n\n"
        "Or use natural language: 'Field 5 harvested 12 bins'"
    )
```

# Command handlers for logging

async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Log application data”””
try:
args = context.args
if len(args) < 6:
await update.message.reply_text(“Usage: /app [field_id] [type] [product] [rate] [rate_unit] [acres]”)
return

```
    field_id, app_type, product, rate, rate_unit, acres = args[:6]
    notes = " ".join(args[6:]) if len(args) > 6 else ""
    
    db.add_application(
        datetime.now().strftime('%Y-%m-%d'),
        field_id, app_type, product,
        float(rate), rate_unit, float(acres),
        notes=notes
    )
    
    await update.message.reply_text(
        f"✅ **Application Logged**\n\n"
        f"Field {field_id}: {product} ({app_type})\n"
        f"Rate: {rate} {rate_unit} on {acres} acres"
    )
except Exception as e:
    await update.message.reply_text(f"❌ Error logging application: {str(e)}")
```

async def cost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Log cost data”””
try:
args = context.args
if len(args) < 6:
await update.message.reply_text(“Usage: /cost [field_id] [category] [item] [quantity] [unit] [cost_per_unit]”)
return

```
    field_id, category, item, quantity, unit, cost_per_unit = args[:6]
    notes = " ".join(args[6:]) if len(args) > 6 else ""
    
    db.add_cost(
        datetime.now().strftime('%Y-%m-%d'),
        field_id, category, item,
        float(quantity), unit, float(cost_per_unit),
        notes=notes
    )
    
    total_cost = float(quantity) * float(cost_per_unit)
    await update.message.reply_text(
        f"✅ **Cost Logged**\n\n"
        f"Field {field_id}: {item} ({category})\n"
        f"Quantity: {quantity} {unit}\n"
        f"Total Cost: ${total_cost:.2f}"
    )
except Exception as e:
    await update.message.reply_text(f"❌ Error logging cost: {str(e)}")
```

async def obs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Log field observation”””
try:
args = context.args
if len(args) < 4:
await update.message.reply_text(“Usage: /obs [field_id] [type] [severity] [description]”)
return

```
    field_id, obs_type, severity = args[:3]
    description = " ".join(args[3:])
    
    action_needed = severity.lower() == "high"
    
    db.add_observation(
        datetime.now().strftime('%Y-%m-%d'),
        field_id, obs_type, severity,
        description, action_needed
    )
    
    action_text = "⚠️ Action needed!" if action_needed else "✅ Monitoring"
    
    await update.message.reply_text(
        f"✅ **Observation Logged**\n\n"
        f"Field {field_id}: {obs_type.title()} Issue\n"
        f"Severity: {severity.title()}\n"
        f"Note: {description}\n"
        f"Status: {action_text}"
    )
except Exception as e:
    await update.message.reply_text(f"❌ Error logging observation: {str(e)}")
```

async def harvest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Log harvest data”””
try:
args = context.args
if len(args) < 2:
await update.message.reply_text(“Usage: /harvest [field_id] [bins] [notes]”)
return

```
    field_id, bins = args[:2]
    notes = " ".join(args[2:]) if len(args) > 2 else ""
    
    # Get field variety
    fields = load_fields()
    field_data = next((f for f in fields if f['id'] == field_id), {})
    variety = field_data.get('variety', 'Unknown')
    
    db.add_harvest_data(
        datetime.now().strftime('%Y-%m-%d'),
        field_id, variety, int(bins), notes
    )
    
    await update.message.reply_text(
        f"✅ **Harvest Logged**\n\n"
        f"Field {field_id} ({variety})\n"
        f"Bins: {bins}\n"
        f"Notes: {notes}"
    )
except Exception as e:
    await update.message.reply_text(f"❌ Error logging harvest: {str(e)}")
```

async def priorities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Show today’s field priorities”””
fields = load_fields()
current_date = datetime.now().strftime(’%Y-%m-%d’)

```
high_priority = []
medium_priority = []

for field in fields:
    priority = advisor.get_field_priority(field, current_date)
    if "High" in priority:
        high_priority.append((field, priority))
    else:
        medium_priority.append((field, priority))

msg = "🎯 **TODAY'S FIELD PRIORITIES**\n\n"

if high_priority:
    msg += "**🔴 HIGH PRIORITY:**\n"
    for field, priority in high_priority:
        msg += f"• Field {field['id']} ({field['name']}) - {priority}\n"
    msg += "\n"

msg += "**🟡 STANDARD MONITORING:**\n"
for field, priority in medium_priority[:10]:  # Show first 10
    msg += f"• Field {field['id']} ({field['name']})\n"

if len(medium_priority) > 10:
    msg += f"... and {len(medium_priority) - 10} more fields\n"

await update.message.reply_text(msg, parse_mode='Markdown')
```

async def total_acres_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
acres = get_total_acres()
await update.message.reply_text(f”🌳 You currently farm **{acres} acres** across all 45 fields.”, parse_mode=‘Markdown’)

async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
f”🍑 **Interactive Farm Map**\n\n”
f”Here is your professional shaded field map for clients:\n”
f”{DASHBOARD_URL}\n\n”
“Share this link with anyone — it’s clean and fully interactive!”,
parse_mode=‘Markdown’
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Enhanced message handler with natural language processing”””
text = update.message.text.lower()
today = datetime.now().strftime(”%Y-%m-%d”)
fields = load_fields()

```
# Try to parse harvest entries (existing functionality)
harvest_entries = []
for field in fields:
    fid = field["id"]
    if f"field {fid}" in text or f"block {fid}" in text:
        import re
        bins_match = re.search(r'(\d+)\s*bin', text)
        if bins_match:
            bins = int(bins_match.group(1))
            harvest_entries.append({
                "field_id": fid,
                "variety": field["variety"],
                "bins": bins
            })

# Log harvest entries
if harvest_entries:
    for entry in harvest_entries:
        db.add_harvest_data(today, entry["field_id"], entry["variety"], entry["bins"])
    
    await update.message.reply_text(f"✅ Harvest logged! {len(harvest_entries)} entries saved for today.")
    return

# Try to parse application entries
if "spray" in text and ("field" in text or "block" in text):
    import re
    field_match = re.search(r'(?:field|block)\s*(\d+)', text)
    if field_match:
        field_id = field_match.group(1)
        product = "Unknown product"
        
        if "fungicide" in text:
            product = "Fungicide"
            app_type = "fungicide"
        elif "insecticide" in text:
            product = "Insecticide"
            app_type = "insecticide"
        elif "herbicide" in text:
            product = "Herbicide"
            app_type = "herbicide"
        else:
            app_type = "spray"
        
        # Find the field to get acres
        field_data = next((f for f in fields if f['id'] == field_id), None)
        acres = field_data.get('acres', 0) if field_data else 0
        
        db.add_application(today, field_id, app_type, product, 0, "unknown", acres)
        
        await update.message.reply_text(f"✅ Application logged! {product} on Field {field_id} ({acres} acres).")
        return

# Simple responses for common queries
if "acre" in text or "how many acres" in text:
    acres = get_total_acres()
    await update.message.reply_text(f"🌳 You currently farm **{acres} acres** across all fields.", parse_mode='Markdown')
elif "weather" in text:
    await weather_command(update, context)
elif "cost" in text or "expense" in text:
    await costs_command(update, context)
elif "recommend" in text:
    await recommendations_command(update, context)
else:
    await update.message.reply_text(
        "Got it! Try:\n"
        "• /weather for current conditions\n"
        "• /recommendations for daily advice\n"
        "• /log to record data\n"
        "• /map to see all fields\n"
        "• Text things like 'Field 5 12 bins' or 'Sprayed Block 1 with fungicide'"
    )
```

async def daily_weather_update():
“”“Fetch and store daily weather data”””
if not weather_service:
return

```
try:
    current = await weather_service.get_current_weather(FARM_LAT, FARM_LON)
    if current:
        today = datetime.now().strftime('%Y-%m-%d')
        weather_data = {
            'temp_high': current.get('temp_high'),
            'temp_low': current.get('temp_low'),
            'temp_avg': current.get('temp_current'),
            'humidity': current.get('humidity'),
            'wind_speed': current.get('wind_speed'),
            'precipitation': 0,  # Would need additional API call
            'conditions': current.get('conditions'),
            'soil_temp': None,  # Would need agricultural API
            'uv_index': current.get('uv_index')
        }
        db.add_weather_data(today, weather_data)
        print(f"Weather data updated for {today}")
except Exception as e:
    print(f"Error updating weather data: {e}")
```

def main():
“”“Main bot function with all handlers”””
if not TOKEN:
print(“Error: TELEGRAM_BOT_TOKEN not found in environment variables”)
return

```
app = Application.builder().token(TOKEN).build()

# Command handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("map", show_map))
app.add_handler(CommandHandler("report", report))
app.add_handler(CommandHandler("weather", weather_command))
app.add_handler(CommandHandler("recommendations", recommendations_command))
app.add_handler(CommandHandler("costs", costs_command))
app.add_handler(CommandHandler("priorities", priorities_command))
app.add_handler(CommandHandler("acres", total_acres_command))
app.add_handler(CommandHandler("dashboard", dashboard_command))
app.add_handler(CommandHandler("log", log_command))

# Logging command handlers
app.add_handler(CommandHandler("app", app_command))
app.add_handler(CommandHandler("cost", cost_command))
app.add_handler(CommandHandler("obs", obs_command))
app.add_handler(CommandHandler("harvest", harvest_command))

# Callback handlers
app.add_handler(CallbackQueryHandler(button_handler))

# Message handler
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("🌳 Enhanced Farm Management Bot is running!")
print("Features: Weather monitoring, cost tracking, applications, recommendations")

# Start the bot
app.run_polling()
```

if **name** == “**main**”:
main()
