"""Transparent Safe / Caution / No-Go rules for drone and marine activities.

Every verdict carries the list of :class:`Reason` objects that produced it, so
the briefing can show *why* an hour is Caution or No-Go.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

from flweatherops import units as U
from flweatherops.data import HourData

THUNDER_CODES = {95, 96, 99}


class Status(IntEnum):
    """Ordered so that ``max()`` gives the worst status."""

    SAFE = 0
    CAUTION = 1
    NOGO = 2

    @property
    def label(self) -> str:
        return {0: "SAFE", 1: "CAUTION", 2: "NO-GO"}[int(self)]

    @property
    def style(self) -> str:
        return {0: "bold green", 1: "bold yellow", 2: "bold red"}[int(self)]


@dataclass(frozen=True)
class Rule:
    """A single numeric threshold check."""

    key: str  # threshold key in config
    label: str
    getter: Callable[[HourData], float | None]
    kind: str  # "wind" | "pct" | "in" | "mi" | "ft" | "f"
    lower_is_worse: bool = False


RULES: dict[str, list[Rule]] = {
    "drone": [
        Rule("wind_speed_mph", "Wind", lambda h: h.wind_mph, "wind"),
        Rule("wind_gust_mph", "Gusts", lambda h: h.gust_mph, "wind"),
        Rule("wind_aloft_mph", "Wind aloft (80–120 m)", lambda h: h.wind_aloft_mph, "wind"),
        Rule("precip_prob_pct", "Rain chance", lambda h: h.precip_prob_pct, "pct"),
        Rule("precip_in", "Rain amount", lambda h: h.precip_in, "in"),
        Rule("visibility_mi", "Visibility", lambda h: h.visibility_mi, "mi", lower_is_worse=True),
        Rule("heat_index_f", "Heat index", lambda h: h.heat_index_f, "f"),
    ],
    "marine": [
        Rule("wind_speed_mph", "Wind", lambda h: h.wind_mph, "wind"),
        Rule("wind_gust_mph", "Gusts", lambda h: h.gust_mph, "wind"),
        Rule("wave_height_ft", "Waves", lambda h: h.wave_ft, "ft"),
        Rule("precip_prob_pct", "Rain chance", lambda h: h.precip_prob_pct, "pct"),
        Rule("precip_in", "Rain amount", lambda h: h.precip_in, "in"),
        Rule("visibility_mi", "Visibility", lambda h: h.visibility_mi, "mi", lower_is_worse=True),
        Rule("heat_index_f", "Heat index", lambda h: h.heat_index_f, "f"),
    ],
}


def _fmt(kind: str, value: float, wind_units: str) -> str:
    if kind == "wind":
        return f"{U.wind(value, wind_units)} {U.wind_unit(wind_units)}"
    return {
        "pct": f"{value:.0f}%",
        "in": f"{value:.2f} in/h",
        "mi": f"{value:.1f} mi",
        "ft": f"{value:.1f} ft",
        "f": f"{value:.0f}°F",
    }.get(kind, f"{value:g}")


@dataclass(frozen=True)
class Reason:
    """Why an hour got its status."""

    label: str
    level: Status
    value: float | None = None
    limit: float | None = None
    kind: str = ""
    lower_is_worse: bool = False
    note: str = ""

    def text(self, wind_units: str = "mph") -> str:
        """Human-readable explanation, e.g. 'Gusts 24 mph ≥ 18'."""
        if self.note:
            return self.note
        if self.value is None or self.limit is None:
            return self.label
        op = "<" if self.lower_is_worse else "≥"
        return (
            f"{self.label} {_fmt(self.kind, self.value, wind_units)} "
            f"{op} {_fmt(self.kind, self.limit, wind_units)}"
        )


@dataclass
class Verdict:
    """Status for one hour + reasons (worst first)."""

    status: Status
    reasons: list[Reason] = field(default_factory=list)


def _check(rule: Rule, h: HourData, limits: dict[str, Any]) -> Reason | None:
    value = rule.getter(h)
    if value is None or not isinstance(limits, dict):
        return None
    for level, key in ((Status.NOGO, "nogo"), (Status.CAUTION, "caution")):
        limit = limits.get(key)
        if limit is None:
            continue
        hit = value < limit if rule.lower_is_worse else value >= limit
        if hit:
            return Reason(rule.label, level, value, float(limit), rule.kind, rule.lower_is_worse)
    return None


def lightning_reason(h: HourData, cfg: dict[str, Any]) -> Reason | None:
    """Thunderstorm code, or high CAPE combined with meaningful rain chance."""
    if h.weather_code in THUNDER_CODES:
        return Reason("Lightning", Status.NOGO, note="Thunderstorm forecast")
    if h.cape is None:
        return None
    pp = h.precip_prob_pct or 0
    if pp < float(cfg.get("precip_prob_min", 30)):
        return None
    nogo, caution = cfg.get("cape_nogo"), cfg.get("cape_caution")
    if nogo is not None and h.cape >= nogo:
        return Reason(
            "Lightning", Status.NOGO,
            note=f"Lightning risk: CAPE {h.cape:.0f} J/kg + {pp:.0f}% rain",
        )
    if caution is not None and h.cape >= caution:
        return Reason(
            "Lightning", Status.CAUTION,
            note=f"Storm potential: CAPE {h.cape:.0f} J/kg + {pp:.0f}% rain",
        )
    return None


def evaluate(h: HourData, activity: str, thresholds: dict[str, Any]) -> Verdict:
    """Evaluate one hour for ``activity`` ('drone' or 'marine')."""
    limits = thresholds.get(activity, {})
    reasons: list[Reason] = []
    if h.wind_mph is None:
        reasons.append(Reason("Wind", Status.CAUTION, note="Wind data missing"))
    for rule in RULES[activity]:
        r = _check(rule, h, limits.get(rule.key, {}))
        if r:
            reasons.append(r)
    lr = lightning_reason(h, thresholds.get("lightning", {}))
    if lr:
        reasons.append(lr)
    reasons.sort(key=lambda r: r.level, reverse=True)
    status = max((r.level for r in reasons), default=Status.SAFE)
    return Verdict(status, reasons)


@dataclass
class WindowSummary:
    """Roll-up of a forecast window for one activity."""

    now: Verdict
    worst: Status
    counts: dict[Status, int]
    best_window: tuple[HourData, HourData] | None
    change: tuple[HourData, Status] | None  # first hour the status changes from now
    top_factors: list[str]


def summarize(
    hours: list[HourData],
    verdicts: list[Verdict],
    *,
    daylight_only: bool = False,
    current: Verdict | None = None,
) -> WindowSummary:
    """Summarize hourly verdicts: worst case, best Safe window, main limiting factors."""
    now = current or verdicts[0]
    counts = Counter(v.status for v in verdicts)

    best: tuple[int, int] | None = None
    start: int | None = None
    for i, (h, v) in enumerate(zip(hours, verdicts)):
        ok = v.status == Status.SAFE and (h.is_day or not daylight_only)
        if ok and start is None:
            start = i
        if start is not None and (not ok or i == len(hours) - 1):
            end = i if ok else i - 1
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
            start = None

    change = None
    for h, v in zip(hours[1:], verdicts[1:]):
        if v.status != now.status:
            change = (h, v.status)
            break

    factor_counts = Counter(
        r.label for v in verdicts for r in v.reasons if r.level >= Status.CAUTION
    )
    return WindowSummary(
        now=now,
        worst=max((v.status for v in verdicts), default=Status.SAFE),
        counts={s: counts.get(s, 0) for s in Status},
        best_window=(hours[best[0]], hours[best[1]]) if best else None,
        change=change,
        top_factors=[name for name, _ in factor_counts.most_common(3)],
    )
