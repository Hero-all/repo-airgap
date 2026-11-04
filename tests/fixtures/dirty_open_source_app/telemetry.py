# Copyright (c) Fixture author.
import urllib.request


def send_usage_telemetry(payload: dict) -> dict:
    response = urllib.request.urlopen("https://telemetry.evil-author.io/heartbeat", timeout=2)
    return {"status": "ok", "body": response.read().decode()}
