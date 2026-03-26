import time
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
# GEOCODING — Fixed for Nominatim 429 errors
# =============================================================================
@st.cache_data(show_spinner="Geocoding location...")
def geocode_city(city_name):
    try:
        # Use a STATIC, identifiable user_agent. 
        # (It's best practice to put your actual email here)
        geolocator = Nominatim(
            user_agent="facade_wind_analyzer_v1_your_email@example.com", 
            timeout=10,
        )
        
        # Respect Nominatim's rule: max 1 request per second
        time.sleep(1)
        
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
def calc_return_period(df, threshold_pa, years_of_data):
    exceedances = (df["gust_pressure_pa"] >= threshold_pa).sum()
    if exceedances == 0:
        return None
    return round(years_of_data / exceedances, 1)

# =============================================================================
# SPEC STATUS LOGIC
# =============================================================================
def get_spec_status(exceedances_per_year):
    if exceedances_per_year == 0:
        return "✅ Safe", "green", "Never exceeded in the analysis period."
    elif exceedances_per_year < 1:
        return "✅ Acceptable", "green", f"Exceeded roughly once every {round(1/exceedances_per_year, 1)} years."
    elif exceedances_per_year < 5:
        return "⚠️ Borderline", "orange", f"Exceeded ~{round(exceedances_per_year, 1)}x per year — review your spec carefully."
    else:
        return "🚨 Upgrade Needed", "red", f"Exceeded ~{round(exceedances_per_year, 1)}x per year — this spec is insufficient for this location."

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.header("⚙️ Analysis Parameters")
    city_name = st.text_input(
        "City / Location",
        value="Halifax, Canada",
        help="Enter any city. Uses OpenStreetMap for geocoding.",
    )
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date",
            value=date.today() - timedelta(days=365 * 10),
            max_value=date.today() - timedelta(days=5),
        )
    with col2:
        end_date = st.date_input(
            "End Date",
            value=date.today() - timedelta(days=5),
            max_value=date.today() - timedelta(days=5),
        )
    st.divider()
    st.markdown("### 📐 Reference Thresholds")
    st.caption(
        "- **137 Pa** — Windows (ASTM E331)\n"
        "- **300 Pa** — Curtain Wall (AAMA 501)\n"
        "- **480 Pa** — Masonry / Cladding (ASTM E514)"
    )
    analyze_btn = st.button("🔍 Analyze Location", use_container_width=True, type="primary")

# =============================================================================
# HEADER
# =============================================================================
st.title("🏗️ Facade Wind Pressure Analyzer")
st.markdown(
    "A **design decision engine** for facade engineers and architects. "
    "Enter a city and date range to understand the real wind pressure demands "
    "on your building — backed by historical weather data, not just code estimates."
)
st.divider()

# =============================================================================
# ANALYSIS
# =============================================================================
if analyze_btn:

    if start_date >= end_date:
        st.error("❌ Start date must be before end date.")
        st.stop()

    years_of_data = (end_date - start_date).days / 365.25

    with st.spinner(f"Locating '{city_name}'…"):
        geo_result = geocode_city(city_name)

    if geo_result is None:
        st.error(f"Could not find **'{city_name}'**. Try a more specific name.")
        st.stop()

    lat, lon, display_address = geo_result
    st.success(f"📍 **{display_address}** ({lat:.4f}°N, {lon:.4f}°E) — {years_of_data:.1f} years of data")

    df_raw = fetch_weather(lat=lat, lon=lon, start_date=str(start_date), end_date=str(end_date))

    if df_raw is None or df_raw.empty:
        st.error("No weather data returned for this location and date range.")
        st.stop()

    df = calculate_pressure(df_raw)

    # =========================================================================
    # TOP METRICS
    # =========================================================================
    st.subheader("📊 At a Glance")

    peak_pa = df["gust_pressure_pa"].max()
    peak_gust = df["wind_gust_kmh"].max()
    p99_pa = df["gust_pressure_pa"].quantile(0.99)
    avg_pa = df["avg_pressure_pa"].mean()
    hours_above_480 = int((df["gust_pressure_pa"] >= 480).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🌪️ All-Time Peak Pressure", f"{peak_pa:.0f} Pa", f"{peak_gust:.0f} km/h gust")
    c2.metric("📈 99th Percentile Pressure", f"{p99_pa:.0f} Pa", help="Exceeded only 1% of the time — your practical design ceiling.")
    c3.metric("📉 Average Hourly Pressure", f"{avg_pa:.0f} Pa", help="Typical background wind load on the facade.")
    c4.metric("🚨 Hours Above 480 Pa", f"{hours_above_480:,}", f"{hours_above_480/years_of_data:.1f} per year", delta_color="inverse")

    st.divider()

    # =========================================================================
    # CHART 1 — MONTHLY HEATMAP
    # =========================================================================
    st.subheader("📅 Monthly Wind Pressure Heatmap")
    st.caption("Average gust pressure by month and year. Red = high pressure months. Reveals your peak design season and year-over-year trends.")

    MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    monthly_avg = df.groupby(["year", "month"])["gust_pressure_pa"].mean().reset_index()
    monthly_avg["month_name"] = monthly_avg["month"].apply(lambda m: MONTH_NAMES[m-1])
    pivot = monthly_avg.pivot(index="year", columns="month_name", values="gust_pressure_pa")
    pivot = pivot.reindex(columns=[m for m in MONTH_NAMES if m in pivot.columns])

    fig_heat = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        colorscale="RdYlBu_r",
        colorbar=dict(title="Avg Pa"),
        hovertemplate="Year: %{y}<br>Month: %{x}<br>Avg Pressure: %{z:.0f} Pa<extra></extra>",
    ))
    fig_heat.update_layout(
        xaxis_title="Month",
        yaxis_title="Year",
        height=350,
        margin=dict(t=20, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    # =========================================================================
    # CHART 2 — MONTHLY BAR CHART
    # =========================================================================
    st.subheader("📊 Average Pressure by Month (All Years Combined)")
    st.caption("Which months consistently demand the most from your facade system.")

    monthly_overall = df.groupby("month")["gust_pressure_pa"].mean().reset_index()
    monthly_overall["month_name"] = monthly_overall["month"].apply(lambda m: MONTH_NAMES[m-1])

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        x=monthly_overall["month_name"],
        y=monthly_overall["gust_pressure_pa"],
        marker_color=[
            "#F44336" if p >= 300 else "#FF9800" if p >= 137 else "#2196F3"
            for p in monthly_overall["gust_pressure_pa"]
        ],
        hovertemplate="<b>%{x}</b><br>Avg Pressure: %{y:.0f} Pa<extra></extra>",
    ))
    for name, vals in THRESHOLDS.items():
        fig_bar.add_hline(
            y=vals["pa"],
            line_dash=vals["dash"],
            line_color=vals["color"],
            line_width=1.5,
            annotation_text=f"{name.split('(')[0].strip()} {vals['pa']} Pa",
            annotation_position="right",
            annotation_font_color=vals["color"],
            annotation_font_size=11,
        )
    fig_bar.update_layout(
        xaxis_title="Month",
        yaxis_title="Average Gust Pressure (Pa)",
        height=380,
        margin=dict(t=20, b=40, r=220),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)"),
        showlegend=False,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # =========================================================================
    # CHART 3 — PRESSURE EXCEEDANCE CURVE
    # =========================================================================
    st.subheader("📈 Pressure Exceedance Curve")
    st.caption("How many hours per year does pressure exceed a given level? Read off each threshold line to get your spec inputs directly.")

    pressure_levels = np.arange(0, df["gust_pressure_pa"].max() + 50, 10)
    exceedance_hours = [(df["gust_pressure_pa"] >= p).sum() / years_of_data for p in pressure_levels]

    fig_exc = go.Figure()
    fig_exc.add_trace(go.Scatter(
        x=pressure_levels,
        y=exceedance_hours,
        mode="lines",
        line=dict(color="#1565C0", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(21, 101, 192, 0.1)",
        hovertemplate="Pressure: %{x:.0f} Pa<br>Exceeded: %{y:.1f} hrs/year<extra></extra>",
    ))
    for name, vals in THRESHOLDS.items():
        hrs = (df["gust_pressure_pa"] >= vals["pa"]).sum() / years_of_data
        fig_exc.add_vline(
            x=vals["pa"],
            line_dash=vals["dash"],
            line_color=vals["color"],
            line_width=2,
            annotation_text=f"{vals['pa']} Pa → {hrs:.1f} hrs/yr",
            annotation_position="top right",
            annotation_font_color=vals["color"],
            annotation_font_size=11,
        )
    fig_exc.update_layout(
        xaxis_title="Wind Pressure (Pa)",
        yaxis_title="Hours Exceeded Per Year (avg)",
        height=400,
        margin=dict(t=20, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)"),
        xaxis=dict(gridcolor="rgba(128,128,128,0.15)"),
        showlegend=False,
    )
    st.plotly_chart(fig_exc, use_container_width=True)

    st.divider()

    # =========================================================================
    # TOP 10 WORST STORMS
    # =========================================================================
    st.subheader("🌪️ Top 10 Worst Recorded Storm Events")
    st.caption("The highest pressure hours ever recorded at this location. Correlate these dates with any known damage reports.")

    top10 = df[["wind_gust_kmh", "wind_speed_kmh", "gust_pressure_pa"]].nlargest(10, "gust_pressure_pa").copy()
    top10.index = top10.index.strftime("%Y-%m-%d %H:%M UTC")
    top10.columns = ["Peak Gust (km/h)", "Avg Wind (km/h)", "Gust Pressure (Pa)"]
    top10.insert(0, "Rank", range(1, 11))

    def highlight_row(row):
        pa = row["Gust Pressure (Pa)"]
        if pa >= 480:
            return ["background-color: rgba(244,67,54,0.15)"] * len(row)
        elif pa >= 300:
            return ["background-color: rgba(255,152,0,0.15)"] * len(row)
        elif pa >= 137:
            return ["background-color: rgba(33,150,243,0.15)"] * len(row)
        return [""] * len(row)

    st.dataframe(top10.style.apply(highlight_row, axis=1).format(precision=1), use_container_width=True)

    st.divider()

    # =========================================================================
    # FACADE SPECIFICATION CARD
    # =========================================================================
    st.subheader("📋 Facade Specification Recommendation")
    st.caption("Automatic spec guidance based on real pressure data — ready to include in a design report or client presentation.")

    worst_month = monthly_overall.loc[monthly_overall["gust_pressure_pa"].idxmax(), "month_name"]
    best_month = monthly_overall.loc[monthly_overall["gust_pressure_pa"].idxmin(), "month_name"]

    st.markdown(
        f"#### 📍 {city_name} &nbsp;|&nbsp; 📅 {start_date} → {end_date} ({years_of_data:.1f} years)\n"
        f"**Peak design month:** {worst_month} &nbsp;|&nbsp; "
        f"**Lowest risk month:** {best_month} &nbsp;|&nbsp; "
        f"**99th percentile pressure:** {p99_pa:.0f} Pa"
    )

    color_map = {"green": "#E8F5E9", "orange": "#FFF3E0", "red": "#FFEBEE"}
    border_map = {"green": "#4CAF50", "orange": "#FF9800", "red": "#F44336"}

    for name, vals in THRESHOLDS.items():
        total_exceedances = (df["gust_pressure_pa"] >= vals["pa"]).sum()
        per_year = total_exceedances / years_of_data
        rp = calc_return_period(df, vals["pa"], years_of_data)
        rp_str = f"Once every {rp} years" if rp else "Never in this period"
        status, color, note = get_spec_status(per_year)

        bg = color_map[color]
        border = border_map[color]

        st.markdown(
            f"""
            <div style="
                background-color: {bg};
                border-left: 5px solid {border};
                border-radius: 6px;
                padding: 14px 18px;
                margin-bottom: 12px;
            ">
                <div style="font-size: 1.05em; font-weight: 700; margin-bottom: 4px;">
                    {name} — {vals['pa']} Pa
                </div>
                <div style="font-size: 1.15em; font-weight: 800;">{status}</div>
                <div style="margin-top: 6px; color: #444; font-size: 0.9em;">
                    📊 Exceeded <b>{per_year:.1f}x per year</b> &nbsp;|&nbsp;
                    🔁 Return period: <b>{rp_str}</b><br>
                    💬 {note}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()

    # =========================================================================
    # DOWNLOAD
    # =========================================================================
    with st.expander("📥 Download Full Dataset (CSV)"):
        csv_out = df[["wind_gust_kmh", "wind_speed_kmh", "wind_gust_ms", "gust_pressure_pa", "avg_pressure_pa"]].copy()
        csv_out.columns = ["Wind Gust (km/h)", "Wind Speed (km/h)", "Wind Gust (m/s)", "Gust Pressure (Pa)", "Avg Pressure (Pa)"]
        csv_out.index = csv_out.index.strftime("%Y-%m-%d %H:%M")
        st.download_button(
            label="⬇️ Download CSV",
            data=csv_out.to_csv().encode("utf-8"),
            file_name=f"facade_wind_{city_name.replace(' ','_')}_{start_date}_{end_date}.csv",
            mime="text/csv",
        )

else:
    # =========================================================================
    # LANDING PAGE
    # =========================================================================
    st.info("👈 Enter a city and date range in the sidebar, then click **Analyze Location** to begin.")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
        #### 📅 Monthly Heatmap
        See which months and years produce the highest wind pressures — identify your peak design season at a glance.
        """)
    with col2:
        st.markdown("""
        #### 📈 Exceedance Curve
        Discover how often pressure exceeds each ASTM threshold per year — the core input for choosing your facade spec.
        """)
    with col3:
        st.markdown("""
        #### 📋 Specification Card
        Get an automatic facade recommendation with return periods — ready to drop into a design report or client deck.
        """)

    st.divider()
    st.markdown("""
    #### 🔍 Suggested Cities to Try
    | City | Why Interesting |
    |------|----------------|
    | Wellington, New Zealand | Windiest city on Earth |
    | Halifax, Nova Scotia | Severe nor'easters |
    | Miami, Florida | Hurricane season exposure |
    | Aberdeen, Scotland | North Sea winter storms |
    | Phoenix, Arizona | Baseline — very low pressure |

    **Tip:** Use a 10-year date range for the most reliable specification results.
    """)
