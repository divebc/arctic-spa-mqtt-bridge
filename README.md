# Arctic Spa MQTT Bridge

A read-only Home Assistant [local add-on](https://www.home-assistant.io/docs/supervisor/addons/) that
connects to an Arctic Spas controller's local websocket (`ws://<spa_host>:8765/`), sends the same
startup query the stock Customer Portal web app sends (`{"query":0}`), and republishes the
`live` / `sett` / `error` / `status` blocks it streams back to MQTT with Home Assistant MQTT
Discovery, so entities appear automatically.

**It never sends anything to the spa besides the read-only query.** No pump, heater, light or
setting commands — this is observation only.

## Background

The spa controller exposes, on the local network:

- `ws://<spa_host>:8765/` — the live telemetry feed described above (reverse-engineered from the
  customer portal's own minified JS; no published spec was used)
- `http://<spa_host>/` — the "Customer Portal" React app (serves this websocket feed to the browser)
- `http://<spa_host>:8080/` — SWUpdate firmware-flash UI (not touched by this project)
- MQTT broker on `1883` — currently only publishes `/local/network/*` connectivity status; no spa
  telemetry is on it yet (that's presumably what the vendor's own in-progress MQTT rollout will add)

## Discovered data (example payload)

```json
{"action": "publish", "topic": "live", "data": {
  "SBConnected": true, "RFIDConnected": true, "STemp": 66, "TSP": 101,
  "P1": 15, "P2": 0, "P3": 0, "P4": 0, "P5": 0,
  "H1": 2, "H2": 2, "Filter": 0, "Oz": 0, "Li": 0, "Fan": 0,
  "HTemp": 66, "Current": 91,
  "sbpH": 676, "sbORP": 774, "sbWear": 100, "sbProducing": false
}}
```

Field meanings for `Current` scale, `H1`/`H2` semantics, `sbORP` scale, and the 64-bit `status`
word are **not confirmed** against vendor documentation — they're inferred from observed values.
See inline comments/labels in `run.py` for what's marked uncertain.

**`sbpH` is confirmed:** raw ÷ 100 = actual pH (`676` → 6.76, verified against a live spa reading
of 6.75). This conversion is done as a Home Assistant template sensor on top of the raw MQTT value
published by this bridge, not inside `run.py` itself — see
[Downstream Home Assistant config](#downstream-home-assistant-config).

**`Current` calibration in progress:** tracked since 2026-11-08 against a separate 240 V circuit
power meter (Emporia Vue) on the spa's breaker, to see whether the two move together and, if so,
derive a raw-to-amps scale factor. Check back after a few days to a week of data.

## Entities created

Under a single "Hot Tub Spa" device:

| Entity | Source field | Notes |
|---|---|---|
| Water Temperature | `STemp` | °F |
| Target Temperature | `TSP` | °F |
| Heater Sensor Temperature | `HTemp` | °F |
| Pump 1 / Pump 2 | `P1`/`P2` | on if > 0 |
| Heater 1 / Heater 2 | `H1`/`H2` | on if > 0, semantics unconfirmed |
| Filtering / Ozone / Light | `Filter`/`Oz`/`Li` | on if > 0 |
| pH (raw) / ORP (raw) | `sbpH`/`sbORP` | pH scale confirmed (÷100); ORP scale unconfirmed |
| Salt Cell Wear % | `sbWear` | |
| Salt Cell Producing | `sbProducing` | |
| Spa Boy / RFID Connected | `SBConnected`/`RFIDConnected` | diagnostic |
| Fault Active | `Upper_ERR_Word`/`Lower_ERR_Word` | on if either != all-zero |
| Error/Status words (raw) | — | diagnostic, 64-bit maps not decoded |
| Firmware versions | `LPCFWVer`/`SBFWVer`/`YOCFWVer` | diagnostic |

## Install

1. Copy `arctic_spa_bridge/` into `/addons/local/` on your Home Assistant host (e.g. via the Samba
   share add-on).
2. Settings → Add-ons → Add-on Store → ⋮ → **Check for updates**. It appears under "Local add-ons."
3. Install, open **Configuration**, set `mqtt_password` to your MQTT broker user's password.
4. Start it and check the log for `Connected, sent startup query`.

## Downstream Home Assistant config

Not part of this add-on, but built on top of its raw MQTT entities, as Home Assistant template
helpers (created via the UI/config-flow, so they're not version-controlled here yet — see "Ideas
for evolving this"):

- `sensor.hot_tub_spa_ph` — `sensor.hot_tub_spa_ph_raw_unconfirmed_scale` ÷ 100, `device_class: ph`.
- `sensor.hot_tub_spa_ph_status` — green (7.0–7.3) / yellow (6.8–6.99 or 7.31–7.6) / red (otherwise),
  used for a color-coded dashboard card and an "out of range" push alert (below 6.9 or above 7.4,
  held 2 minutes).
- `sensor.hot_tub_spa_circuit_current_emporia_240v` — the Emporia Vue circuit power ÷ 240 V, as an
  independent amps estimate to compare against `Current` while calibrating it.

## Ideas for evolving this

- Confirm `Current`/`H1`/`H2`/`sbORP`/`status` scales and semantics against Arctic Spas' own
  forthcoming MQTT spec, or by correlating `Current` against the Emporia comparison above.
- Decode the `status`/`error` bit maps once a legend is available, instead of exposing raw hex words.
- Add `Mode1`-`Mode6`/`EndMode*` (filtration schedule) as a schedule sensor.
- Move the pH conversion and status banding into `run.py` itself (as derived MQTT sensors/discovery
  entries) now that the scale is confirmed, instead of a separate HA-side template helper.
- Bring the downstream Home Assistant config (template sensors, automations, dashboard) into this
  repo as exportable YAML/blueprints, so a fresh install isn't manual.
