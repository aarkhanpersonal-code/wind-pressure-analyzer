import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date, timedelta
from geopy.geocoders import Nominatim
import geopy.exc
import openmeteo_requests
import requests_cache
from retry_requests import retry

st.set_page_config(page_title="Driving Rain Wind Pressure Analyzer", page_icon="🌧️", layout="wide")

def geocode_city(city_name):
    try:
        geolocator = Nominatim(user_agent="facade_wind_pressure_analyzer_v1", timeout=10)
        location = geolocator.geocode(city_name)
        if location is None:
            return None
        return location.latitude, location.longitude, location.address
    except geopy.exc.GeocoderTimedOut:
        st.error("⏱️ The geocoding service timed out. Please try again.")
        return None
    except geopy.exc.GeocoderUnavailable:
        st.error("🌐 The geocoding service is unavailable. Check your connection.")
        return None
    except Exception as e:
        st.error(f"An unexpected geocoding error occurred: {e}")
        return None

@st.cache_data(ttl=3600, show_spinner="Fetching historical weather data…")
def fetch_weather(lat, lon, start_date, end_date):
    cache_session = requests_cache.CachedSession(".openmeteo_cache", expire_after=-1)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.3)
    openmeteo = openmeteo_requests.Client(session=retry_session)
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ["precipitation", "wind_gusts_10m"],
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }
    try:
        responses = openmeteo.weather_api(url, params=params)
        response = responses[0]
        hourly = response.Hourly()
        time_range = pd.date_range(
            start=pd.Timestamp(hourly.Time(), unit="s", tz="UTC"),
            end=pd.Timestamp(hourly.TimeEnd(), unit="s", tz="UTC"),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left",
        )
        df = pd.DataFrame({
            "time": time_range,
            "precipitation_mm": hourly.Variables(0).ValuesAsNumpy(),
            "wind_gust_kmh": hourly.Variables(1).ValuesAsNumpy(),
        })
        df.set_index("time", inplace=True)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        st.error(f"❌ Failed to fetch weather data: {e}")
        return None

def calculate_wind_pressure(df, rain_threshold_mm=0.1):
    result = df.copy()
    result["wind_gust_ms"] = result["wind_gust_kmh"] / 3.6
    result["wind_pressure_pa"] = 0.613 * result["wind_gust_ms"] ** 2
    result["is_wet_hour"] = result["precipitation_mm"] > rain_threshold_mm
    result["driving_rain_pressure_pa"] = np.where(result["is_wet_hour"], result["wind_pressure_pa"], 0.0)
    return result

with st.sidebar:
    st.header("⚙️ Analysis Parameters")
    city_name = st.text_input("City / Location", value="Toronto, Canada")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start Date", value=date.today() - timedelta(days=365), max_value=date.today() - timedelta(days=5))
    with col2:
        end_date = st.date_input("End Date", value=date.today() - timedelta(days=5), max_value=date.today() - timedelta(days=5))
    st.divider()
    st.subheader("🏗️ Test Standard Threshold")
    threshold_pa = st.number_input("Pressure Threshold (Pa)", min_value=50, max_value=3000, value=480, step=10)
    st.caption("**Common References:**\n- ASTM E514 (masonry): **480 Pa**\n- ASTM E331 (windows): **137 Pa**\n- AAMA 501.1 (curtain wall): **300 Pa**\n- NBC Canada: **500 Pa**")
    analyze_btn = st.button("🔍 Analyze Storm Data", use_container_width=True, type="primary")

st.title("🌧️ Driving Rain Wind Pressure Analyzer")
st.markdown("""
This tool compares real-world storm events against static pressure thresholds used in lab water-penetration tests.
When a storm produces pressures that **exceed** the test standard, the facade was subjected to conditions more severe than it was rated for.

> **Physics:** P = 0.613 × V² (Pa), evaluated only during hours with rain > 0.1 mm/hr. Wind **gusts** are used, not average speed.
""")
st.divider()

if analyze_btn:
    if start_date >= end_date:
        st.error("❌ Start date must be before end date.")
        st.stop()

    with st.spinner(f"Locating '{city_name}'…"):
        geo_result = geocode_city(city_name)

    if geo_result is None:
        st.error(f"Could not find coordinates for **'{city_name}'**. Try a more specific name.")
        st.stop()

    lat, lon, display_address = geo_result
    st.success(f"📍 **{display_address}** ({lat:.4f}°N, {lon:.4f}°E)")

    df_raw = fetch_weather(lat=lat, lon=lon, start_date=str(start_date), end_date=str(end_date))

    if df_raw is None or df_raw.empty:
        st.error("No weather data returned for this location and date range.")
        st.stop()

    df = calculate_wind_pressure(df_raw)
    df["is_breach_event"] = df["is_wet_hour"] & (df["wind_pressure_pa"] >= threshold_pa)
    breach_events = df[df["is_breach_event"]]

    st.subheader("📊 Summary Statistics")
    total_hours = len(df)
    wet_hours = int(df["is_wet_hour"].sum())
    breach_count = int(df["is_breach_event"].sum())
    max_pressure = df["driving_rain_pressure_pa"].max()
    max_gust_kmh = df.loc[df["is_wet_hour"], "wind_gust_kmh"].max() if wet_hours > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Hours", f"{total_hours:,}")
    c2.metric("Wet Hours", f"{wet_hours:,}")
    c3.metric("⚠️ Breach Events", f"{breach_count:,}", delta=f"{breach_count/wet_hours*100:.1f}% of wet hrs" if wet_hours > 0 else "—", delta_color="inverse")
    c4.metric("Peak Pressure", f"{max_pressure:.0f} Pa")
    c5.metric("Peak Wet Gust", f"{max_gust_kmh:.1f} km/h")

    st.subheader("📈 Driving Rain Pressure Over Time")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["wind_pressure_pa"], mode="lines", name="Wind Pressure (all hours)", line=dict(color="rgba(100, 160, 220, 0.25)", width=1)))
    fig.add_trace(go.Scatter(x=df.index, y=df["driving_rain_pressure_pa"], mode="lines", name="Driving Rain Pressure (rain > 0.1mm)", line=dict(color="steelblue", width=1.5)))
    if not breach_events.empty:
        fig.add_trace(go.Scatter(x=breach_events.index, y=breach_events["wind_pressure_pa"], mode="markers", name=f"⚠️ Breach Event (≥ {threshold_pa} Pa + rain)", marker=dict(color="crimson", size=8, symbol="circle")))
    fig.add_hline(y=threshold_pa, line_dash="dash", line_color="red", line_width=2, annotation_text=f"Threshold: {threshold_pa} Pa", annotation_position="top left", annotation_font_color="red")
    fig.update_layout(xaxis_title="Date / Time (UTC)", yaxis_title="Wind Pressure (Pascals)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified", height=500, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    if not breach_events.empty:
        st.subheader(f"⚠️ {len(breach_events)} Breach Event(s) Detected")
        display_df = breach_events[["precipitation_mm", "wind_gust_kmh", "wind_gust_ms", "wind_pressure_pa"]].copy()
        display_df.columns = ["Rain (mm/hr)", "Wind Gust (km/h)", "Wind Gust (m/s)", "Pressure (Pa)"]
        display_df.index = display_df.index.strftime("%Y-%m-%d %H:%M UTC")
        st.dataframe(display_df.style.format(precision=2), use_container_width=True)
    else:
        st.success(f"✅ No breach events detected above {threshold_pa} Pa during rainy hours.")

    with st.expander("📥 Download Raw Data"):
        csv_out = df[["precipitation_mm", "wind_gust_kmh", "wind_gust_ms", "wind_pressure_pa", "is_wet_hour", "driving_rain_pressure_pa", "is_breach_event"]].copy()
        csv_out.index = csv_out.index.strftime("%Y-%m-%d %H:%M")
        st.download_button(label="⬇️ Download CSV", data=csv_out.to_csv().encode("utf-8"), file_name=f"driving_rain_{city_name}_{start_date}_{end_date}.csv", mime="text/csv")

else:
    st.info("👈 Enter a city name and date range in the sidebar, then click **Analyze Storm Data** to begin.")
