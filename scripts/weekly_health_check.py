#!/usr/bin/env python3

import io
import json
import os
import smtplib
import subprocess
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime
from email.message import EmailMessage


HA_URL = "http://localhost:8123"
ORCHESTRATOR_URL = "http://localhost:8001"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


HA_ENTITIES = {
    "Coffee Maker Smart Plug": "switch.plug_91",
    "HydraWise Monitor Smart Plug": "switch.plug_92",
    "Vacuum Cleaner Smart Plug": "switch.plug_93",
    "Bedroom 1 TV Smart Plug": "switch.plug_94",
    "Bedroom 2 Humidifier Smart Plug": "switch.feit_smart_plug1_humidifier_socket_1",
    "Guest Bedroom TV Smart Plug": "switch.plug_fish_tank_light_socket_1",

    "Front Door Motion Sensor": "binary_sensor.hobeian_zg_204zl",
    "Garage Motion Sensor": "binary_sensor.motion_sensor_garage",

    "Single Garage Door": "cover.garage_door_3",
    "Double Garage Door": "cover.garage12",

    "Permanent Lights 2": "light.permanent_lights_2",
    "Permanent Lights": "light.permanent_lights",

    "Media Room Lights": "light.upstairs_media_light_1",
    "Study Room Lights": "light.sd_study_light_1",
    "Living Room Lights": "light.sd_livingroom_light_1",
    "Outdoor Front Lights": "light.outside_front_light_1",
    "Bedroom 2 Lights": "light.bedroom_2_light_1",
    "Hallway Lights": "light.hallway_kids_light_1",
    "Bedroom 1 Lights": "light.bedroom_1_light_1",
    "Master Bedroom Lights": "light.master_bedroom_light_1",

    "Guest Room Fan": "fan.fan_switch",
    "Guest Room Dimmer": "light.dimmer_switch_light_1",
    "Bedroom 3 Dimmer": "light.dimmer_switch_2_light_1",
    "Back Side Lights": "switch.ss01s_t1_3s_switch_1",

    "WiFi Soil Sensor": "sensor.wifi_soil_sensor_temperature",
}


CONTAINERS = [
    "econest-real-orchestrator",
    "econest-real-ollama",
    "econest-real-arcadedb",
    "econest-real-mysql",
    "homeassistant",
]


def get_env_value(name):
    value = os.environ.get(name)

    if value:
        return value

    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if line.startswith(f"{name}="):
                    return (
                        line.split("=", 1)[1]
                        .strip()
                        .strip('"')
                        .strip("'")
                    )

    except OSError:
        pass

    return None


def get_ha_token():
    return get_env_value("HA_TOKEN")


def get_ha_states():
    token = get_ha_token()

    if not token:
        return None, "HA_TOKEN not found"

    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read()), None

    except urllib.error.URLError as e:
        return None, str(e)


def check_docker():
    results = []

    for container in CONTAINERS:
        cmd = [
            "docker",
            "inspect",
            "-f",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}",
            container,
        ]

        try:
            output = subprocess.check_output(
                cmd,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()

            status, health, restarts = output.split("|")

            ok = (
                status == "running"
                and health in ("healthy", "starting", "none")
            )

            results.append(
                {
                    "name": container,
                    "ok": ok,
                    "status": status,
                    "health": health,
                    "restarts": restarts,
                }
            )

        except subprocess.CalledProcessError:
            results.append(
                {
                    "name": container,
                    "ok": False,
                    "status": "not found",
                    "health": "unknown",
                    "restarts": "?",
                }
            )

    return results


def check_orchestrator():
    try:
        req = urllib.request.Request(
            f"{ORCHESTRATOR_URL}/health"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    except Exception as e:
        return {
            "status": "unreachable",
            "error": str(e),
        }


def build_report():
    now = datetime.now()

    output = io.StringIO()

    with redirect_stdout(output):

        print("=" * 60)
        print("ECO NEST WEEKLY HEALTH CHECK")
        print(now.strftime("%A, %B %d, %Y %I:%M %p"))
        print("=" * 60)

        # --------------------------------------------------
        # Docker
        # --------------------------------------------------

        print("\nDOCKER")
        print("-" * 60)

        docker_results = check_docker()

        for item in docker_results:
            symbol = "✓" if item["ok"] else "✗"

            print(
                f"{symbol} {item['name']}: "
                f"status={item['status']}, "
                f"health={item['health']}, "
                f"restarts={item['restarts']}"
            )

        docker_failed = sum(
            1
            for item in docker_results
            if not item["ok"]
        )

        # --------------------------------------------------
        # Orchestrator
        # --------------------------------------------------

        print("\nORCHESTRATOR API")
        print("-" * 60)

        orchestrator = check_orchestrator()

        orchestrator_status = orchestrator.get(
            "status",
            "unknown",
        )

        print(f"Status: {orchestrator_status}")

        services = orchestrator.get(
            "services",
            {},
        )

        for name, value in services.items():
            symbol = "✓" if value else "✗"
            print(f"{symbol} {name}: {value}")

        # --------------------------------------------------
        # Home Assistant / IoT
        # --------------------------------------------------

        print("\nHOME ASSISTANT / IOT DEVICES")
        print("-" * 60)

        states, error = get_ha_states()

        available = 0
        unavailable = 0
        unknown = 0

        if error:
            print(f"✗ Home Assistant: {error}")
            ha_ok = False

        else:
            print("✓ Home Assistant API: reachable")
            ha_ok = True

            state_map = {
                item["entity_id"]: item
                for item in states
            }

            for name, entity_id in HA_ENTITIES.items():

                item = state_map.get(entity_id)

                if not item:
                    print(f"✗ {name}: not found")
                    unavailable += 1
                    continue

                state = item.get(
                    "state",
                    "unknown",
                )

                if state == "unavailable":
                    print(
                        f"✗ {name}: unavailable"
                    )
                    unavailable += 1

                elif state == "unknown":
                    print(
                        f"⚠ {name}: unknown"
                    )
                    unknown += 1

                else:
                    print(
                        f"✓ {name}: available"
                    )
                    available += 1

        # --------------------------------------------------
        # Overall status
        # --------------------------------------------------

        overall_ok = (
            docker_failed == 0
            and orchestrator_status == "ok"
            and ha_ok
            and unavailable == 0
            and unknown == 0
        )

        overall_status = (
            "OK"
            if overall_ok
            else "ATTENTION"
        )

        # --------------------------------------------------
        # Summary
        # --------------------------------------------------

        print("\nSUMMARY")
        print("-" * 60)

        print(
            f"Overall Status: {overall_status}"
        )

        print(
            f"IoT devices checked: "
            f"{len(HA_ENTITIES)}"
        )

        print(
            f"Available: {available}"
        )

        print(
            f"Unavailable/not found: "
            f"{unavailable}"
        )

        print(
            f"Unknown: {unknown}"
        )

        print(
            f"Docker issues: {docker_failed}"
        )

        if orchestrator_status == "ok":
            print("Orchestrator API: OK")
        else:
            print(
                "Orchestrator API: DEGRADED"
            )

        print("=" * 60)

    return output.getvalue(), overall_status


def send_email(report, overall_status):
    sender = get_env_value("SMTP_EMAIL")
    password = get_env_value("SMTP_APP_PASSWORD")
    recipients_raw = get_env_value(
        "HEALTH_CHECK_RECIPIENTS"
    )

    if not sender:
        print(
            "\nEmail not sent: "
            "SMTP_EMAIL not found"
        )
        return False

    if not password:
        print(
            "\nEmail not sent: "
            "SMTP_APP_PASSWORD not found"
        )
        return False

    if not recipients_raw:
        print(
            "\nEmail not sent: "
            "HEALTH_CHECK_RECIPIENTS not found"
        )
        return False

    recipients = [
        email.strip()
        for email in recipients_raw.split(",")
        if email.strip()
    ]

    if not recipients:
        print(
            "\nEmail not sent: "
            "no recipients configured"
        )
        return False

    subject = (
        f"EcoNest Weekly Health Check - "
        f"{overall_status}"
    )

    message = EmailMessage()

    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    message.set_content(report)

    try:
        with smtplib.SMTP_SSL(
            SMTP_HOST,
            SMTP_PORT,
            timeout=20,
        ) as smtp:

            smtp.login(
                sender,
                password,
            )

            smtp.send_message(message)

        print(
            f"\n✓ Email sent to "
            f"{len(recipients)} recipient(s)"
        )

        return True

    except Exception as e:
        print(
            f"\n✗ Email failed: {e}"
        )

        return False


def main():
    report, overall_status = build_report()

    # Print the report to the terminal.
    print(report, end="")

    # Send the same report by email.
    send_email(
        report,
        overall_status,
    )


if __name__ == "__main__":
    main()