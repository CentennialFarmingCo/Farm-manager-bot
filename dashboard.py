import streamlit as st
import folium
from streamlit_folium import st_folium
import json
import re

st.set_page_config(page_title="Centennial Farming Company", page_icon="🍑", layout="wide")

# Your logo
st.image("st.image("https://raw.githubusercontent.com/CentennialFarmingCo/Farm-manager-bot/main/CENTENNIAL%201%20%20final%20Jpg.jpeg", width=600)", 
         use_column_width=False, width=600)

st.title("🍑 Centennial Farming Company")
st.markdown("**Professional Interactive Field Map** — Current & Prospective Clients")

# Load data
with open("fields_map.json", "r") as f:
    data = json.load(f)["fields"]

# Clean display name for clients (no ownership prefixes)
def clean_name(name):
    name = re.sub(r'^(Johnston|Fagundes|Blue Lupin)\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^.*?Block', 'Block', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

# Totals (no pandas needed)
total_acres = round(sum(float(field.get("acres", 0)) for field in data), 1)
peach_count = sum(1 for f in data if "Peach" in f.get("variety", ""))
almond_count = len(data) - peach_count

col1, col2, col3 = st.columns(3)
col1.metric("Total Acres", f"{total_acres}")
col2.metric("Peach Fields", peach_count)
col3.metric("Almond Fields", almond_count)

# Interactive Map
st.subheader("Interactive Farm Boundaries")

m = folium.Map(location=[37.41, -120.78], zoom_start=12, tiles="CartoDB positron")

peach_layer = folium.FeatureGroup(name="🌳 Peach Fields")
almond_layer = folium.FeatureGroup(name="🌰 Almond Fields")

for field in data:
    color = "#2E8B57" if "Peach" in field.get("variety", "") else "#8B4513"
    folium.Polygon(
        locations=field["polygon"],
        color=color,
        weight=3,
        fill=True,
        fillOpacity=0.4,
        popup=folium.Popup(f"""
            <b>{clean_name(field['name'])}</b><br>
            {field['variety']}<br>
            <b>{field['acres']} acres</b>
        """, max_width=300),
        tooltip=clean_name(field['name']),
        highlight_function=lambda x: {"weight": 5, "fillOpacity": 0.7}
    ).add_to(peach_layer if "Peach" in field.get("variety", "") else almond_layer)

peach_layer.add_to(m)
almond_layer.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, width=1200, height=700)

st.caption("✅ Click any shaded field for details • Hover for quick info • Toggle layers on the right")
