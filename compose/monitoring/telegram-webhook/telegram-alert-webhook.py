#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SECRET_FILE = os.environ.get("TELEGRAM_ENV_FILE", "/run/secrets/telegram.env")
MAX_MESSAGE_LENGTH = 3900


def log(message):
    print(message, file=sys.stderr, flush=True)


def load_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


SECRETS = load_env_file(SECRET_FILE)
TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or SECRETS.get("TELEGRAM_BOT_TOKEN", "")).strip()
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or SECRETS.get("TELEGRAM_CHAT_ID", "")).strip()
ENABLED_RAW = os.environ.get("TELEGRAM_ENABLED") or SECRETS.get("TELEGRAM_ENABLED", "false")
ENABLED = ENABLED_RAW.strip().lower() in {"1", "true", "yes", "on"}


def telegram_configured():
    return ENABLED and bool(TOKEN) and bool(CHAT_ID)


def severity_prefix(severity):
    severity = (severity or "unknown").lower()
    if severity == "critical":
        return "[CRITICAL]"
    if severity == "warning":
        return "[WARNING]"
    return f"[{severity.upper()}]"


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "\n...[truncated]"


def format_alert(alert):
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    status = alert.get("status", "unknown").upper()
    severity = labels.get("severity", "unknown")
    lines = [
        f"{severity_prefix(severity)} {status}",
        f"Alert: {labels.get('alertname', 'unknown')}",
        f"Severity: {severity}",
    ]

    summary = annotations.get("summary")
    description = annotations.get("description")
    instance = labels.get("instance") or labels.get("name") or labels.get("job")
    service = labels.get("name") or labels.get("job")

    if summary:
        lines.append(f"Summary: {summary}")
    if description:
        lines.append(f"Description: {description}")
    if service:
        lines.append(f"Service: {service}")
    if instance:
        lines.append(f"Instance: {instance}")
    if alert.get("startsAt"):
        lines.append(f"Started: {alert['startsAt']}")
    if status == "RESOLVED" and alert.get("endsAt"):
        lines.append(f"Resolved: {alert['endsAt']}")

    return "\n".join(lines)


def format_payload(payload):
    alerts = payload.get("alerts", [])
    status = payload.get("status", "unknown").upper()
    common = payload.get("commonLabels", {})
    header = [
        f"ShreyWS Alertmanager: {status}",
        f"Grouped alerts: {len(alerts)}",
    ]
    if common.get("alertname"):
        header.append(f"Group: {common['alertname']}")
    if common.get("severity"):
        header.append(f"Severity: {common['severity']}")

    chunks = ["\n".join(header)]
    for alert in alerts[:5]:
        chunks.append(format_alert(alert))
    if len(alerts) > 5:
        chunks.append(f"...and {len(alerts) - 5} more alerts in this group.")

    return truncate("\n\n".join(chunks), MAX_MESSAGE_LENGTH)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Telegram returned HTTP {response.status}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        state = "enabled" if telegram_configured() else "disabled"
        self.wfile.write(f"ok telegram={state}\n".encode("utf-8"))

    def do_POST(self):
        if self.path != "/alert":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("content-length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        if not telegram_configured():
            log("telegram delivery disabled or missing credentials")
            self.send_response(204)
            self.end_headers()
            return

        try:
            send_telegram(format_payload(payload))
        except urllib.error.HTTPError as exc:
            log(f"telegram delivery failed: http_status={exc.code}")
            self.send_response(502)
            self.end_headers()
            return
        except Exception as exc:
            log(f"telegram delivery failed: {type(exc).__name__}")
            self.send_response(502)
            self.end_headers()
            return

        log("telegram delivery succeeded")
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    log("telegram alert webhook starting")
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
