"""Offline tests: core safety rules and end-to-end CLI runs against mocked APIs.

Run with:  python -m pytest -q
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from flweatherops import cli
from flweatherops.config import DEFAULT_CONFIG
from flweatherops.data import HourData
from flweatherops.safety import Status, evaluate, summarize

TH = DEFAULT_CONFIG["thresholds"]


def calm_hour(**kw: Any) -> HourData:
    base = dict(
        time=datetime(2026, 10, 27, 9), temp_f=78, heat_index_f=80, precip_prob_pct=5,
        precip_in=0, weather_code=1, visibility_mi=15, wind_mph=6, gust_mph=10,
        wind80_mph=9, wind120_mph=11, cape=200, wave_ft=1.5,
    )
    base.update(kw)
    return HourData(**base)


def test_calm_is_safe() -> None:
    assert evaluate(calm_hour(), "drone", TH).status is Status.SAFE
    assert evaluate(calm_hour(), "marine", TH).status is Status.SAFE


def test_gusts_trigger_caution_with_reason() -> None:
    v = evaluate(calm_hour(gust_mph=20), "drone", TH)
    assert v.status is Status.CAUTION
    assert any(r.label == "Gusts" for r in v.reasons)
    assert "Gusts 20 mph ≥ 18 mph" in v.reasons[0].text()


def test_aloft_and_visibility_nogo() -> None:
    assert evaluate(calm_hour(wind120_mph=30), "drone", TH).status is Status.NOGO
    assert evaluate(calm_hour(visibility_mi=2), "drone", TH).status is Status.NOGO


def test_waves_only_affect_marine() -> None:
    h = calm_hour(wave_ft=6)
    assert evaluate(h, "marine", TH).status is Status.NOGO
    assert evaluate(h, "drone", TH).status is Status.SAFE


def test_lightning_proxy() -> None:
    assert evaluate(calm_hour(weather_code=95), "marine", TH).status is Status.NOGO
    assert evaluate(calm_hour(cape=1500, precip_prob_pct=35), "drone", TH).status is Status.CAUTION
    # High CAPE with no rain chance is not flagged.
    assert evaluate(calm_hour(cape=3000, precip_prob_pct=5), "drone", TH).status is Status.SAFE


def test_knots_display() -> None:
    v = evaluate(calm_hour(wind_mph=23), "marine", TH)
    assert "20 kt" in v.reasons[0].text("kn")


def test_summary_best_window_and_change() -> None:
    hours = [calm_hour(time=datetime(2026, 10, 27, 6 + i)) for i in range(6)]
    hours[3].gust_mph = 30
    verdicts = [evaluate(h, "drone", TH) for h in hours]
    s = summarize(hours, verdicts)
    assert s.best_window == (hours[0], hours[2])
    assert s.change is not None and s.change[1] is Status.NOGO
    assert s.top_factors == ["Gusts"]


# --------------------------------------------------------------------------- #
# End-to-end with mocked HTTP (see fakes.py)
# --------------------------------------------------------------------------- #


def test_cli_default_run(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--hours", "24"]) == 0
    out = capsys.readouterr().out
    assert "Vero Beach" in out and "Boynton Beach" in out
    assert "Recommendation" in out and "Tides" in out
    assert "Safe Window Finder" in out and "Operational notes" in out


def test_cli_all_knots_and_marine_failure(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    fake_http.marine_fails = True
    assert cli.main(["--all", "--units", "kn", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "All locations" in out and "Okeechobee" in out
    assert "Marine data unavailable" in out


def test_cache_fallback(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["vero"]) == 0
    fake_http.forecast_fails = True
    assert cli.main(["vero"]) == 0
    assert "using cached data" in capsys.readouterr().out


def test_total_failure_exit_code(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    fake_http.forecast_fails = True
    assert cli.main(["vero", "--no-cache"]) == 1
    assert "network unreachable" in capsys.readouterr().out
