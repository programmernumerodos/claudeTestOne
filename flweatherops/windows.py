"""Safe Window Finder: continuous runs of SAFE hours in an evaluated forecast.

Used by both the CLI (``--windows``) and the dashboard. Windows are ranked by
length, then by how far below the limits the conditions sit (lower peak gust).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from flweatherops.data import HourData
from flweatherops.safety import Status, Verdict


@dataclass
class SafeWindow:
    """A continuous block of SAFE hours. ``end`` is exclusive (last hour + 1 h)."""

    start: datetime
    end: datetime
    hours: list[HourData]

    @property
    def duration_h(self) -> int:
        return len(self.hours)

    @property
    def max_gust_mph(self) -> float | None:
        vals = [h.gust_mph for h in self.hours if h.gust_mph is not None]
        return max(vals) if vals else None

    @property
    def max_wind_mph(self) -> float | None:
        vals = [h.wind_mph for h in self.hours if h.wind_mph is not None]
        return max(vals) if vals else None

    @property
    def max_wave_ft(self) -> float | None:
        vals = [h.wave_ft for h in self.hours if h.wave_ft is not None]
        return max(vals) if vals else None

    def label(self, today: date | None = None) -> str:
        """'14:00–17:00 today', '09:00–12:00 tomorrow', ... (end is exclusive)."""
        return format_span(self.start, self.end, today)


def day_word(d: date, today: date | None = None) -> str:
    """'today', 'tomorrow' or a weekday name."""
    today = today or datetime.now().date()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return f"{d:%a %b} {d.day}"


def format_span(start: datetime, end: datetime, today: date | None = None) -> str:
    """Human-readable 24 h span; crosses-midnight spans name both days."""
    s, e = start.strftime("%H:%M"), end.strftime("%H:%M")
    d0 = day_word(start.date(), today)
    last_hour = end - timedelta(hours=1)
    if last_hour.date() != start.date():
        return f"{d0} {s} – {day_word(end.date(), today)} {e}"
    return f"{s}–{e} {d0}"


def find_windows(
    hours: list[HourData],
    verdicts: list[Verdict],
    *,
    min_hours: int = 1,
    daylight_only: bool = False,
) -> list[SafeWindow]:
    """All runs of consecutive SAFE hours of at least ``min_hours``, in time order.

    A gap in the hourly series (missing hour) breaks a run.
    """
    windows: list[SafeWindow] = []
    run: list[HourData] = []

    def flush() -> None:
        if len(run) >= max(min_hours, 1):
            windows.append(SafeWindow(run[0].time, run[-1].time + timedelta(hours=1), list(run)))
        run.clear()

    for h, v in zip(hours, verdicts):
        ok = v.status == Status.SAFE and (h.is_day or not daylight_only)
        contiguous = not run or h.time - run[-1].time == timedelta(hours=1)
        if ok and contiguous:
            run.append(h)
        else:
            flush()
            if ok:
                run.append(h)
    flush()
    return windows


def rank_windows(windows: list[SafeWindow]) -> list[SafeWindow]:
    """Best first: longest, then lowest peak gust, then earliest."""
    return sorted(
        windows,
        key=lambda w: (-w.duration_h, w.max_gust_mph if w.max_gust_mph is not None else 99, w.start),
    )
