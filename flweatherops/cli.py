"""Command-line interface for flWeatherOps (briefings, safe windows, tropical, astro, history)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console

from flweatherops import __version__
from flweatherops.config import (
    DEFAULT_CONFIG,
    ConfigError,
    Location,
    load_config,
    locations_from_config,
)
from rich.panel import Panel

from flweatherops import history
from flweatherops.astro import astro_hours
from flweatherops.data import FetchError, geocode
from flweatherops.render import (
    render_astro,
    render_briefing,
    render_error,
    render_footer,
    render_history,
    render_multi_summary,
    render_tropical,
    tropical_banner,
    windows_panel,
)
from flweatherops.service import Briefing, build_briefing, get_tropical, log_history

MAX_HOURS = 72


def build_parser() -> argparse.ArgumentParser:
    """Argument parser."""
    p = argparse.ArgumentParser(
        prog="flweatherops",
        description="Drone + marine weather briefings for South/Central Florida "
        "(Open-Meteo + NOAA tides).",
        epilog="Examples:\n"
        "  flweatherops                       # default location(s)\n"
        "  flweatherops okeechobee --units kn\n"
        "  flweatherops --all --summary\n"
        '  flweatherops --place "Stuart, FL"\n'
        "  flweatherops --lat 27.20 --lon -80.17 --name 'St. Lucie Inlet'\n"
        "  flweatherops --windows --min-window 3   # Safe Window Finder (72 h)\n"
        "  flweatherops --tropical                 # NHC tropical check\n"
        "  flweatherops vero --astro               # night-sky conditions\n"
        "  flweatherops --history 30               # last 30 logged briefings\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("locations", nargs="*", metavar="LOCATION",
                   help="Configured location key(s) or name(s), e.g. vero-beach okeechobee")
    p.add_argument("-a", "--all", action="store_true", help="Brief every configured location")
    p.add_argument("--place", help='Look up a place by name, e.g. "Jupiter, FL"')
    p.add_argument("--lat", type=float, help="Custom latitude (use with --lon)")
    p.add_argument("--lon", type=float, help="Custom longitude (use with --lat)")
    p.add_argument("--name", help="Label for a --lat/--lon location")
    p.add_argument("--inland", action="store_true",
                   help="Treat a custom location as inland (skip waves/tides)")
    p.add_argument("-H", "--hours", type=int, default=24,
                   help=f"Hours for the summary/hourly table (1-{MAX_HOURS}, default 24)")
    p.add_argument("-u", "--units", choices=("mph", "kn"), default="mph",
                   help="Wind display units (thresholds are always configured in mph)")
    p.add_argument("--activity", choices=("both", "drone", "marine"), default="both",
                   help="Which activity to brief")
    p.add_argument("-s", "--summary", action="store_true",
                   help="Only print the overview table (implied detail is skipped)")
    focus = p.add_argument_group("focused views (combine freely; replaces the full briefing)")
    focus.add_argument("-w", "--windows", action="store_true",
                       help="Safe Window Finder: all Safe windows over windows.scan_hours (72 h)")
    focus.add_argument("--min-window", type=int, metavar="H",
                       help="Minimum Safe-window length in hours (default: config, 2)")
    focus.add_argument("-t", "--tropical", action="store_true",
                       help="NHC tropical report: active storms, distance, watches/warnings")
    focus.add_argument("--astro", action="store_true",
                       help="Astrophotography: clouds, transparency, moon phase/altitude")
    focus.add_argument("--history", nargs="?", type=int, const=20, metavar="N",
                       help="Show the last N logged briefings (default 20) and exit")
    p.add_argument("--no-tropical", action="store_true", help="Skip the NHC tropical check")
    p.add_argument("--no-log", action="store_true", help="Don't record this run in the history log")
    p.add_argument("-c", "--config", type=Path, help="Path to config.json")
    p.add_argument("--no-cache", action="store_true", help="Do not read or write the cache")
    p.add_argument("--no-tides", action="store_true", help="Skip NOAA tide lookup")
    p.add_argument("--list", action="store_true", help="List configured locations and exit")
    p.add_argument("--dump-config", action="store_true",
                   help="Print the default config as JSON (redirect to config.json to customize)")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _resolve(
    args: argparse.Namespace, known: dict[str, Location]
) -> tuple[list[Location], list[tuple[str, str]]]:
    """Turn CLI arguments into a list of locations (+ resolution errors)."""
    chosen: list[Location] = []
    errors: list[tuple[str, str]] = []

    if args.lat is not None:
        chosen.append(
            Location(
                key="custom",
                name=args.name or f"{args.lat:.3f}, {args.lon:.3f}",
                lat=args.lat,
                lon=args.lon,
                coastal=not args.inland,
            )
        )
    if args.place:
        try:
            loc = geocode(args.place)
            loc.coastal = not args.inland
            chosen.append(loc)
        except FetchError as exc:
            errors.append((args.place, f"lookup failed: {exc}"))

    for name in args.locations:
        norm = name.lower().strip().replace(" ", "-")
        match = known.get(norm) or next(
            (l for k, l in known.items()
             if k.startswith(norm) or l.name.lower().startswith(name.lower())),
            None,
        )
        if match:
            chosen.append(match)
        else:
            try:
                loc = geocode(name)
                loc.coastal = not args.inland
                chosen.append(loc)
            except FetchError as exc:
                errors.append((name, f"unknown location ({exc})"))

    if args.all:
        chosen.extend(l for l in known.values() if l not in chosen)
    if not chosen and not errors:
        chosen = [l for l in known.values() if l.default] or list(known.values())[:1]
    return chosen, errors


def _brief(loc: Location, cfg: dict, args: argparse.Namespace, tropical) -> Briefing:  # type: ignore[no-untyped-def]
    """Fetch data and evaluate rules for one location."""
    activities = ["drone", "marine"] if args.activity == "both" else [args.activity]
    return build_briefing(
        loc,
        cfg,
        hours=args.hours,
        activities=activities,
        use_cache=not args.no_cache,
        with_tides=not args.no_tides,
        tropical=tropical,
        min_window_hours=args.min_window,
        units=args.units,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.lat is None) != (args.lon is None):
        parser.error("--lat and --lon must be used together")
    if args.lat is not None and not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        parser.error("--lat/--lon out of range")
    console = Console()

    if args.dump_config:
        print(json.dumps(DEFAULT_CONFIG, indent=2))
        return 0
    if not 1 <= args.hours <= MAX_HOURS:
        console.print(f"[red]--hours must be between 1 and {MAX_HOURS}[/red]")
        return 2
    if args.min_window is not None and not 1 <= args.min_window <= MAX_HOURS:
        console.print(f"[red]--min-window must be between 1 and {MAX_HOURS}[/red]")
        return 2

    try:
        cfg = load_config(args.config)
        known = locations_from_config(cfg)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        return 2

    if args.list:
        for key, l in known.items():
            flag = " (default)" if l.default else ""
            kind = "coastal" if l.coastal else "inland"
            console.print(f"[bold]{key}[/bold]{flag}: {l.name}  {l.lat:.4f}, {l.lon:.4f}  [dim]{kind}[/dim]")
        return 0

    if args.history is not None:
        key = None
        if len(args.locations) == 1:
            norm = args.locations[0].lower().replace(" ", "-")
            key = next((k for k in known if k.startswith(norm)), None)
        render_history(console, history.recent(args.history, key), args.units)
        return 0

    locations, failures = _resolve(args, known)
    briefings: list[Briefing] = []
    multi = len(locations) + len(failures) > 1 or args.summary

    with console.status("Fetching forecasts…", spinner="dots"):
        tropical = None if args.no_tropical else get_tropical(cfg, use_cache=not args.no_cache)
        for loc in locations:
            try:
                b = _brief(loc, cfg, args, tropical)
                briefings.append(b)
                if not args.no_log:
                    log_history(b, cfg)
            except FetchError as exc:
                failures.append((loc.name, str(exc)))

    if args.windows or args.tropical or args.astro:
        _render_focused(console, args, cfg, briefings, failures, tropical)
        render_footer(console)
        return 0 if briefings or (args.tropical and tropical and tropical.ok) else 1

    if tropical is not None and not tropical.ok:
        console.print(f"[yellow]⚠ Tropical check skipped: {tropical.error}[/yellow]")
    elif tropical is not None and tropical.cached_age is not None:
        mins = int(tropical.cached_age.total_seconds() // 60)
        console.print(f"[yellow]⚠ NHC request failed — tropical data is from cache ({mins} min old).[/yellow]")
    if multi:
        render_multi_summary(console, briefings, failures, args.units)
    if not args.summary:
        for b in briefings:
            render_briefing(console, b, args.units, args.hours)
        if not multi:
            for name, err in failures:
                render_error(console, name, err)
    render_footer(console)
    return 0 if briefings else 1


def _render_focused(console: Console, args: argparse.Namespace, cfg: dict,  # type: ignore[type-arg]
                    briefings: list[Briefing], failures: list[tuple[str, str]], tropical) -> None:  # type: ignore[no-untyped-def]
    """Print only the sections requested by --windows / --tropical / --astro."""
    if args.tropical:
        render_tropical(
            console, tropical,
            [(b.data.location.name, b.tropical) for b in briefings if b.tropical],
        )
    for b in briefings:
        name = b.data.location.name
        if args.windows:
            if b.tropical and b.tropical.relevant:
                console.print(tropical_banner(b.tropical))
            panel = windows_panel(b, args.units)
            panel.title = f"{name} · {panel.title}"
            console.print(panel)
        if args.astro:
            tz = cfg.get("timezone", "America/New_York")
            loc = b.data.location
            render_astro(console, name, astro_hours(b.data.hours[:36], loc.lat, loc.lon, tz))
    for name, err in failures:
        console.print(Panel(f"[red]{err}[/red]", title=name, border_style="red"))


if __name__ == "__main__":
    sys.exit(main())
