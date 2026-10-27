"""Rich terminal rendering for briefings."""

from __future__ import annotations

from dataclasses import dataclass
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from flweatherops import units as U
from flweatherops.data import HourData, LocationData
from flweatherops.safety import Status, Verdict, WindowSummary

ATTRIBUTION = (
    "Weather data: Open-Meteo.com (CC BY 4.0; free tier is for non-commercial use). "
    "Tides: NOAA CO-OPS. Model guidance only — always check official NWS forecasts, "
    "TFRs/airspace (B4UFLY) and marine warnings before you go."
)

WMO: dict[int, str] = {
    0: "Clear", 1: "Mostly clr", 2: "Pt cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Lt drizzle", 53: "Drizzle", 55: "Hvy drizzle",
    56: "Frz drizzle", 57: "Frz drizzle",
    61: "Lt rain", 63: "Rain", 65: "Hvy rain", 66: "Frz rain", 67: "Frz rain",
    71: "Lt snow", 73: "Snow", 75: "Hvy snow", 77: "Snow grains",
    80: "Lt showers", 81: "Showers", 82: "Hvy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "T-storm", 96: "T-storm+hail", 99: "T-storm+hail",
}

# Reason label -> hourly-table column that should be highlighted.
_HIGHLIGHT = {
    "Wind": "wind",
    "Gusts": "gust",  # merged into the wind column
    "Wind aloft (80–120 m)": "aloft",
    "Rain chance": "rain",
    "Rain amount": "rain",
    "Visibility": "vis",
    "Waves": "waves",
    "Heat index": "temp",
    "Lightning": "cape",
}
_LEVEL_STYLE = {Status.CAUTION: "yellow", Status.NOGO: "bold red"}


@dataclass
class Briefing:
    """Data + evaluated verdicts for one location."""

    data: LocationData
    activities: list[str]
    hourly: dict[str, list[Verdict]]
    now: dict[str, Verdict]
    summary: dict[str, WindowSummary]


def wx_text(code: int | None) -> str:
    """Short description for a WMO weather code."""
    return "—" if code is None else WMO.get(code, f"Code {code}")


def badge(status: Status, short: bool = False) -> Text:
    """Colored status badge."""
    if short:
        label = {Status.SAFE: "SAFE", Status.CAUTION: "CAUT", Status.NOGO: "NOGO"}[status]
        return Text(label, style=status.style)
    return Text(f" {status.label} ", style=f"{status.style} reverse")


def _window_text(s: WindowSummary) -> str:
    if not s.best_window:
        return "none in window"
    a, b = s.best_window
    same_day = a.time.date() == b.time.date()
    start = U.hour_label(a.time, with_day=True)
    end = U.hour_label(b.time, with_day=not same_day)
    hours = int((b.time - a.time).total_seconds() // 3600) + 1
    return f"{start} – {end} ({hours}h)" if a is not b else f"{start} (1h)"


def _why(s: WindowSummary, wind_units: str) -> Text:
    out = Text()
    if s.now.reasons:
        out.append("; ".join(r.text(wind_units) for r in s.now.reasons[:3]))
    else:
        out.append("All limits OK now", style="green")
    if s.change:
        h, st = s.change
        out.append(f"\n→ {st.label} from {U.hour_label(h.time, with_day=True)}", style=st.style)
    if s.top_factors and s.worst > Status.SAFE:
        out.append(f"\nWatch: {', '.join(s.top_factors)}", style="dim")
    return out


def summary_table(b: Briefing, wind_units: str) -> Table:
    """Top-of-briefing recommendation table."""
    n = len(b.data.hours)
    t = Table(box=box.ROUNDED, expand=True, title="Recommendation", title_style="bold")
    t.add_column("Activity", style="bold", no_wrap=True)
    t.add_column("Now", justify="center", no_wrap=True)
    t.add_column(f"Next {n}h outlook", min_width=24)
    t.add_column("Why / what to watch", ratio=1, min_width=28)
    for act in b.activities:
        s = b.summary[act]
        c = s.counts
        outlook = Text()
        outlook.append(f"{c[Status.SAFE]} safe", style="green")
        outlook.append(" · ")
        outlook.append(f"{c[Status.CAUTION]} caut", style="yellow")
        outlook.append(" · ")
        outlook.append(f"{c[Status.NOGO]} no-go", style="red")
        label = "Best daylight window" if act == "drone" else "Best window"
        outlook.append(f"\n{label}:\n", style="dim")
        outlook.append(_window_text(s), style="bold")
        name = "🛸 Drone" if act == "drone" else "🚤 Marine"
        if act == "marine" and not b.data.marine_available:
            name += "\n[dim](no waves)[/dim]"
        t.add_row(name, badge(s.now.status), outlook, _why(s, wind_units))
    return t


def current_panel(d: LocationData, wind_units: str) -> Panel:
    """Current conditions block."""
    h = d.current
    wu = U.wind_unit(wind_units)
    g = Table.grid(padding=(0, 2))
    g.add_column(style="dim", no_wrap=True)
    g.add_column(no_wrap=True)
    g.add_column(style="dim", no_wrap=True)
    g.add_column(no_wrap=True)
    g.add_row(
        "Weather", wx_text(h.weather_code),
        "Temp / feels", f"{U.num(h.temp_f, suffix='°F')} / {U.num(h.heat_index_f, suffix='°F')}",
    )
    g.add_row(
        "Wind 10 m",
        f"{U.compass(h.wind_dir)} {U.wind(h.wind_mph, wind_units)} G{U.wind(h.gust_mph, wind_units)} {wu}",
        "Wind aloft",
        f"80 m {U.wind(h.wind80_mph, wind_units)} · 120 m {U.wind(h.wind120_mph, wind_units)} {wu}",
    )
    g.add_row(
        "Rain chance", U.num(h.precip_prob_pct, suffix="%"),
        "Humidity", U.num(h.humidity_pct, suffix="%"),
    )
    g.add_row(
        "Visibility", U.num(h.visibility_mi, "{:.1f}", " mi"),
        "Clouds (low)", f"{U.num(h.cloud_cover_pct, suffix='%')} ({U.num(h.cloud_low_pct, suffix='%')})",
    )
    g.add_row("UV index", U.num(h.uv_index, "{:.1f}"), "CAPE", U.num(h.cape, suffix=" J/kg"))
    if d.marine_available:
        g.add_row(
            "Waves",
            f"{U.num(h.wave_ft, '{:.1f}', ' ft')} @ {U.num(h.wave_period_s, suffix='s')} "
            f"from {U.compass(h.wave_dir)}",
            "Swell / wind wave",
            f"{U.num(h.swell_ft, '{:.1f}', ' ft')} @ {U.num(h.swell_period_s, suffix='s')} "
            f"{U.compass(h.swell_dir)} / {U.num(h.wind_wave_ft, '{:.1f}', ' ft')}",
        )
    return Panel(g, title="Current conditions", title_align="left", border_style="cyan")


def tide_panel(d: LocationData) -> Panel | None:
    """Upcoming high/low tides, if available."""
    if not d.tide_station or not d.tides:
        return None
    t = Table.grid(padding=(0, 2))
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True)
    t.add_column(justify="right", no_wrap=True)
    for e in d.tides[:6]:
        style = "bold blue" if e.kind == "High" else "cyan"
        t.add_row(
            Text(e.kind, style=style),
            e.time.strftime("%a ") + e.time.strftime("%I:%M %p").lstrip("0"),
            f"{e.height_ft:.1f} ft",
        )
    st = d.tide_station
    dist = f", {st.distance_mi:.0f} mi" if st.distance_mi is not None else ""
    src = Text(f"{st.name} ({st.id}{dist})", style="dim", overflow="fold")
    return Panel(
        Group(t, src),
        title="Tides (MLLW)",
        title_align="left",
        border_style="blue",
        width=34,
    )


def _cell(text: str, col: str, levels: dict[str, Status]) -> Text:
    lvl = levels.get(col)
    return Text(text, style=_LEVEL_STYLE.get(lvl, "")) if lvl else Text(text)


def hourly_table(b: Briefing, wind_units: str, max_rows: int, width: int = 120) -> Table:
    """Hour-by-hour table; metric cells that trip a threshold are colored.

    Low-priority columns (Sky, UV) are dropped on narrow terminals.
    """
    wu = U.wind_unit(wind_units)
    show_sky = width >= 112
    show_uv = width >= 96
    roomy = width >= 92  # below this: drop Vis column and heat index
    show_waves = b.data.marine_available
    t = Table(
        box=box.SIMPLE_HEAVY,
        title=f"Hourly forecast (next {min(max_rows, len(b.data.hours))}h)",
        title_style="bold",
        caption=f"Wind/gust/aloft in {wu} (aloft = max of 80/120 m) · vis in mi · "
        "HI = heat index · waves ft/period.\nColored values tripped a Caution "
        "(yellow) / No-Go (red) limit. Dim times = night.",
        caption_style="dim",
        pad_edge=False,
    )
    headers = ["Time"] + (["Sky"] if show_sky else [])
    headers += ["°F/HI" if roomy else "°F", "Rain", "Wind/G", "Aloft"]
    headers += (["Vis"] if roomy else []) + ["CAPE"]
    headers += (["UV"] if show_uv else []) + (["Waves"] if show_waves else [])
    headers += ["Drone" if a == "drone" else "Marine" for a in b.activities]
    for h in headers:
        justify = "left" if h in ("Time", "Sky") else (
            "center" if h in ("Drone", "Marine") else "right")
        t.add_column(h, justify=justify, no_wrap=True,
                     min_width={"Time": 9, "Waves": 7, "Wind/G": 7}.get(h, len(h)))

    rows = b.data.hours[:max_rows]
    prev_day = None
    for i, h in enumerate(rows):
        levels: dict[str, Status] = {}
        for act in b.activities:
            for r in b.hourly[act][i].reasons:
                col = _HIGHLIGHT.get(r.label)
                if col and r.level > levels.get(col, Status.SAFE):
                    levels[col] = r.level
        wind_lvl = max(levels.get("wind", Status.SAFE), levels.get("gust", Status.SAFE))
        if wind_lvl:
            levels["wind"] = wind_lvl
        new_day = h.time.date() != prev_day
        prev_day = h.time.date()
        dim = "" if h.is_day else "dim"
        row: list[Text] = [
            Text(U.hour_label(h.time, with_day=new_day), style=("bold " if new_day else "") + dim)
        ]
        if show_sky:
            row.append(Text(wx_text(h.weather_code), style=dim))
        row += [
            _cell(
                f"{U.num(h.temp_f)}/{U.num(h.heat_index_f)}" if roomy else U.num(h.temp_f),
                "temp",
                levels,
            ),
            _cell(U.num(h.precip_prob_pct, suffix="%"), "rain", levels),
            _cell(
                f"{U.compass(h.wind_dir)} {U.wind(h.wind_mph, wind_units)}"
                f"/{U.wind(h.gust_mph, wind_units)}",
                "wind",
                levels,
            ),
            _cell(U.wind(h.wind_aloft_mph, wind_units), "aloft", levels),
        ]
        if roomy:
            row.append(_cell(U.num(h.visibility_mi, "{:.0f}"), "vis", levels))
        row.append(_cell(U.num(h.cape), "cape", levels))
        if show_uv:
            row.append(Text(U.num(h.uv_index)))
        if show_waves:
            row.append(
                _cell(f"{U.num(h.wave_ft, '{:.1f}')}' {U.num(h.wave_period_s)}s", "waves", levels)
            )
        for act in b.activities:
            row.append(badge(b.hourly[act][i].status, short=True))
        t.add_row(*row, end_section=i + 1 < len(rows) and rows[i + 1].time.hour == 0)
    return t


def render_briefing(
    console: Console, b: Briefing, wind_units: str, table_hours: int
) -> None:
    """Print the full briefing for one location."""
    loc = b.data.location
    stamp = b.data.generated.strftime("%a %b %d, %I:%M %p %Z").replace(" 0", " ")
    meta = Text(f"{loc.lat:.3f}, {loc.lon:.3f} · generated {stamp}", style="dim")
    parts: list = [meta, summary_table(b, wind_units)]
    side = [current_panel(b.data, wind_units)]
    tp = tide_panel(b.data)
    if tp:
        side.append(tp)
    if console.width >= 120:
        parts.append(Columns(side, equal=False, expand=False))
    else:
        parts.extend(side)
    parts.append(hourly_table(b, wind_units, table_hours, console.width))
    if b.data.notes:
        parts.append(Text("\n".join(f"⚠ {n}" for n in b.data.notes), style="yellow"))
    console.print(
        Panel(
            Group(*parts),
            title=Text.assemble(("flWeatherOps", "bold cyan"), " · ", (loc.name, "bold white")),
            title_align="left",
            border_style="cyan",
            box=box.HEAVY,
        )
    )


def render_multi_summary(
    console: Console,
    briefings: list[Briefing],
    failures: list[tuple[str, str]],
    wind_units: str,
) -> None:
    """One-line-per-location overview for ``--all``."""
    wu = U.wind_unit(wind_units)
    t = Table(box=box.ROUNDED, title="All locations — overview", title_style="bold cyan", expand=True)
    t.add_column("Location", style="bold")
    t.add_column("Drone now", justify="center")
    t.add_column("Drone best window")
    t.add_column("Marine now", justify="center")
    t.add_column("Marine best window")
    t.add_column(f"Wind/Gust {wu}", justify="right")
    t.add_column("Waves", justify="right")
    t.add_column("Rain", justify="right")
    for b in briefings:
        h: HourData = b.data.current
        row: list = [b.data.location.name]
        for act in ("drone", "marine"):
            if act in b.summary:
                row += [badge(b.summary[act].now.status), _window_text(b.summary[act])]
            else:
                row += ["—", "—"]
        row += [
            f"{U.compass(h.wind_dir)} {U.wind(h.wind_mph, wind_units)}/{U.wind(h.gust_mph, wind_units)}",
            U.num(h.wave_ft, "{:.1f}", " ft") if b.data.marine_available else "n/a",
            U.num(h.precip_prob_pct, suffix="%"),
        ]
        t.add_row(*row)
    for name, err in failures:
        t.add_row(name, Text("ERROR", style="bold red"), Text(err, style="red"), "", "", "", "", "")
    console.print(t)


def render_error(console: Console, name: str, err: str) -> None:
    """Print a location that could not be fetched."""
    console.print(
        Panel(
            Text(f"Could not get a forecast: {err}\nNo usable cached data either.", style="red"),
            title=f"flWeatherOps · {name}",
            border_style="red",
        )
    )


def render_footer(console: Console) -> None:
    """Attribution / disclaimer line."""
    console.print(Text(ATTRIBUTION, style="dim italic"))
