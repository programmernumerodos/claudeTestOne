"""Rich terminal rendering for briefings (CLI only; the dashboard has its own UI)."""

from __future__ import annotations

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from flweatherops import units as U
from flweatherops.advice import Advice
from flweatherops.astro import AstroHour, best_astro_block
from flweatherops.data import HourData, LocationData
from flweatherops.history import HistoryRow
from flweatherops.safety import Status, WindowSummary
from flweatherops.service import Briefing
from flweatherops.tropical import LocationTropical, TropicalReport
from flweatherops.windows import rank_windows

ATTRIBUTION = (
    "Weather data: Open-Meteo.com (CC BY 4.0; free tier is for non-commercial use). "
    "Tides: NOAA CO-OPS. Tropical: NOAA NHC. Model guidance only — always check official NWS forecasts, "
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
_ADVICE_STYLE = {"info": ("•", "cyan"), "warn": ("▲", "yellow"), "alert": ("■", "bold red")}


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
    n = b.horizon
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
    g.add_column()
    g.add_column(style="dim", no_wrap=True)
    g.add_column()
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
        title=f"Hourly forecast (next {min(max_rows, b.horizon)}h)",
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

    rows = b.data.hours[: min(max_rows, b.horizon)]
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
    parts: list = [meta]
    if b.tropical and (b.tropical.relevant or b.tropical.florida_alerts):
        parts.append(tropical_banner(b.tropical))
    parts.append(summary_table(b, wind_units))
    side = [current_panel(b.data, wind_units)]
    tp = tide_panel(b.data)
    if tp:
        side.append(tp)
    if console.width >= 120:
        parts.append(Columns(side, equal=False, expand=False))
    else:
        parts.extend(side)
    parts.append(advice_panel(b))
    parts.append(windows_panel(b, wind_units, limit=3))
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


# --------------------------------------------------------------------------- #
# New sections: advice, safe windows, tropical, astro, history
# --------------------------------------------------------------------------- #


def _advice_lines(items: list[Advice]) -> Text:
    out = Text()
    for i, a in enumerate(items):
        icon, style = _ADVICE_STYLE.get(a.level, ("•", ""))
        if i:
            out.append("\n")
        out.append(f"{icon} {a.topic}: ", style=style)
        out.append(a.text)
    return out


def advice_panel(b: Briefing) -> Panel:
    """Practical notes per activity."""
    groups = []
    for act in b.activities:
        items = b.advice.get(act) or []
        title = "🛸 Drone notes" if act == "drone" else "🚤 Marine notes"
        groups.append(Text(title, style="bold"))
        groups.append(_advice_lines(items) if items else Text("No special notes.", style="dim"))
    return Panel(Group(*groups), title="Operational notes", title_align="left", border_style="magenta")


def windows_text(b: Briefing, act: str, wind_units: str, limit: int | None = None) -> Text:
    """'Drone Safe windows: 14:00–17:00 today, ...' plus the best pick."""
    wins = b.windows.get(act, [])
    label = "Drone" if act == "drone" else "Marine"
    scan = len(b.data.hours)
    out = Text()
    if not wins:
        out.append(f"{label}: ", style="bold")
        out.append(f"no Safe window ≥ the minimum length in the next {scan}h.", style="yellow")
        return out
    best = rank_windows(wins)[0]
    shown = wins if limit is None else wins[:limit]
    out.append(f"{label} Safe windows: ", style="bold")
    out.append(", ".join(w.label(b.today) for w in shown), style="green")
    if limit is not None and len(wins) > limit:
        out.append(f" (+{len(wins) - limit} more)", style="dim")
    detail = f"max gust {U.wind(best.max_gust_mph, wind_units)} {U.wind_unit(wind_units)}"
    if act == "marine" and best.max_wave_ft is not None:
        detail += f", waves ≤ {best.max_wave_ft:.1f} ft"
    out.append(f"\n  Best {label.lower()} window: ", style="bold")
    out.append(f"{best.label(b.today)} ({best.duration_h}h; {detail})")
    return out


def windows_panel(b: Briefing, wind_units: str, limit: int | None = None) -> Panel:
    """Safe Window Finder results for each activity."""
    scan = len(b.data.hours)
    body = Group(*[windows_text(b, a, wind_units, limit) for a in b.activities])
    note = "daylight only for drones" if b.activities and "drone" in b.activities else ""
    return Panel(
        body,
        title=f"Safe Window Finder (next {scan}h{', ' + note if note else ''})",
        title_align="left",
        border_style="green",
    )


def tropical_banner(trop: LocationTropical) -> Panel:
    """Prominent banner when a storm is relevant to this location."""
    level = trop.level
    lines = Text()
    for i, imp in enumerate(trop.relevant):
        if i:
            lines.append("\n")
        lines.append(f"{imp.level.label}: ", style=imp.level.style)
        lines.append(imp.summary)
    if trop.florida_alerts and not trop.relevant:
        kinds = sorted({f"{a.kind} ({s.name})" for s, a in trop.florida_alerts})
        lines.append("NHC watches/warnings for parts of Florida: " + ", ".join(kinds), style="yellow")
    style = {Status.NOGO: "red", Status.CAUTION: "yellow"}.get(level, "yellow")
    return Panel(lines, title="🌀 Tropical alert", title_align="left", border_style=style)


def render_tropical(console: Console, report: TropicalReport | None, assessments: list[tuple[str, LocationTropical]]) -> None:
    """Stand-alone tropical report (``--tropical``)."""
    if report is None:
        console.print("[dim]Tropical monitoring is disabled in config.[/dim]")
        return
    if not report.ok:
        console.print(Panel(Text(report.error or "NHC unavailable", style="yellow"),
                            title="🌀 Tropical (NHC)", border_style="yellow"))
        return
    if report.cached_age is not None:
        console.print(f"[yellow]NHC: live request failed — using cached data "
                      f"({int(report.cached_age.total_seconds() // 60)} min old).[/yellow]")
    if not report.storms:
        console.print(Panel(Text("No active Atlantic tropical cyclones.", style="green"),
                            title="🌀 Tropical (NHC)", border_style="green"))
        return
    t = Table(box=box.ROUNDED, title="🌀 Active Atlantic systems (NHC)", title_style="bold", expand=True)
    t.add_column("System", style="bold")
    t.add_column("Intensity")
    t.add_column("Position / motion")
    t.add_column("Track")
    for s in report.storms:
        motion = (f"toward {U.compass(s.movement_dir)} at {s.movement_speed_mph:.0f} mph"
                  if s.movement_dir is not None and s.movement_speed_mph is not None else "motion n/a")
        pos = f"{abs(s.lat):.1f}°{'N' if s.lat >= 0 else 'S'} {abs(s.lon):.1f}°{'W' if s.lon < 0 else 'E'}"
        t.add_row(s.title, s.intensity_text, f"{pos}, {motion}", s.track_source)
    console.print(t)
    for name, lt in assessments:
        lines = Text()
        if not lt.impacts:
            lines.append("No systems within range.", style="green")
        for i, imp in enumerate(lt.impacts):
            if i:
                lines.append("\n")
            lines.append(f"{imp.level.label} ", style=imp.level.style)
            lines.append(imp.summary)
        console.print(Panel(lines, title=f"Impact · {name}", title_align="left",
                            border_style={Status.NOGO: "red", Status.CAUTION: "yellow"}.get(lt.level, "green")))
    for s in report.storms:
        if s.alerts_text:
            console.print(Panel(Text(s.alerts_text.strip()), title=f"Watches & warnings · {s.title}",
                                title_align="left", border_style="yellow"))
        for n in s.notes:
            console.print(f"[yellow]⚠ {s.title}: {n}[/yellow]")


def render_astro(console: Console, name: str, rated: list[AstroHour], max_rows: int = 14) -> None:
    """Night-sky table for astrophotography."""
    if not rated:
        console.print(f"[dim]{name}: no night hours in the forecast window.[/dim]")
        return
    first = rated[0].moon
    t = Table(
        box=box.SIMPLE_HEAVY,
        title=f"🌌 Astro conditions · {name}",
        caption=f"Moon: {first.phase_name}, {first.illumination:.0%} illuminated. "
        "Score: 100 = clear, transparent, moonless.",
        caption_style="dim",
    )
    for col, j in (("Time", "left"), ("Score", "right"), ("Rating", "left"), ("Cloud", "right"),
                   ("Low/Mid/High", "right"), ("Moon", "right"), ("Notes", "left")):
        t.add_column(col, justify=j, no_wrap=col != "Notes")
    style = {"Excellent": "bold green", "Good": "green", "Fair": "yellow", "Poor": "red"}
    for a in rated[:max_rows]:
        t.add_row(
            U.hour_label(a.time, with_day=True),
            str(a.score),
            Text(a.rating, style=style[a.rating]),
            U.num(a.cloud_pct, suffix="%"),
            f"{U.num(a.hour.cloud_low_pct)}/{U.num(a.hour.cloud_mid_pct)}/{U.num(a.hour.cloud_high_pct)}",
            f"{a.moon.altitude_deg:+.0f}°",
            ", ".join(a.notes),
        )
    console.print(t)
    best = best_astro_block(rated)
    if best:
        console.print(f"[bold]Best astro block:[/bold] {U.hour_label(best[0].time, with_day=True)} – "
                      f"{U.hour_label(best[-1].time, with_day=True)} (avg score "
                      f"{sum(a.score for a in best) // len(best)})")
    else:
        console.print("[yellow]No Good-or-better astro hours in the window.[/yellow]")


def render_history(console: Console, rows: list[HistoryRow], wind_units: str) -> None:
    """Recent logged briefings."""
    if not rows:
        console.print("[dim]No briefing history yet — it is recorded each time you run a briefing.[/dim]")
        return
    wu = U.wind_unit(wind_units)
    t = Table(box=box.SIMPLE_HEAVY, title="Briefing history (newest first)", title_style="bold",
              caption=f"Wind/gust in {wu}", caption_style="dim", pad_edge=False)
    t.add_column("When", no_wrap=True)
    t.add_column("Location", min_width=12, max_width=24)
    wide = console.width >= 90  # drop Aloft and °F on narrow terminals
    cols = ["Drone", "Marine", "Trop", "Wind/G"] + (["Aloft"] if wide else []) + ["Waves", "Rain"]
    for col in cols + (["°F"] if wide else []):
        t.add_column(col, no_wrap=True, justify="left" if col in ("Drone", "Marine", "Trop") else "right")
    short = {"SAFE": "SAFE", "CAUTION": "CAUT", "NO-GO": "NOGO"}
    styles = {s.label: s.style for s in Status}

    def st(v: str | None) -> Text:
        return Text(short.get(v or "", "—"), style=styles.get(v or "", ""))

    for r in rows:
        cells: list = [
            r.logged_at.strftime("%m/%d %H:%M"),
            r.location_name,
            st(r.drone_status), st(r.marine_status), st(r.tropical_status),
            f"{U.wind(r.wind_mph, wind_units)}/{U.wind(r.gust_mph, wind_units)}",
        ]
        if wide:
            cells.append(U.wind(r.wind_aloft_mph, wind_units))
        cells += [U.num(r.wave_ft, "{:.1f}"), U.num(r.precip_prob_pct, suffix="%")]
        if wide:
            cells.append(U.num(r.temp_f))
        t.add_row(*cells)
    console.print(t)
