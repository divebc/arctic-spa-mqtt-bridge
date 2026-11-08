#!/usr/bin/env python3
"""
Arctic Spa MQTT Bridge
----------------------
Read-only bridge: connects to the spa controller's local websocket
(ws://<spa_host>:<spa_ws_port>/), sends the same startup query the
Customer Portal web app itself sends ({"query":0}), and republishes
the "live"/"sett"/"error"/"status" blocks it streams back to MQTT
with Home Assistant MQTT Discovery.

This never sends any command/control message to the spa — only the
read-only query that the stock customer portal page sends on load.

Field meanings (temp units, Current scale, H1/H2 semantics, status
bit layout) are inferred from observed values, not from a published
spec. Treat anything marked "raw/unconfirmed" as provisional until
confirmed against Arctic Spas' own documentation.
"""
import asyncio
import json
import logging
import os
import time

import websockets
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arctic_spa_bridge")

OPTIONS_PATH = "/data/options.json"


def load_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        # Local/dev run outside the add-on environment
        return {
            "spa_host": os.environ.get("SPA_HOST", "10.10.30.104"),
            "spa_ws_port": int(os.environ.get("SPA_WS_PORT", "8765")),
            "mqtt_host": os.environ.get("MQTT_HOST", "localhost"),
            "mqtt_port": int(os.environ.get("MQTT_PORT", "1883")),
            "mqtt_username": os.environ.get("MQTT_USERNAME", ""),
            "mqtt_password": os.environ.get("MQTT_PASSWORD", ""),
            "poll_interval_seconds": int(os.environ.get("POLL_INTERVAL", "30")),
        }


OPTS = load_options()

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "arcticspa"
BRIDGE_STATE_TOPIC = f"{NODE_ID}/bridge/state"  # ON/OFF, doubles as availability

DEVICE_INFO = {
    "identifiers": ["arctic_spa_hot_tub"],
    "name": "Hot Tub Spa",
    "manufacturer": "Arctic Spas",
    "model": "Spa Controller (local websocket bridge)",
}

# ---------------------------------------------------------------------------
# Entity map: (component, object_id, topic_block, config)
# topic_block selects which incoming "topic" (live/sett/error/status) feeds
# this entity's state topic: arcticspa/<topic_block>/state
# ---------------------------------------------------------------------------
ENTITIES = [
    # --- live telemetry ---
    ("sensor", "water_temp", "live", {
        "name": "Water Temperature", "unit_of_measurement": "°F",
        "device_class": "temperature", "state_class": "measurement",
        "value_template": "{{ value_json.STemp }}",
    }),
    ("sensor", "target_temp", "live", {
        "name": "Target Temperature", "unit_of_measurement": "°F",
        "device_class": "temperature", "state_class": "measurement",
        "value_template": "{{ value_json.TSP }}",
    }),
    ("sensor", "heater_sensor_temp", "live", {
        "name": "Heater Sensor Temperature", "unit_of_measurement": "°F",
        "device_class": "temperature", "state_class": "measurement",
        "value_template": "{{ value_json.HTemp }}",
    }),
    ("sensor", "current_raw", "live", {
        "name": "Current (raw, unconfirmed scale)", "state_class": "measurement",
        "entity_category": "diagnostic",
        "value_template": "{{ value_json.Current }}",
    }),
    ("binary_sensor", "pump_1", "live", {
        "name": "Pump 1",
        "value_template": "{{ 'ON' if (value_json.P1 | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "pump_2", "live", {
        "name": "Pump 2",
        "value_template": "{{ 'ON' if (value_json.P2 | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "heater_1", "live", {
        "name": "Heater 1 (raw H1>0, unconfirmed semantics)",
        "value_template": "{{ 'ON' if (value_json.H1 | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "heater_2", "live", {
        "name": "Heater 2 (raw H2>0, unconfirmed semantics)",
        "value_template": "{{ 'ON' if (value_json.H2 | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "filtering", "live", {
        "name": "Filtering",
        "value_template": "{{ 'ON' if (value_json.Filter | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "ozone", "live", {
        "name": "Ozone",
        "value_template": "{{ 'ON' if (value_json.Oz | int(0)) > 0 else 'OFF' }}",
    }),
    ("binary_sensor", "light", "live", {
        "name": "Light",
        "value_template": "{{ 'ON' if (value_json.Li | int(0)) > 0 else 'OFF' }}",
    }),
    ("sensor", "ph_raw", "live", {
        "name": "pH (raw, unconfirmed scale)", "state_class": "measurement",
        "entity_category": "diagnostic",
        "value_template": "{{ value_json.sbpH }}",
    }),
    ("sensor", "orp_raw", "live", {
        "name": "ORP (raw, unconfirmed scale)", "state_class": "measurement",
        "entity_category": "diagnostic",
        "value_template": "{{ value_json.sbORP }}",
    }),
    ("sensor", "salt_cell_wear", "live", {
        "name": "Salt Cell Wear", "unit_of_measurement": "%",
        "state_class": "measurement", "entity_category": "diagnostic",
        "value_template": "{{ value_json.sbWear }}",
    }),
    ("binary_sensor", "salt_cell_producing", "live", {
        "name": "Salt Cell Producing",
        "value_template": "{{ 'ON' if value_json.sbProducing else 'OFF' }}",
    }),
    ("binary_sensor", "spa_boy_connected", "live", {
        "name": "Spa Boy Module Connected", "entity_category": "diagnostic",
        "value_template": "{{ 'ON' if value_json.SBConnected else 'OFF' }}",
    }),
    ("binary_sensor", "rfid_connected", "live", {
        "name": "RFID Connected", "entity_category": "diagnostic",
        "value_template": "{{ 'ON' if value_json.RFIDConnected else 'OFF' }}",
    }),

    # --- error block ---
    ("binary_sensor", "fault", "error", {
        "name": "Fault Active", "device_class": "problem",
        "value_template": (
            "{{ 'ON' if (value_json.Upper_ERR_Word != '00000000' "
            "or value_json.Lower_ERR_Word != '00000000') else 'OFF' }}"
        ),
    }),
    ("sensor", "error_word_upper", "error", {
        "name": "Error Word Upper (raw)", "entity_category": "diagnostic",
        "value_template": "{{ value_json.Upper_ERR_Word }}",
    }),
    ("sensor", "error_word_lower", "error", {
        "name": "Error Word Lower (raw)", "entity_category": "diagnostic",
        "value_template": "{{ value_json.Lower_ERR_Word }}",
    }),

    # --- status block (bit meanings not confirmed, kept as raw words) ---
    ("sensor", "status_word_upper", "status", {
        "name": "Status Word Upper (raw, unconfirmed bit map)",
        "entity_category": "diagnostic",
        "value_template": "{{ value_json.Upper_STAT_Word }}",
    }),
    ("sensor", "status_word_lower", "status", {
        "name": "Status Word Lower (raw, unconfirmed bit map)",
        "entity_category": "diagnostic",
        "value_template": "{{ value_json.Lower_STAT_Word }}",
    }),

    # --- settings / firmware (slow-changing) ---
    ("sensor", "main_board_firmware", "sett", {
        "name": "Main Board Firmware", "entity_category": "diagnostic",
        "value_template": "{{ value_json.LPCFWVer }}",
    }),
    ("sensor", "spa_boy_firmware", "sett", {
        "name": "Spa Boy Firmware", "entity_category": "diagnostic",
        "value_template": "{{ value_json.SBFWVer }}",
    }),
    ("sensor", "wifi_module_firmware", "sett", {
        "name": "Wi-Fi Module Firmware", "entity_category": "diagnostic",
        "value_template": "{{ value_json.YOCFWVer }}",
    }),
]


def mqtt_connect():
    client = mqtt.Client(client_id="arctic_spa_bridge")
    if OPTS.get("mqtt_username"):
        client.username_pw_set(OPTS["mqtt_username"], OPTS.get("mqtt_password") or None)
    client.will_set(BRIDGE_STATE_TOPIC, payload="OFF", retain=True)
    client.connect(OPTS["mqtt_host"], int(OPTS["mqtt_port"]), keepalive=60)
    client.loop_start()
    return client


def publish_discovery(client: mqtt.Client):
    for component, object_id, block, cfg in ENTITIES:
        topic = f"{DISCOVERY_PREFIX}/{component}/{NODE_ID}/{object_id}/config"
        payload = dict(cfg)
        payload["unique_id"] = f"{NODE_ID}_{object_id}"
        payload["state_topic"] = f"{NODE_ID}/{block}/state"
        payload["availability_topic"] = BRIDGE_STATE_TOPIC
        payload["payload_available"] = "ON"
        payload["payload_not_available"] = "OFF"
        payload["device"] = DEVICE_INFO
        client.publish(topic, json.dumps(payload), retain=True)
    log.info("Published discovery config for %d entities", len(ENTITIES))


async def spa_loop(client: mqtt.Client):
    uri = f"ws://{OPTS['spa_host']}:{OPTS['spa_ws_port']}/"
    backoff = 2
    while True:
        try:
            log.info("Connecting to spa at %s", uri)
            async with websockets.connect(uri, open_timeout=10, ping_interval=20, ping_timeout=10) as ws:
                client.publish(BRIDGE_STATE_TOPIC, "ON", retain=True)
                backoff = 2
                await ws.send(json.dumps({"query": 0}))
                log.info("Connected, sent startup query")
                last_query = time.time()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=OPTS["poll_interval_seconds"] + 15)
                    except asyncio.TimeoutError:
                        # Nothing pushed recently; re-send the query to keep fresh data flowing
                        await ws.send(json.dumps({"query": 0}))
                        last_query = time.time()
                        continue

                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue

                    if msg.get("action") == "publish" and "topic" in msg and "data" in msg:
                        block = msg["topic"]
                        if block in ("live", "sett", "error", "status"):
                            client.publish(f"{NODE_ID}/{block}/state", json.dumps(msg["data"]))
                    # {"RTC": ...} and other shapes are ignored for now

                    if time.time() - last_query > OPTS["poll_interval_seconds"]:
                        await ws.send(json.dumps({"query": 0}))
                        last_query = time.time()
        except Exception as exc:  # noqa: BLE001 - log and reconnect
            log.warning("Spa connection lost/failed: %s", exc)
            client.publish(BRIDGE_STATE_TOPIC, "OFF", retain=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main():
    client = mqtt_connect()
    time.sleep(1)  # let the MQTT connection establish before publishing discovery
    publish_discovery(client)
    try:
        asyncio.run(spa_loop(client))
    finally:
        client.publish(BRIDGE_STATE_TOPIC, "OFF", retain=True)
        client.loop_stop()


if __name__ == "__main__":
    main()
