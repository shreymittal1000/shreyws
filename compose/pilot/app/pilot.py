#!/usr/bin/env python3
"""Tiny ShreyWS platform pilot service.

This service intentionally avoids user accounts, plugins, command execution,
external databases, and outbound API calls. It exists to exercise the ShreyWS
platform integration points with a small persistent SQLite state file.
"""

from __future__ import annotations

import html
import os
import signal
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_PATH = os.environ.get("PILOT_BASE_PATH", "/pilot").rstrip("/")
DB_PATH = Path(os.environ.get("PILOT_DB_PATH", "/data/pilot.db"))
STARTED_AT = time.time()
os.umask(0o027)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS counters (
              name TEXT PRIMARY KEY,
              value INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at INTEGER NOT NULL,
              event TEXT NOT NULL
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO counters(name, value) VALUES ('requests', 0)")
        conn.execute("INSERT INTO events(created_at, event) VALUES (?, ?)", (int(time.time()), "service_started"))
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{DB_PATH}{suffix}")
        if path.exists():
            path.chmod(0o640)


def record_request(path: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'requests'")
        conn.execute("INSERT INTO events(created_at, event) VALUES (?, ?)", (int(time.time()), f"request:{path}"))


def request_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT value FROM counters WHERE name = 'requests'").fetchone()
        return int(row[0]) if row else 0


def recent_events() -> list[tuple[int, str]]:
    with connect() as conn:
        return list(conn.execute("SELECT created_at, event FROM events ORDER BY id DESC LIMIT 8"))


def metric_text() -> str:
    uptime = max(0, int(time.time() - STARTED_AT))
    count = request_count()
    db_present = 1 if DB_PATH.exists() else 0
    return "\n".join(
        [
            "# HELP shreyws_pilot_up Whether the ShreyWS pilot app can serve requests.",
            "# TYPE shreyws_pilot_up gauge",
            "shreyws_pilot_up 1",
            "# HELP shreyws_pilot_uptime_seconds Seconds since the pilot process started.",
            "# TYPE shreyws_pilot_uptime_seconds counter",
            f"shreyws_pilot_uptime_seconds {uptime}",
            "# HELP shreyws_pilot_requests_total Total pilot HTTP requests handled.",
            "# TYPE shreyws_pilot_requests_total counter",
            f"shreyws_pilot_requests_total {count}",
            "# HELP shreyws_pilot_sqlite_present Whether the pilot SQLite database file exists.",
            "# TYPE shreyws_pilot_sqlite_present gauge",
            f"shreyws_pilot_sqlite_present {db_present}",
            "",
        ]
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ShreyWSPilot/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f'level=info component=pilot client="{self.client_address[0]}" message="{fmt % args}"', flush=True)

    def send_text(self, status: HTTPStatus, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def normalized_path(self) -> str:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == BASE_PATH:
            return "/"
        if path.startswith(f"{BASE_PATH}/"):
            return path[len(BASE_PATH) :]
        return path

    def do_GET(self) -> None:
        path = self.normalized_path()

        if path == "/-/health":
            self.send_text(HTTPStatus.OK, "ok\n")
            return

        if path == "/metrics":
            self.send_text(HTTPStatus.OK, metric_text())
            return

        record_request(path)

        if path in {"/", "/index.html"}:
            events = "\n".join(
                f"<li><code>{created_at}</code> {html.escape(event)}</li>" for created_at, event in recent_events()
            )
            body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ShreyWS Pilot</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; max-width: 780px; }}
    code {{ background: #f3f4f6; padding: 0.1rem 0.25rem; border-radius: 4px; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <h1>ShreyWS Pilot</h1>
  <p>This small internal service verifies ShreyWS app deployment, routing, authentication, persistence, metrics, logs, backups, and rollback.</p>
  <ul>
    <li>Persistent database: <code>{html.escape(str(DB_PATH))}</code></li>
    <li>Requests recorded: <code>{request_count()}</code></li>
    <li>Uptime seconds: <code>{int(time.time() - STARTED_AT)}</code></li>
  </ul>
  <p><a href="{BASE_PATH}/metrics">Metrics</a> | <a href="{BASE_PATH}/-/health">Health</a></p>
  <h2>Recent Events</h2>
  <ul>{events}</ul>
</body>
</html>
"""
            self.send_text(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return

        self.send_text(HTTPStatus.NOT_FOUND, "not found\n")


def main() -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)

    def stop(_signum: int, _frame: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("level=info component=pilot message=\"service started\" port=8000", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
