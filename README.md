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

Field meanings for `Current` scale, `H1`/`H2` semantics, `sbpH`/`sbORP` scale, and the 64-bit
`status` word are **not confirmed** against vendor documentation — they're inferred from observed
values. See inline comments/labels in `run.py` for what's marked uncertain.

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
| pH (raw) / ORP (raw) | `sbpH`/`sbORP` | scale unconfirmed |
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

## Ideas for evolving this

- Confirm field scales/semantics against Arctic Spas' own forthcoming MQTT spec and correct the
  "raw/unconfirmed" labels in `run.py`.
- Decode the `status`/`error` bit maps once a legend is available, instead of exposing raw hex words.
- Add `Mode1`-`Mode6`/`EndMode*` (filtration schedule) as a schedule sensor.
- Package as a proper add-on repository (`repository.yaml`) for easier install/updates instead of a
  local add-on.
