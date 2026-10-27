"""Plotly figures and pandas tables for the Streamlit dashboard.

These are pure functions of a :class:`~flweatherops.service.Briefing`, with no
Streamlit imports, so they can be unit-tested and reused, e.g. in a notebook.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from flweatherops import units as U
from flweatherops.astro import AstroHour
from flweatherops.config import Location
from flweatherops.data import HourData
from flweatherops.safety import Status, Verdict
from flweatherops.service import Briefing
from flweatherops.tropical import TropicalReport
from flweatherops.windows import SafeWindow

STATUS_COLOR = {Status.SAFE: "#2e7d32", Status.CAUTION: "#f9a825", Status.NOGO: "#c62828"}
STATUS_BG = {Status.SAFE: "#e8f5e9", Status.CAUTION: "#fff8e1", Status.NOGO: "#ffebee"}
SERIES = {
    "wind": "#1565c0", "gust": "#6a1b9a", "w80": "#00897b", "w120": "#ef6c00",
    "precip": "#1e88e5", "wave": "#0277bd", "period": "#8d6e63", "temp": "#e53935",
    "feels": "#fb8c00", "cloud": "#78909c",
}
LAYOUT = dict(
    height=300,
    margin=dict(l=10, r=10, t=40, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),
)


def _conv(mph: float | None, units: str) -> float | None:
    if mph is None:
        return None
    return round(mph / U.MPH_PER_KNOT, 1) if units == "kn" else round(mph, 1)


def _times(hours: list[HourData]) -> list[datetime]:
    return [h.time for h in hours]


def _shade_status(fig: go.Figure, hours: list[HourData], verdicts: list[Verdict]) -> None:
    """Shade runs of Caution/No-Go hours in the background of a chart."""
    run_start: datetime | None = None
    run_status: Status | None = None
    for h, v in [*zip(hours, verdicts), (None, None)]:
        st = v.status if v else None
        if st != run_status:
            if run_status in (Status.CAUTION, Status.NOGO) and run_start is not None:
                end = h.time if h else hours[-1].time + timedelta(hours=1)
                fig.add_vrect(x0=run_start, x1=end, fillcolor=STATUS_COLOR[run_status],
                              opacity=0.08, line_width=0, layer="below")
            run_start, run_status = (h.time if h else None), st


def _hline(fig: go.Figure, y: Any, color: str, text: str) -> None:
    if y is None:
        return
    fig.add_hline(y=y, line_dash="dot", line_color=color, line_width=1,
                  annotation_text=text, annotation_position="top left",
                  annotation_font_size=10, annotation_font_color=color)


def wind_chart(
    b: Briefing, units: str, hours: int, thresholds: dict[str, Any], activity: str = "drone"
) -> go.Figure:
    """10 m wind + gusts and 80/120 m wind, with the activity's gust limits."""
    hs = b.data.hours[:hours]
    t = _times(hs)
    fig = go.Figure()
    fig.add_scatter(x=t, y=[_conv(h.wind_mph, units) for h in hs], name="Wind 10 m",
                    line=dict(color=SERIES["wind"], width=2.5))
    fig.add_scatter(x=t, y=[_conv(h.gust_mph, units) for h in hs], name="Gusts 10 m",
                    line=dict(color=SERIES["gust"], width=1.5, dash="dash"))
    fig.add_scatter(x=t, y=[_conv(h.wind80_mph, units) for h in hs], name="Wind 80 m",
                    line=dict(color=SERIES["w80"], width=1.5))
    fig.add_scatter(x=t, y=[_conv(h.wind120_mph, units) for h in hs], name="Wind 120 m (~400 ft)",
                    line=dict(color=SERIES["w120"], width=1.5))
    gl = thresholds.get(activity, {}).get("wind_gust_mph", {})
    _hline(fig, _conv(gl.get("caution"), units), STATUS_COLOR[Status.CAUTION], f"{activity} gust caution")
    _hline(fig, _conv(gl.get("nogo"), units), STATUS_COLOR[Status.NOGO], f"{activity} gust no-go")
    if activity in b.hourly:
        _shade_status(fig, hs, b.hourly[activity][:hours])
    fig.update_layout(title=f"Wind ({U.wind_unit(units)}). Shading = {activity} Caution/No-Go",
                      yaxis_title=U.wind_unit(units), **LAYOUT)
    return fig


def precip_chart(b: Briefing, hours: int, thresholds: dict[str, Any]) -> go.Figure:
    """Precipitation probability bars + amount line."""
    hs = b.data.hours[:hours]
    t = _times(hs)
    fig = go.Figure()
    fig.add_bar(x=t, y=[h.precip_prob_pct for h in hs], name="Rain chance %",
                marker_color=SERIES["precip"], opacity=0.75)
    fig.add_scatter(x=t, y=[h.precip_in for h in hs], name="Rain in/h", yaxis="y2",
                    line=dict(color="#0d47a1", width=1.5))
    _hline(fig, thresholds.get("drone", {}).get("precip_prob_pct", {}).get("caution"),
           STATUS_COLOR[Status.CAUTION], "drone caution")
    _hline(fig, thresholds.get("marine", {}).get("precip_prob_pct", {}).get("caution"),
           "#ef6c00", "marine caution")
    fig.update_layout(
        title="Precipitation", yaxis=dict(title="%", range=[0, 100]),
        yaxis2=dict(title="in/h", overlaying="y", side="right", rangemode="tozero", showgrid=False),
        **LAYOUT,
    )
    return fig


def wave_chart(b: Briefing, hours: int, thresholds: dict[str, Any]) -> go.Figure:
    """Significant wave height, swell and wind wave + dominant period."""
    hs = b.data.hours[:hours]
    t = _times(hs)
    fig = go.Figure()
    fig.add_scatter(x=t, y=[h.wave_ft for h in hs], name="Wave height ft",
                    line=dict(color=SERIES["wave"], width=2.5), fill="tozeroy",
                    fillcolor="rgba(2,119,189,0.12)")
    fig.add_scatter(x=t, y=[h.swell_ft for h in hs], name="Swell ft",
                    line=dict(color="#4fc3f7", width=1.5, dash="dash"))
    fig.add_scatter(x=t, y=[h.wind_wave_ft for h in hs], name="Wind wave ft",
                    line=dict(color="#90a4ae", width=1.5, dash="dot"))
    fig.add_scatter(x=t, y=[h.wave_period_s for h in hs], name="Period s", yaxis="y2",
                    line=dict(color=SERIES["period"], width=1))
    wl = thresholds.get("marine", {}).get("wave_height_ft", {})
    _hline(fig, wl.get("caution"), STATUS_COLOR[Status.CAUTION], "marine caution")
    _hline(fig, wl.get("nogo"), STATUS_COLOR[Status.NOGO], "marine no-go")
    if "marine" in b.hourly:
        _shade_status(fig, hs, b.hourly["marine"][:hours])
    fig.update_layout(
        title="Waves. Shading = marine Caution/No-Go",
        yaxis=dict(title="ft", rangemode="tozero"),
        yaxis2=dict(title="s", overlaying="y", side="right", rangemode="tozero", showgrid=False),
        **LAYOUT,
    )
    return fig


def temp_chart(b: Briefing, hours: int) -> go.Figure:
    """Temperature and apparent temperature (heat index), with night shading."""
    hs = b.data.hours[:hours]
    t = _times(hs)
    fig = go.Figure()
    fig.add_scatter(x=t, y=[h.temp_f for h in hs], name="Temp °F",
                    line=dict(color=SERIES["temp"], width=2.5))
    fig.add_scatter(x=t, y=[h.heat_index_f for h in hs], name="Feels like °F",
                    line=dict(color=SERIES["feels"], width=1.5, dash="dash"))
    start = None
    for h in [*hs, None]:
        night = h is not None and not h.is_day
        if night and start is None:
            start = h.time
        elif not night and start is not None:
            end = h.time if h else hs[-1].time + timedelta(hours=1)
            fig.add_vrect(x0=start, x1=end, fillcolor="#37474f", opacity=0.07, line_width=0, layer="below")
            start = None
    fig.update_layout(title="Temperature. Shading = night", yaxis_title="°F", **LAYOUT)
    return fig


def status_timeline(b: Briefing, hours: int) -> go.Figure:
    """One colored strip per activity: green/yellow/red by hour."""
    hs = b.data.hours[:hours]
    fig = go.Figure()
    for row, act in enumerate(b.activities):
        vs = b.hourly[act][:hours]
        for st in Status:
            idx = [i for i, v in enumerate(vs) if v.status == st]
            if not idx:
                continue
            fig.add_bar(
                x=[3600 * 1000] * len(idx),  # 1 h in ms
                base=[hs[i].time for i in idx],
                y=[act.title()] * len(idx),
                orientation="h",
                marker_color=STATUS_COLOR[st],
                name=st.label,
                legendgroup=st.label,
                showlegend=row == 0,
                hovertext=[
                    f"{hs[i].time:%a %H:%M}: {st.label}"
                    + ("<br>" + "<br>".join(r.text() for r in vs[i].reasons[:3]) if vs[i].reasons else "")
                    for i in idx
                ],
                hoverinfo="text",
            )
    fig.update_layout(
        barmode="overlay", height=90 + 45 * len(b.activities), xaxis=dict(type="date"),
        margin=dict(l=10, r=10, t=30, b=10), title="Status by hour",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    return fig


def hourly_frame(b: Briefing, units: str, hours: int) -> pd.DataFrame:
    """Hourly table for display (display units, status + top reason per activity)."""
    wu = U.wind_unit(units)
    rows = []
    for i, h in enumerate(b.data.hours[:hours]):
        row: dict[str, Any] = {
            "Time": h.time.strftime("%a %H:%M"),
            "Sky": _wx(h.weather_code),
            "°F": h.temp_f,
            "Feels °F": h.heat_index_f,
            "Rain %": h.precip_prob_pct,
            f"Wind {wu}": _conv(h.wind_mph, units),
            f"Gust {wu}": _conv(h.gust_mph, units),
            "Dir": U.compass(h.wind_dir),
            f"Aloft {wu}": _conv(h.wind_aloft_mph, units),
            "Vis mi": None if h.visibility_mi is None else round(h.visibility_mi, 1),
            "CAPE": h.cape,
            "UV": h.uv_index,
        }
        if b.data.marine_available:
            row["Waves ft"] = None if h.wave_ft is None else round(h.wave_ft, 1)
            row["Period s"] = h.wave_period_s
        for act in b.activities:
            v = b.hourly[act][i]
            row[act.title()] = v.status.label
            row[f"{act.title()} why"] = "; ".join(r.text(units) for r in v.reasons[:2])
        rows.append(row)
    df = pd.DataFrame(rows)
    status = [a.title() for a in b.activities]
    why = [f"{a.title()} why" for a in b.activities]
    rest = [c for c in df.columns if c not in ("Time", *status, *why)]
    return df[["Time", *status, *rest, *why]] if len(df) else df


def style_hourly(df: pd.DataFrame) -> Any:
    """pandas Styler coloring status columns."""
    colors = {s.label: f"background-color: {STATUS_BG[s]}; color: {STATUS_COLOR[s]}; font-weight: 600"
              for s in Status}
    status_cols = [c for c in ("Drone", "Marine") if c in df.columns]
    fmt = {c: "{:.0f}" for c in df.columns if df[c].dtype.kind == "f"}
    for c in ("Vis mi", "Waves ft"):
        if c in df.columns:
            fmt[c] = "{:.1f}"
    return (
        df.style.map(lambda v: colors.get(v, ""), subset=status_cols)
        .format(fmt, na_rep="—")
    )


def windows_frame(windows: list[SafeWindow], today: Any, units: str) -> pd.DataFrame:
    """Safe windows as a table."""
    wu = U.wind_unit(units)
    return pd.DataFrame([
        {
            "Window": w.label(today),
            "Hours": w.duration_h,
            f"Max wind {wu}": _conv(w.max_wind_mph, units),
            f"Max gust {wu}": _conv(w.max_gust_mph, units),
            "Max waves ft": None if w.max_wave_ft is None else round(w.max_wave_ft, 1),
        }
        for w in windows
    ])


def storm_map(report: TropicalReport, locations: list[Location], highlight: Location | None = None,
              caution_mi: float = 400, nogo_mi: float = 200) -> go.Figure:
    """Map of active storms (track + current position) and locations with alert radii."""
    fig = go.Figure()
    for loc in locations:
        fig.add_scattergeo(
            lat=[loc.lat], lon=[loc.lon], mode="markers",
            marker=dict(size=10 if loc is highlight else 6,
                        color="#1565c0" if loc is highlight else "#546e7a"),
            name=loc.name, text=[loc.name], hoverinfo="text", showlegend=False,
        )
    if highlight is not None:
        for radius, color in ((caution_mi, STATUS_COLOR[Status.CAUTION]), (nogo_mi, STATUS_COLOR[Status.NOGO])):
            lats, lons = _circle(highlight.lat, highlight.lon, radius)
            fig.add_scattergeo(lat=lats, lon=lons, mode="lines", line=dict(color=color, width=1, dash="dot"),
                               name=f"{radius:.0f} mi", hoverinfo="skip")
    for s in report.storms:
        lats = [s.lat] + [p.lat for p in s.track]
        lons = [s.lon] + [p.lon for p in s.track]
        labels = ["now"] + [f"{p.time:%a %HZ}" + (f" {p.max_wind_kt} kt" if p.max_wind_kt else "") for p in s.track]
        fig.add_scattergeo(lat=lats, lon=lons, mode="lines+markers",
                           line=dict(color="#c62828", width=2, dash="dash" if s.track_source == "extrapolated" else "solid"),
                           marker=dict(size=[14] + [6] * len(s.track), color="#c62828", symbol="circle"),
                           name=s.title, text=[f"{s.title}: {l}" for l in labels], hoverinfo="text")
    fig.update_geos(
        projection_type="mercator", showland=True, landcolor="#eceff1", showocean=True,
        oceancolor="#e3f2fd", showcountries=True, countrycolor="#b0bec5",
        showsubunits=True, subunitcolor="#cfd8dc",
        lataxis_range=[15, 37], lonaxis_range=[-98, -60],
    )
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", yanchor="bottom", y=0, x=0))
    return fig


def _circle(lat: float, lon: float, radius_mi: float, n: int = 72) -> tuple[list[float], list[float]]:
    lats, lons = [], []
    for k in range(n + 1):
        a = 2 * math.pi * k / n
        dlat = radius_mi * math.cos(a) / 69.0
        dlon = radius_mi * math.sin(a) / (69.0 * math.cos(math.radians(lat)))
        lats.append(lat + dlat)
        lons.append(lon + dlon)
    return lats, lons


def astro_chart(rated: list[AstroHour], moon: list[tuple[datetime, float]]) -> go.Figure:
    """Astro score bars per night hour, with cloud cover and hourly moon altitude."""
    t = [a.time for a in rated]
    fig = go.Figure()
    fig.add_bar(x=t, y=[a.score for a in rated], name="Astro score",
                marker_color=["#2e7d32" if a.score >= 75 else "#7cb342" if a.score >= 55
                              else "#f9a825" if a.score >= 35 else "#c62828" for a in rated])
    fig.add_scatter(x=t, y=[a.cloud_pct for a in rated], name="Cloud %",
                    line=dict(color=SERIES["cloud"], width=1.5))
    fig.add_scatter(x=[m[0] for m in moon], y=[round(m[1]) for m in moon], name="Moon alt °",
                    line=dict(color="#fdd835", width=1.5, dash="dot"), yaxis="y2")
    fig.update_layout(
        title="Night-sky quality (gaps = daytime)",
        yaxis=dict(title="score / cloud %", range=[0, 100]),
        yaxis2=dict(title="moon altitude °", overlaying="y", side="right", range=[-90, 90],
                    dtick=30, showgrid=False, zeroline=True, zerolinecolor="#fdd835"),
        **LAYOUT,
    )
    return fig


WX_SHORT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    80: "Showers", 81: "Showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "T-storm/hail",
    99: "T-storm/hail",
}


def _wx(code: int | None) -> str:
    return "—" if code is None else WX_SHORT.get(code, f"Code {code}")
