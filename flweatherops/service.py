"""Shared orchestration: fetch -> evaluate -> windows/advice/tropical -> Briefing.

The CLI (``cli.py``) and the Streamlit dashboard (``dashboard.py``) both call
:func:`build_briefing`, so they always agree on verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from flweatherops import history
from flweatherops.advice import Advice, drone_advice, marine_advice
from flweatherops.config import Location
from flweatherops.data import LocationData, load_location
from flweatherops.safety import Verdict, WindowSummary, evaluate, summarize, with_extra
from flweatherops.tropical import LocationTropical, TropicalReport, assess_location, fetch_tropical
from flweatherops.windows import SafeWindow, find_windows

ACTIVITIES = ("drone", "marine")


@dataclass
class Briefing:
    """Everything needed to present one location."""

    data: LocationData
    activities: list[str]
    hourly: dict[str, list[Verdict]]  # one verdict per hour in data.hours (full scan)
    now: dict[str, Verdict]
    summary: dict[str, WindowSummary]  # over the first ``horizon`` hours
    horizon: int
    windows: dict[str, list[SafeWindow]]  # over the full scan
    advice: dict[str, list[Advice]]  # tropical is shown separately (banner/tab)
    tropical: LocationTropical | None = None

    @property
    def today(self) -> date:
        """Local date at the location (for 'today'/'tomorrow' labels)."""
        return self.data.generated.date()


def get_tropical(cfg: dict[str, Any], *, use_cache: bool = True) -> TropicalReport | None:
    """Fetch the NHC report once per run (None when disabled in config)."""
    if not cfg.get("tropical", {}).get("enabled", True):
        return None
    try:
        return fetch_tropical(use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001 - optional subsystem must never break a briefing
        return TropicalReport(ok=False, error=f"tropical check failed ({exc.__class__.__name__}: {exc})")


def build_briefing(
    loc: Location,
    cfg: dict[str, Any],
    *,
    hours: int = 24,
    activities: Iterable[str] = ACTIVITIES,
    use_cache: bool = True,
    with_tides: bool = True,
    tropical: TropicalReport | None = None,
    min_window_hours: int | None = None,
    units: str = "mph",
) -> Briefing:
    """Fetch data for ``loc`` and evaluate all rules.

    Args:
        hours: Horizon for the summary/hourly table.
        tropical: A pre-fetched NHC report (shared across locations), or None.
        min_window_hours: Minimum Safe-window length (default from config).

    Raises:
        flweatherops.data.FetchError: The core forecast is unavailable.
    """
    acts = [a for a in activities if a in ACTIVITIES]
    wcfg = cfg.get("windows", {})
    scan = max(hours, int(wcfg.get("scan_hours", 72)))
    min_h = int(min_window_hours or wcfg.get("min_hours", 2))

    data = load_location(
        loc,
        cfg,
        scan,
        use_cache=use_cache,
        with_marine="marine" in acts,
        with_tides="marine" in acts and with_tides,
    )
    trop = assess_location(tropical, loc, data.hours, cfg) if tropical else None
    extra = trop.hour_reasons if trop else [[] for _ in data.hours]

    thresholds = cfg["thresholds"]
    hourly = {
        a: [with_extra(evaluate(h, a, thresholds), extra[i]) for i, h in enumerate(data.hours)]
        for a in acts
    }
    now = {a: with_extra(evaluate(data.current, a, thresholds), extra[0]) for a in acts}
    horizon = min(hours, len(data.hours))
    summary = {
        a: summarize(
            data.hours[:horizon], hourly[a][:horizon],
            daylight_only=(a == "drone"), current=now[a],
        )
        for a in acts
    }
    windows = {
        a: find_windows(
            data.hours, hourly[a], min_hours=min_h,
            daylight_only=(a == "drone" and bool(wcfg.get("drone_daylight_only", True))),
        )
        for a in acts
    }
    advice = {
        a: drone_advice(data, cfg, units) if a == "drone" else marine_advice(data, cfg, units)
        for a in acts
    }
    return Briefing(data, acts, hourly, now, summary, horizon, windows, advice, trop)


def briefing_record(b: Briefing) -> dict[str, Any]:
    """Flatten a briefing into a history-log record."""
    h = b.data.current
    loc = b.data.location

    def st(a: str) -> str | None:
        return b.now[a].status.label if a in b.now else None

    return {
        "logged_at": b.data.generated.replace(tzinfo=None).isoformat(timespec="seconds"),
        "location_key": loc.key,
        "location_name": loc.name,
        "lat": loc.lat,
        "lon": loc.lon,
        "drone_status": st("drone"),
        "marine_status": st("marine"),
        "tropical_status": b.tropical.level.label if b.tropical else None,
        "temp_f": h.temp_f,
        "heat_index_f": h.heat_index_f,
        "wind_mph": h.wind_mph,
        "gust_mph": h.gust_mph,
        "wind_aloft_mph": h.wind_aloft_mph,
        "wind_dir": h.wind_dir,
        "precip_prob_pct": h.precip_prob_pct,
        "visibility_mi": h.visibility_mi,
        "cape": h.cape,
        "wave_ft": h.wave_ft if b.data.marine_available else None,
        "wave_period_s": h.wave_period_s if b.data.marine_available else None,
        "reasons": {a: [r.text() for r in v.reasons] for a, v in b.now.items()},
    }


def log_history(b: Briefing, cfg: dict[str, Any]) -> bool:
    """Log a briefing if history is enabled (rate-limited per location)."""
    hcfg = cfg.get("history", {})
    if not hcfg.get("enabled", True):
        return False
    return history.log_briefing(
        briefing_record(b), min_interval_min=float(hcfg.get("min_interval_min", 30))
    )
