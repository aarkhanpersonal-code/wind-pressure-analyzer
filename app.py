import time
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date, timedelta
from geopy.geocoders import ArcGIS
import geopy.exc
import openmeteo_requests
import requests_cache
from retry_requests import retry

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Facade Wind Pressure Analyzer",
    page_icon="🏗️",
    layout="wide",
)

# =============================================================================
# CONSTANTS — ASTM / AAMA THRESHOLDS
# =============================================================================
THRESHOLDS = {
    "Windows (ASTM E331)": {"pa": 137, "color": "#2196F3", "dash": "dot"},
    "Curtain Wall (AAMA 501)": {"pa": 300, "color": "#FF9800", "dash": "dash"},
    "Masonry / Cladding (ASTM E514)": {"pa": 480, "color": "#F44336", "dash": "dashdot"},
}

# =============================================================================
# GEOCODING — Switched to ArcGIS to avoid 429 limits
# =============================================================================
@st.cache_data(show_spinner="Geocoding location...")
def geocode_city(city_name):
    try:
        # ArcGIS is much more forgiving and doesn't require a user_agent string
        geolocator = ArcGIS(timeout=10)
        
        location = geolocator.geocode(city_name)
        
        if location is None:
            return None
        return location.latitude, location.longitude, location.address
        
    except geopy.exc.GeocoderTimedOut:
        st.error("⏱️ Geocoding timed out. Please try again.")
        return None
    except geopy.exc.GeocoderUnavailable:
        st.error("🌐 Geocoding service unavailable. Check your connection.")
        return None
    except Exception as e:
        st.error(f"Geocoding error: {e}")
        return None

# =============================================================================
# WEATHER FETCH (CACHED)
# =============================================================================
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
        "hourly": ["wind_gusts_10m", "wind_speed_10m"],
        "wind_speed_unit": "kmh",
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
            "wind_gust_kmh": hourly.Variables(0).ValuesAsNumpy(),
            "wind_speed_kmh": hourly.Variables(1).ValuesAsNumpy(),
        })
        df.set_index("time", inplace=True)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        st.error(f"❌ Failed to fetch weather data: {e}")
        return None

# =============================================================================
# PHYSICS ENGINE — P = 0.613 × V²
# =============================================================================
def calculate_pressure(df):
    result = df.copy()
    result["wind_gust_ms"] = result["wind_gust_kmh"] / 3.6
    result["wind_speed_ms"] = result["wind_speed_kmh"] / 3.6
    result["gust_pressure_pa"] = 0.613 * result["wind_gust_ms"] ** 2
    result["avg_pressure_pa"] = 0.613 * result["wind_speed_ms"] ** 2
    result["month"] = result.index.month
    result["year"] = result.index.year
    return result

# =============================================================================
# RETURN PERIOD
# =============================================================================
def calc_return_period(df, threshold_pa, years_of
