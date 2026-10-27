"""flWeatherOps Streamlit dashboard.

Run with:   streamlit run dashboard.py

It uses the same data layer and safety logic as the CLI
(``flweatherops.service.build_briefing``). API calls are cached with
``st.cache_data``: forecasts for 10 minutes and the NHC check for 15 minutes.
The ↻ Refresh button clears both caches.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pandas as pd
import streamlit as st

from flweatherops import charts, history
from flweatherops import units as U
from flweatherops.astro import astro_hours, best_astro_block, moon_track
from flweatherops.config import ConfigError, Location, load_config, locations_from_config
from flweatherops.data import FetchError, geocode
from flweatherops.safety import Status
from flweatherops.service import Briefing, build_briefing, get_tropical, log_history
from flweatherops.tropical import TropicalReport
from flweatherops.windows import find_windows, rank_windows

st.set_page_config(page_title="flWeatherOps", page_icon="🌤️", layout="wide")

st.markdown(
    """
<style>
.block-container {padding-top: 3rem; padding-bottom: 2rem;}
.fwo-card {border-radius: 12px; padding: 14px 16px; margin-bottom: 8px; color: #1b1b1b;
           border: 1px solid rgba(0,0,0,0.08);}
.fwo-card .t {font-size: 0.95rem; font-weight: 600; opacity: 0.8;}
.fwo-card .s {font-size: 2.1rem; font-weight: 800; line-height: 1.15; letter-spacing: 0.5px;}
.fwo-card .r {font-size: 0.92rem; margin-top: 4px;}
.fwo-card .x {font-size: 0.82rem; margin-top: 6px; opacity: 0.75;}
@media (max-width: 640px) { .fwo-card .s {font-size: 1.7rem;} }
</style>
""",
    unsafe_allow_html=True,
)

ICON = {"info": "ℹ️", "warn": "⚠️", "alert": "⛔"}
ACT_LABEL = {"drone": "🛸 Drone", "marine": "🚤 Marine"}


# --------------------------------------------------------------------------- #
# Cached data access
# --------------------------------------------------------------------------- #


def load_cfg() -> dict[str, Any]:
    try:
        return load_config()
    except ConfigError as exc:
        st.error(f"Config error: {exc}")
        st.stop()
        raise  # unreachable; keeps type checkers happy


@st.cache_data(ttl=900, show_spinner="Checking NHC for tropical systems…")
def cached_tropical(cfg_json: str) -> TropicalReport | None:
    return get_tropical(json.loads(cfg_json))


@st.cache_data(ttl=600, show_spinner="Fetching forecast…")
def cached_briefing(loc_json: str, cfg_json: str, hours: int, units: str) -> Briefing:
    cfg = json.loads(cfg_json)
    loc = Location(**json.loads(loc_json))
    b = build_briefing(loc, cfg, hours=hours, tropical=cached_tropical(cfg_json), units=units)
    log_history(b, cfg)  # rate-limited per location (history.min_interval_min)
    return b


@st.cache_data(ttl=86400, show_spinner="Looking up place…")
def cached_geocode(query: str) -> dict[str, Any] | None:
    try:
        return dataclasses.asdict(geocode(query))
    except FetchError:
        return None


# --------------------------------------------------------------------------- #
# Sidebar: location + options
# --------------------------------------------------------------------------- #

cfg = load_cfg()
cfg_json = json.dumps(cfg, sort_keys=True)
known = locations_from_config(cfg)

with st.sidebar:
    st.header("🌤️ flWeatherOps")
    mode = st.radio("Location", ["Saved", "Search", "Lat/Lon"], horizontal=True)
    loc: Location | None = None
    if mode == "Saved":
        keys = list(known)
        default_key = next((k for k, l in known.items() if l.default), keys[0])
        key = st.selectbox("Saved locations", keys, index=keys.index(default_key),
                           format_func=lambda k: known[k].name)
        loc = known[key]
    elif mode == "Search":
        q = st.text_input("City or place", placeholder="e.g. Jupiter, FL")
        inland = st.checkbox("Inland (skip waves/tides)", key="inland_search")
        if q:
            found = cached_geocode(q)
            if found:
                loc = dataclasses.replace(Location(**found), coastal=not inland)
                st.caption(f"📍 {loc.name} ({loc.lat:.3f}, {loc.lon:.3f})")
            else:
                st.warning("No match found.")
    else:
        c1, c2 = st.columns(2)
        lat = c1.number_input("Latitude", value=26.70, min_value=-90.0, max_value=90.0, format="%.4f")
        lon = c2.number_input("Longitude", value=-80.03, min_value=-180.0, max_value=180.0, format="%.4f")
        name = st.text_input("Label", value=f"{lat:.3f}, {lon:.3f}")
        inland = st.checkbox("Inland (skip waves/tides)", key="inland_ll")
        loc = Location(key="custom", name=name, lat=lat, lon=lon, coastal=not inland)

    hours = st.slider("Forecast hours to show", 12, 72, 48, step=6)
    units = st.radio("Wind units", ["mph", "kn"], horizontal=True)
    if st.button("↻ Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(
        "Weather: [Open-Meteo](https://open-meteo.com) (CC BY 4.0, free tier is for "
        "non-commercial use) · Tides: NOAA CO-OPS · Tropical: NOAA NHC. "
        "Model guidance only. Always check official NWS forecasts, airspace (B4UFLY) and "
        "marine warnings."
    )

if loc is None:
    st.info("Choose a location in the sidebar to get a briefing.")
    st.stop()

try:
    b = cached_briefing(json.dumps(dataclasses.asdict(loc)), cfg_json, hours, units)
except FetchError as exc:
    st.error(f"Could not get a forecast for {loc.name}: {exc}")
    st.stop()

data = b.data
cur = data.current
wu = U.wind_unit(units)
th = cfg["thresholds"]

# --------------------------------------------------------------------------- #
# Header, alerts, status cards
# --------------------------------------------------------------------------- #

st.subheader(data.location.name)
st.caption(
    f"{loc.lat:.3f}, {loc.lon:.3f} · generated {data.generated:%a %b %d, %H:%M %Z} · "
    f"{'coastal' if loc.coastal else 'inland'}"
)
for note in data.notes:
    st.warning(note, icon="⚠️")

trop = b.tropical
report = trop.report if trop else None
if trop and trop.relevant:
    text = "  \n".join(f"**{i.level.label}**: {i.summary}" for i in trop.relevant)
    (st.error if trop.level == Status.NOGO else st.warning)(f"🌀 **Tropical alert**  \n{text}")
elif trop and trop.florida_alerts:
    kinds = ", ".join(sorted({f"{a.kind} ({s.name})" for s, a in trop.florida_alerts}))
    st.warning(f"🌀 NHC watches/warnings in effect for parts of Florida: {kinds}")
elif report is not None and not report.ok:
    st.caption(f"🌀 Tropical check unavailable: {report.error}")


def status_card(act: str) -> str:
    s = b.summary[act]
    v = s.now
    color, bg = charts.STATUS_COLOR[v.status], charts.STATUS_BG[v.status]
    reason = "; ".join(r.text(units) for r in v.reasons[:2]) if v.reasons else "All limits OK now"
    best = rank_windows(b.windows[act])
    extra = []
    if s.change:
        h, stt = s.change
        extra.append(f"→ {stt.label} from {h.time:%a %H:%M}")
    extra.append(f"Best window: {best[0].label(b.today)}" if best else "No Safe window in 72 h")
    c = s.counts
    extra.append(f"Next {b.horizon}h: {c[Status.SAFE]} safe · {c[Status.CAUTION]} caution · "
                 f"{c[Status.NOGO]} no-go")
    return (
        f'<div class="fwo-card" style="background:{bg}; border-left: 8px solid {color};">'
        f'<div class="t">{ACT_LABEL[act]}</div>'
        f'<div class="s" style="color:{color}">{v.status.label}</div>'
        f'<div class="r">{reason}</div>'
        f'<div class="x">{"<br>".join(extra)}</div></div>'
    )


cols = st.columns(len(b.activities))
for col, act in zip(cols, b.activities):
    col.markdown(status_card(act), unsafe_allow_html=True)

tabs = st.tabs(["Overview", "Charts", "Hourly", "Safe windows", "🌀 Tropical", "🌌 Astro", "History"])

# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #

with tabs[0]:
    st.markdown("##### Current conditions")
    m = st.columns(4)
    m[0].metric("Temp / feels", f"{U.num(cur.temp_f)}°F", f"feels {U.num(cur.heat_index_f)}°F",
                delta_color="off")
    m[1].metric(f"Wind ({wu})", f"{U.compass(cur.wind_dir)} {U.wind(cur.wind_mph, units)}",
                f"gusts {U.wind(cur.gust_mph, units)}", delta_color="off")
    m[2].metric(f"Aloft 80/120 m ({wu})",
                f"{U.wind(cur.wind80_mph, units)} / {U.wind(cur.wind120_mph, units)}")
    m[3].metric("Rain chance", U.num(cur.precip_prob_pct, suffix="%"))
    m = st.columns(4)
    m[0].metric("Visibility", U.num(cur.visibility_mi, "{:.1f}", " mi"))
    m[1].metric("Clouds (low)", f"{U.num(cur.cloud_cover_pct, suffix='%')}",
                f"low {U.num(cur.cloud_low_pct, suffix='%')}", delta_color="off")
    m[2].metric("UV / CAPE", f"{U.num(cur.uv_index)} / {U.num(cur.cape)}")
    if data.marine_available:
        m[3].metric("Waves", U.num(cur.wave_ft, "{:.1f}", " ft"),
                    f"{U.num(cur.wave_period_s)}s from {U.compass(cur.wave_dir)}", delta_color="off")
    else:
        m[3].metric("Humidity", U.num(cur.humidity_pct, suffix="%"))

    a_cols = st.columns(len(b.activities))
    for col, act in zip(a_cols, b.activities):
        with col:
            st.markdown(f"##### {ACT_LABEL[act]} notes")
            items = b.advice.get(act) or []
            if not items:
                st.caption("No special notes.")
            for a in items:
                st.markdown(f"{ICON.get(a.level, '•')} **{a.topic}:** {a.text}")

    if data.tides:
        st.markdown("##### Tides (MLLW)")
        upcoming = [e for e in data.tides if e.time >= cur.time][:6]
        st.dataframe(
            pd.DataFrame([{"Tide": e.kind, "Time": e.time.strftime("%a %H:%M"),
                           "Height ft": round(e.height_ft, 1)} for e in upcoming]),
            hide_index=True,
        )
        if data.tide_station:
            dist = data.tide_station.distance_mi
            st.caption(f"NOAA station {data.tide_station.name} ({data.tide_station.id})"
                       + (f", {dist:.0f} mi away" if dist is not None else ""))

# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #

with tabs[1]:
    st.plotly_chart(charts.status_timeline(b, hours))
    act_for_wind = st.segmented_control(
        "Wind limits shown for", b.activities, default=b.activities[0],
        format_func=lambda a: ACT_LABEL[a], key="wind_act",
    ) or b.activities[0]
    st.plotly_chart(charts.wind_chart(b, units, hours, th, act_for_wind))
    st.plotly_chart(charts.precip_chart(b, hours, th))
    if data.marine_available:
        st.plotly_chart(charts.wave_chart(b, hours, th))
    elif loc.coastal:
        st.caption("No wave data for this point.")
    st.plotly_chart(charts.temp_chart(b, hours))

# --------------------------------------------------------------------------- #
# Hourly table
# --------------------------------------------------------------------------- #

with tabs[2]:
    df = charts.hourly_frame(b, units, hours)
    st.dataframe(charts.style_hourly(df), hide_index=True, height=min(38 + 35 * len(df), 720))
    st.caption("Status columns are colored green, yellow or red. The “why” columns show the top reasons.")

# --------------------------------------------------------------------------- #
# Safe Window Finder
# --------------------------------------------------------------------------- #

with tabs[3]:
    wcfg = cfg.get("windows", {})
    c1, c2 = st.columns([2, 1])
    min_h = c1.slider("Minimum continuous Safe hours", 1, 8, int(wcfg.get("min_hours", 2)))
    daylight = c2.toggle("Drone: daylight only", value=bool(wcfg.get("drone_daylight_only", True)))
    st.caption(f"Scanning the next {len(data.hours)} hours.")
    for act in b.activities:
        wins = find_windows(data.hours, b.hourly[act], min_hours=min_h,
                            daylight_only=(act == "drone" and daylight))
        st.markdown(f"##### {ACT_LABEL[act]} Safe windows")
        if not wins:
            st.warning(f"No Safe window of {min_h}h or more in the next {len(data.hours)}h.")
            continue
        best = rank_windows(wins)[0]
        st.success(f"**Best {act} window:** {best.label(b.today)} ({best.duration_h}h, "
                   f"max gust {U.wind(best.max_gust_mph, units)} {wu})")
        st.markdown("**All:** " + ", ".join(w.label(b.today) for w in wins))
        st.dataframe(charts.windows_frame(wins, b.today, units), hide_index=True)
    st.plotly_chart(charts.status_timeline(b, len(data.hours)), key="timeline_full")

# --------------------------------------------------------------------------- #
# Tropical
# --------------------------------------------------------------------------- #

with tabs[4]:
    tcfg = cfg.get("tropical", {})
    if report is None:
        st.info("Tropical monitoring is disabled (`tropical.enabled` in config).")
    elif not report.ok:
        st.warning(f"NHC data unavailable: {report.error}. Check https://www.nhc.noaa.gov directly.")
    else:
        if report.cached_age is not None:
            st.caption(f"Live NHC request failed; showing cached data "
                       f"({int(report.cached_age.total_seconds() // 60)} min old).")
        if not report.storms:
            st.success("No active Atlantic tropical cyclones right now.")
        else:
            st.plotly_chart(charts.storm_map(
                report, list(known.values()) + ([loc] if loc.key not in known else []),
                highlight=loc,
                caution_mi=float(tcfg.get("caution_radius_mi", 400)),
                nogo_mi=float(tcfg.get("nogo_radius_mi", 200)),
            ))
            impacts = {i.storm.id: i for i in (trop.impacts if trop else [])}
            for s in report.storms:
                imp = impacts.get(s.id)
                label = f"{s.title} · {s.intensity_text}"
                with st.expander(label, expanded=bool(imp and imp.level > Status.SAFE)):
                    c = st.columns(3)
                    c[0].metric("Max wind", s.intensity_text.split(" (")[0])
                    c[1].metric("Pressure", f"{s.pressure_mb} mb" if s.pressure_mb else "—")
                    motion = (f"{U.compass(s.movement_dir)} {s.movement_speed_mph:.0f} mph"
                              if s.movement_dir is not None and s.movement_speed_mph is not None else "—")
                    c[2].metric("Moving toward", motion)
                    if imp:
                        (st.error if imp.level == Status.NOGO else
                         st.warning if imp.level == Status.CAUTION else st.info)(imp.summary)
                    else:
                        st.caption(f"Beyond {tcfg.get('list_radius_mi', 1500)} mi of {loc.name}. Not a factor.")
                    st.caption(f"Track source: {s.track_source}")
                    if s.alerts_text:
                        st.markdown("**Watches & warnings (NHC public advisory)**")
                        st.code(s.alerts_text.strip(), language=None)
                    for n in s.notes:
                        st.caption(f"⚠️ {n}")
        st.caption(
            f"Rules: within {tcfg.get('nogo_radius_mi', 200)} mi of a storm's position (interpolated "
            f"along the NHC forecast track) → No-Go. Within {tcfg.get('caution_radius_mi', 400)} mi → "
            "Caution. A watch or warning that appears to include this location also counts. "
            "This is a simple distance check, not the official cone. See "
            "[nhc.noaa.gov](https://www.nhc.noaa.gov)."
        )

# --------------------------------------------------------------------------- #
# Astro
# --------------------------------------------------------------------------- #

with tabs[5]:
    rated = astro_hours(data.hours, loc.lat, loc.lon, cfg.get("timezone", "America/New_York"))
    if not rated:
        st.info("No night hours in the forecast window.")
    else:
        moon = rated[0].moon
        best = best_astro_block(rated)
        c = st.columns(3)
        c[0].metric("Moon", moon.phase_name, f"{moon.illumination:.0%} illuminated", delta_color="off")
        sunset = next((s for _, s in data.sun if s >= cur.time), None)
        c[1].metric("Next sunset", f"{sunset:%a %H:%M}" if sunset else "—")
        if best:
            avg = sum(a.score for a in best) // len(best)
            c[2].metric("Best block", f"{best[0].time:%a %H:%M}–{best[-1].time:%H:%M}", f"avg score {avg}",
                        delta_color="off")
        else:
            c[2].metric("Best block", "none ≥ Good")
        st.plotly_chart(charts.astro_chart(
            rated, moon_track(data.hours, loc.lat, loc.lon, cfg.get("timezone", "America/New_York"))
        ))
        st.dataframe(pd.DataFrame([{
            "Time": a.time.strftime("%a %H:%M"), "Score": a.score, "Rating": a.rating,
            "Cloud %": a.cloud_pct, "Low/Mid/High %":
                f"{U.num(a.hour.cloud_low_pct)}/{U.num(a.hour.cloud_mid_pct)}/{U.num(a.hour.cloud_high_pct)}",
            "Vis mi": None if a.hour.visibility_mi is None else round(a.hour.visibility_mi, 1),
            "Moon alt °": round(a.moon.altitude_deg), "Notes": ", ".join(a.notes),
        } for a in rated]), hide_index=True)
        st.caption("Score 100 means clear, transparent and moonless. Penalties: cloud, high cirrus, "
                   "moon above the horizon (scaled by illumination), haze, dew and showers.")

# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #

with tabs[6]:
    all_locs = st.toggle("All locations", value=False)
    rows = history.recent(300, None if all_locs else loc.key)
    if not rows:
        st.info("No history yet. Each briefing is logged (at most every "
                f"{cfg.get('history', {}).get('min_interval_min', 30)} min per location).")
    else:
        hdf = pd.DataFrame([{
            "Logged": r.logged_at, "Location": r.location_name,
            "Drone": r.drone_status, "Marine": r.marine_status, "Tropical": r.tropical_status,
            f"Wind {wu}": charts._conv(r.wind_mph, units), f"Gust {wu}": charts._conv(r.gust_mph, units),
            f"Aloft {wu}": charts._conv(r.wind_aloft_mph, units),
            "Waves ft": r.wave_ft, "Rain %": r.precip_prob_pct, "°F": r.temp_f,
        } for r in rows])
        if not all_locs and len(hdf) > 1:
            st.line_chart(hdf.set_index("Logged")[[f"Wind {wu}", f"Gust {wu}", f"Aloft {wu}"]])
        colors = {s.label: f"color: {charts.STATUS_COLOR[s]}; font-weight: 600" for s in Status}
        st.dataframe(
            hdf.style.map(lambda v: colors.get(v, ""), subset=["Drone", "Marine", "Tropical"])
            .format({"Logged": lambda t: t.strftime("%a %b %d %H:%M")}, na_rep="—")
            .format(precision=1, na_rep="—", subset=[c for c in hdf.columns if hdf[c].dtype.kind == "f"]),
            hide_index=True,
        )
        st.caption(f"Stored in {history.db_path()}")
