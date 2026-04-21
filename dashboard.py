import streamlit as st
import folium
from streamlit_folium import st_folium
import json
import pandas as pd
import re

st.set_page_config(page_title="Centennial Farming Company", page_icon="🍑", layout="wide")

st.title("🍑 Centennial Farming Company")
st.markdown("**Professional Interactive Field Map** — Current & Prospective Clients")

# Load data
with open("fields_map.json", "r") as f:
    data = json.load(f)["fields"]

df = pd.DataFrame(data)

# Clean display name for clients (remove ownership prefixes)
def clean_name(name):
    # Remove ownership prefixes like Johnston, Fagundes, Blue Lupin
    name = re.sub(r'^(Johnston| F agundes|Blue Lupin)\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^\s*Block\s*', 'Block ', name)
    return name.strip()

df['display_name'] = df['name'].apply(clean_name)

# Totals
total_acres = round(df["acres"].sum(), 1)
peach_count = len(df[df["variety"].str.contains("Peach", na=False)])
almond_count = len(df) - peach_count

col1, col2, col3 = st.columns(3)
col1.metric("Total Acres", f"{total_acres}")
col2.metric("Peach Fields", peach_count)
col3.metric("Almond Fields", almond_count)

# Interactive Map
st.subheader("Interactive Farm Boundaries")

m = folium.Map(location=[37.41, -120.78], zoom_start=12, tiles="CartoDB positron")

peach_layer = folium.FeatureGroup(name="🌳 Peach Fields")
almond_layer = folium.FeatureGroup(name="🌰 Almond Fields")

for _, row in df.iterrows():
    color = "#2E8B57" if "Peach" in row["variety"] else "#8B4513"
    folium.Polygon(
        locations=row["polygon"],
        color=color,
        weight=3,
        fill=True,
        fillOpacity=0.4,
        popup=folium.Popup(f"""
            <b>{row['display_name']}</b><br>
            {row['variety']}<br>
            <b>{row['acres']} acres</b>
        """, max_width=300),
        tooltip=row['display_name'],
        highlight_function=lambda x: {"weight": 5, "fillOpacity": 0.7}
    ).add_to(peach_layer if "Peach" in row["variety"] else almond_layer)

peach_layer.add_to(m)
almond_layer.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, width=1200, height=700)

st.caption("✅ Click any shaded field for details • Hover for quick info • Toggle layers on the right")
