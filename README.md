# flWeatherOps

A terminal weather briefing for **drone flying** and **coastal marine / fishing / Sea-Doo** trips in
South/Central Florida. Run it before you head out and you get a clear **SAFE / CAUTION / NO-GO** for
each activity, the reason behind each call, the best safe window over the next day, and an
hour-by-hour table.

- Forecast data comes from **[Open-Meteo](https://open-meteo.com/)**. No API key needed.
- Waves and swell come from the Open-Meteo **Marine API**.
- High/low tides come from **NOAA CO-OPS**, using the nearest prediction station, which is found automatically.
- Each threshold is a plain number you can change in `config.json`.

## Install

Requires Python 3.10+.

```bash
git clone <this repo> && cd claudeTestOne
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python -m flweatherops
```

You can also install it as a command:

```bash
pip install .
flweatherops
```

## Usage

```bash
flweatherops                          # briefing for the default location(s) (Vero Beach)
flweatherops okeechobee               # a configured location (key or name prefix)
flweatherops vero okeechobee          # several locations: overview table + full briefings
flweatherops --all --summary          # one-line overview of every configured location
flweatherops --units kn               # wind in knots (thresholds are still configured in mph)
flweatherops --hours 48               # evaluate/show 48 hours (1–48, default 24)
flweatherops --activity drone         # only the drone briefing (skips waves and tides)
flweatherops --place "Jupiter, FL"    # look up any place by name (Open-Meteo geocoding)
flweatherops --lat 27.20 --lon -80.17 --name "St. Lucie Inlet"
flweatherops --lat 27.0 --lon -81.0 --inland   # custom inland spot (no waves or tides)
flweatherops --list                   # list configured locations
flweatherops --dump-config > config.json       # start a custom config from the defaults
```

Other flags: `--config PATH`, `--no-cache`, `--no-tides`, `--version`. Run `flweatherops -h` for the full list.

The exit code is `0` when at least one briefing was produced, `1` when every location failed, and `2` for a usage or config error.

### What the output shows

1. **Recommendation table**: one row each for Drone and Marine.
   - **Now**: a colored badge: green SAFE, yellow CAUTION or red NO-GO.
   - **Outlook**: how many hours in the window are safe, caution and no-go.
   - **Best window**: the longest run of SAFE hours. For drones only daylight hours count.
   - **Why / what to watch**: the rules that fired right now (e.g. `Gusts 22 mph ≥ 18 mph`), when the status changes next (e.g. `→ NO-GO from Tue 1 PM`), and the factors that limit you most often over the window.
2. **Current conditions**:
   - weather, temperature and feels-like
   - wind and gusts at 10 m
   - wind aloft at 80 m and 120 m
   - rain chance, humidity, visibility, cloud cover (total and low), UV and CAPE
   - waves, swell and wind waves, for coastal spots
3. **Tides**: the next high and low tides (feet above MLLW) from the nearest NOAA station, with the station's name, ID and distance.
4. **Hourly table**:
   - Columns: time, sky, °F/heat index, rain %, wind direction/speed/gust, wind aloft, visibility, CAPE, UV, waves (ft / period), then a status for each activity.
   - A cell that tripped a limit is colored yellow (Caution) or red (No-Go), so you can see which value caused the call.
   - Night hours are dimmed. Each new day starts a new section.
   - On terminals narrower than about 112 / 96 / 92 columns, the Sky, UV, and Vis/heat-index columns are hidden in that order.
5. **Warnings**: anything that degraded, such as "Marine data unavailable" or "using cached data from 2 h ago".

When you brief more than one location (or pass `--all`), an **overview table** comes first.
It has one line per spot: drone and marine status now, the best windows, current wind and gusts, waves, and rain chance.

## Safety rules

Every hour is checked against every rule for an activity, and the worst result wins.
- A "max" rule trips when the value is **≥** its threshold.
- Visibility is a "min" rule: it trips when the value is **<** its threshold.

| Rule (config key) | Drone caution / no-go | Marine caution / no-go |
|---|---|---|
| Sustained wind 10 m (`wind_speed_mph`) | 12 / 18 mph | 15 / 21 mph |
| Gusts 10 m (`wind_gust_mph`) | 18 / 25 mph | 20 / 28 mph |
| Wind aloft, max of 80 m & 120 m ≈ 400 ft (`wind_aloft_mph`) | 18 / 26 mph | – |
| Significant wave height (`wave_height_ft`) | – | 3 / 5 ft |
| Rain chance (`precip_prob_pct`) | 25 / 50 % | 40 / 70 % |
| Rain amount (`precip_in`) | 0.01 / 0.03 in/h | 0.05 / 0.25 in/h |
| Visibility (`visibility_mi`, min) | < 5 / < 3 mi (Part 107 minimum is 3 SM) | < 3 / < 1 mi |
| Heat index (`heat_index_f`) | 100 / 108 °F | 103 / 110 °F |

There is also a **lightning proxy** that applies to both activities:
- A thunderstorm weather code (WMO 95/96/99) is always **NO-GO**.
- Otherwise, if rain chance is ≥ `precip_prob_min` (30 %):
  - CAPE ≥ `cape_caution` (1000 J/kg) gives **CAUTION**;
  - CAPE ≥ `cape_nogo` (2500 J/kg) gives **NO-GO**.
- CAPE measures how unstable the atmosphere is. In Florida, high CAPE together with a real rain chance is a good sign of afternoon thunderstorms. It is only a proxy: if you hear thunder, you're done.

If wind data is missing for an hour, that hour is marked CAUTION rather than being assumed safe.

These defaults are a starting point for a typical small quad (DJI Mini/Air class) and a small boat or PWC near an inlet. Tune them to your own aircraft, vessel and experience.

## Customizing (`config.json`)

flWeatherOps reads the first config file it finds from:
1. `--config PATH`
2. `./config.json`
3. `~/.config/flweatherops/config.json`

Your file is **deep-merged** over the built-in defaults, so you only write what you want to change.
See `config.example.json`:

```json
{
  "locations": {
    "stuart": {"name": "Stuart / St. Lucie Inlet, FL", "lat": 27.1975, "lon": -80.2528,
               "marine_lat": 27.165, "marine_lon": -80.14, "coastal": true, "default": true},
    "vero-beach": {"default": true}
  },
  "thresholds": {
    "drone":  {"wind_gust_mph":  {"caution": 15, "nogo": 22}},
    "marine": {"wave_height_ft": {"caution": 2,  "nogo": 4}}
  }
}
```

### Location fields

| Field | Meaning |
|---|---|
| `name` | Display name. |
| `lat`, `lon` | Point used for the weather forecast. |
| `coastal` | Set it to `false` to skip waves and tides. The marine verdict is still given, from wind, rain and lightning (useful for Lake Okeechobee). |
| `marine_lat`, `marine_lon` | Optional point for the wave model. Put it a mile or two offshore: the wave model has no data for points on land or in narrow lagoons, so a beach coordinate can come back empty. |
| `tide_station` | Optional NOAA station ID. If you leave it out, the nearest prediction station within `tides.max_station_distance_mi` (25 mi) is used. |
| `default` | Include this location in a plain `flweatherops` run. |

### Thresholds

- Thresholds are always in **mph, ft, mi, in/h, °F and J/kg**, whatever `--units` you display in.
- Set a level to `null` to turn it off, e.g. `"heat_index_f": {"caution": null, "nogo": null}`.

### Other settings

- `timezone`: default `America/New_York`.
- `cache.enabled` and `cache.max_age_hours`.
- `tides.enabled` and `tides.max_station_distance_mi`.

## Caching and degraded data

- Every successful API response is saved under `~/.cache/flweatherops/` (or `$XDG_CACHE_HOME/flweatherops`).
- If a live request fails, you still get a briefing from the cached copy, as long as it is no older than `cache.max_age_hours` (6 h). The briefing says how old the data is.
- Use `--no-cache` to turn this off.
- If the marine or tide request fails, the weather briefing still runs:
  - waves are simply not evaluated, and a warning says so;
  - the marine verdict then comes from wind, rain and lightning only.

## APIs and attribution

| Data | Endpoint |
|---|---|
| Forecast | `https://api.open-meteo.com/v1/forecast`: hourly temperature, apparent temperature, RH, precip probability and amount, weather code, cloud cover (total and low), visibility, wind/direction/gusts at 10 m, wind at 80 m and 120 m, UV, CAPE, plus a `current` block. Requested in °F / mph / inches. |
| Marine | `https://marine-api.open-meteo.com/v1/marine`: wave, wind-wave and swell height, period and direction. The Marine API is served from the `marine-api.` host. |
| Geocoding | `https://geocoding-api.open-meteo.com/v1/search`, used for `--place` and unknown location names. |
| Tides | `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter` (high/low predictions, MLLW). The station list comes from the CO-OPS metadata API and is cached for 30 days. |

- **Open-Meteo**: the free API is for **non-commercial use**, and the data is licensed **CC BY 4.0**. Please keep the attribution line that the tool prints. For commercial use, see their pricing and terms.
- **NOAA CO-OPS**: public-domain US government data.
- This tool shows **model guidance, not an official forecast**. Before you go, also check:
  - the NWS forecast and marine warnings (for example NWS Melbourne, `weather.gov/mlb`);
  - airspace and TFRs, using B4UFLY or LAANC;
  - local conditions on site.

## Development

```bash
pip install pytest
python -m pytest -q
```

The tests run offline. They check the safety rules directly, then run the full CLI against mocked
Open-Meteo and NOAA responses: the default run, `--all` in knots with a failed marine request, the cache
fallback, and a total failure.

Layout:

```
flweatherops/
  config.py   defaults (locations + thresholds), config.json loading
  data.py     HTTP + cache, Open-Meteo / NOAA fetchers, parsing into HourData
  safety.py   rules -> Verdict(status, reasons), window summaries
  render.py   rich panels and tables
  units.py    mph/knots, compass, formatting
  cli.py      argparse entry point
```
