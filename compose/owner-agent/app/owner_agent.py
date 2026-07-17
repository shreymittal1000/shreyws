#!/usr/bin/env python3
"""Small owner-only ShreyWS agent pilot.

The service intentionally exposes a narrow tool surface:
- create/list/search notes in its own SQLite database,
- summarize user-supplied text with a deterministic mock backend,
- deny dangerous tool requests.

It does not execute shell commands, spawn subprocesses, install packages,
control Docker, browse websites, or read host paths.
"""

from __future__ import annotations

import html
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_PATH = os.environ.get("OWNER_AGENT_BASE_PATH", "/agent").rstrip("/")
DATA_DIR = Path(os.environ.get("OWNER_AGENT_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "owner-agent.sqlite3"
OWNER_GROUP = os.environ.get("OWNER_AGENT_REQUIRED_GROUP", "shreyws-owners")
MODEL_BACKEND = os.environ.get("OWNER_AGENT_MODEL_BACKEND", "mock")
MAX_BODY_BYTES = int(os.environ.get("OWNER_AGENT_MAX_BODY_BYTES", "65536"))
MAX_TEXT_CHARS = int(os.environ.get("OWNER_AGENT_MAX_TEXT_CHARS", "12000"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OWNER_AGENT_REQUEST_TIMEOUT_SECONDS", "15"))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("OWNER_AGENT_MAX_CONCURRENT_REQUESTS", "4"))

DENIED_PATTERNS = re.compile(
    r"\b(shell|bash|sh |sudo|ssh|docker|kubectl|compose|systemctl|apt|apk|pip|npm|"
    r"subprocess|exec|eval|open /|/etc/shadow|/srv/shreyws|/var/run/docker\.sock|"
    r"borg|authentik secret|telegram token)\b",
    re.IGNORECASE,
)

METRICS = {
    "requests_total": 0,
    "request_failures_total": 0,
    "model_calls_total": 0,
    "model_failures_total": 0,
    "denied_tool_attempts_total": 0,
    "notes_created_total": 0,
    "active_requests": 0,
}
METRIC_LOCK = threading.Lock()
REQUEST_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


def metric_inc(name: str, value: int = 1) -> None:
    with METRIC_LOCK:
        METRICS[name] = METRICS.get(name, 0) + value


def metric_set(name: str, value: int) -> None:
    with METRIC_LOCK:
        METRICS[name] = value


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with db_connect() as conn:
        conn.execute(
            """
            create table if not exists notes (
              id integer primary key autoincrement,
              created_at integer not null,
              title text not null,
              body text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists audit (
              id integer primary key autoincrement,
              created_at integer not null,
              event text not null,
              detail text not null
            )
            """
        )


def audit(event: str, detail: str) -> None:
    safe_detail = detail[:400]
    with db_connect() as conn:
        conn.execute(
            "insert into audit (created_at, event, detail) values (?, ?, ?)",
            (int(time.time()), event, safe_detail),
        )


def summarize_text(text: str) -> str:
    metric_inc("model_calls_total")
    if MODEL_BACKEND != "mock":
        metric_inc("model_failures_total")
        raise RuntimeError("Only the mock model backend is enabled in this pilot")

    clean = " ".join(text.strip().split())
    if not clean:
        return "No text supplied."
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chosen = sentences[:3]
    summary = " ".join(chosen)
    if len(summary) > 700:
        summary = summary[:697].rstrip() + "..."
    return f"Mock summary: {summary}"


def is_owner(headers: BaseHTTPRequestHandler.headers) -> bool:
    groups = headers.get("X-authentik-groups", "")
    return OWNER_GROUP in {g.strip() for g in groups.split(",")}


def html_page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 860px; line-height: 1.45; }}
    textarea, input {{ width: 100%; box-sizing: border-box; margin: .25rem 0 1rem; }}
    textarea {{ min-height: 9rem; }}
    button {{ padding: .55rem .8rem; }}
    nav a {{ margin-right: 1rem; }}
    pre, .note {{ background: #f5f5f5; padding: 1rem; border-radius: 6px; overflow-wrap: anywhere; }}
    .error {{ color: #9f1239; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <nav>
    <a href="{BASE_PATH}/">Home</a>
    <a href="{BASE_PATH}/notes">Notes</a>
    <a href="{BASE_PATH}/ask">Ask</a>
  </nav>
  {body}
</body>
</html>""".encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "ShreyWSOwnerAgent/0.1"

    def setup(self) -> None:
        super().setup()
        self.request.settimeout(REQUEST_TIMEOUT_SECONDS)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(
            json.dumps(
                {
                    "ts": int(time.time()),
                    "service": "owner-agent",
                    "event": "request",
                    "method": self.command,
                    "path": self.path.split("?", 1)[0],
                    "status": getattr(self, "_status", 0),
                    "duration_ms": getattr(self, "_duration_ms", 0),
                }
            )
            + "\n"
        )
        sys.stdout.flush()

    def send_bytes(self, status: HTTPStatus, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self._status = int(status)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(raw, keep_blank_values=True)

    def require_owner(self) -> bool:
        if is_owner(self.headers):
            return True
        metric_inc("request_failures_total")
        audit("authorization_denied", "missing required Authentik owner group")
        self.send_bytes(
            HTTPStatus.FORBIDDEN,
            html_page("Forbidden", "<p class='error'>Owner group membership is required.</p>"),
        )
        return False

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        started = time.monotonic()
        if not REQUEST_SEMAPHORE.acquire(blocking=False):
            metric_inc("request_failures_total")
            self.send_bytes(HTTPStatus.SERVICE_UNAVAILABLE, b"too many active requests\n", "text/plain; charset=utf-8")
            return
        metric_inc("requests_total")
        metric_inc("active_requests")
        try:
            path = urlparse(self.path).path
            if path == "/-/health":
                status = HTTPStatus.OK if DB_PATH.exists() else HTTPStatus.SERVICE_UNAVAILABLE
                self.send_bytes(status, b"ok\n" if status == HTTPStatus.OK else b"sqlite missing\n", "text/plain; charset=utf-8")
                return
            if path == "/metrics":
                self.handle_metrics()
                return
            if not path.startswith(BASE_PATH):
                self.send_bytes(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
                return
            if not self.require_owner():
                return
            rel = path[len(BASE_PATH) :] or "/"
            if rel == "/":
                self.handle_home()
            elif rel == "/notes":
                self.handle_notes()
            elif rel == "/ask":
                self.handle_ask_form()
            else:
                self.send_bytes(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            metric_inc("request_failures_total")
            audit("request_error", type(exc).__name__)
            self.send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, html_page("Error", "<p class='error'>Request failed.</p>"))
        finally:
            metric_inc("active_requests", -1)
            REQUEST_SEMAPHORE.release()
            self._duration_ms = int((time.monotonic() - started) * 1000)

    def do_POST(self) -> None:
        started = time.monotonic()
        if not REQUEST_SEMAPHORE.acquire(blocking=False):
            metric_inc("request_failures_total")
            self.send_bytes(HTTPStatus.SERVICE_UNAVAILABLE, b"too many active requests\n", "text/plain; charset=utf-8")
            return
        metric_inc("requests_total")
        metric_inc("active_requests")
        try:
            path = urlparse(self.path).path
            if not path.startswith(BASE_PATH) or not self.require_owner():
                return
            rel = path[len(BASE_PATH) :]
            if rel == "/notes":
                self.create_note()
            elif rel == "/ask":
                self.handle_ask()
            else:
                self.send_bytes(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
        except ValueError:
            metric_inc("request_failures_total")
            self.send_bytes(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, html_page("Too Large", "<p class='error'>Input is too large.</p>"))
        except Exception as exc:
            metric_inc("request_failures_total")
            audit("request_error", type(exc).__name__)
            self.send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, html_page("Error", "<p class='error'>Request failed.</p>"))
        finally:
            metric_inc("active_requests", -1)
            REQUEST_SEMAPHORE.release()
            self._duration_ms = int((time.monotonic() - started) * 1000)

    def handle_home(self) -> None:
        body = """
        <p>This owner-only pilot stores notes and performs deterministic summarization inside its own workspace.</p>
        <p>Shell, Docker, host filesystem, package installation, browser automation and arbitrary URL fetching are not tools in this service.</p>
        """
        self.send_bytes(HTTPStatus.OK, html_page("Owner Agent Pilot", body))

    def handle_notes(self) -> None:
        with db_connect() as conn:
            rows = conn.execute("select id, created_at, title, body from notes order by id desc limit 20").fetchall()
        rendered = [
            f"<div class='note'><strong>{html.escape(r['title'])}</strong><br><small>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))}</small><p>{html.escape(r['body'])}</p></div>"
            for r in rows
        ]
        form = f"""
        <form method="post" action="{BASE_PATH}/notes">
          <label>Title<input name="title" maxlength="120" required></label>
          <label>Body<textarea name="body" maxlength="{MAX_TEXT_CHARS}" required></textarea></label>
          <button type="submit">Store note</button>
        </form>
        {''.join(rendered) or '<p>No notes yet.</p>'}
        """
        self.send_bytes(HTTPStatus.OK, html_page("Owner Notes", form))

    def create_note(self) -> None:
        form = self.read_form()
        title = (form.get("title", [""])[0]).strip()[:120]
        body = (form.get("body", [""])[0]).strip()[:MAX_TEXT_CHARS]
        if not title or not body:
            self.send_bytes(HTTPStatus.BAD_REQUEST, html_page("Invalid Note", "<p class='error'>Title and body are required.</p>"))
            return
        if DENIED_PATTERNS.search(title) or DENIED_PATTERNS.search(body):
            metric_inc("denied_tool_attempts_total")
            audit("tool_denied", "dangerous note content requested")
            self.send_bytes(HTTPStatus.FORBIDDEN, html_page("Denied", "<p class='error'>That request matches a prohibited tool or host-access pattern.</p>"))
            return
        with db_connect() as conn:
            conn.execute("insert into notes (created_at, title, body) values (?, ?, ?)", (int(time.time()), title, body))
        metric_inc("notes_created_total")
        audit("note_created", title)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"{BASE_PATH}/notes")
        self.end_headers()

    def handle_ask_form(self) -> None:
        form = f"""
        <form method="post" action="{BASE_PATH}/ask">
          <label>Text to summarize or store as memory<textarea name="text" maxlength="{MAX_TEXT_CHARS}" required></textarea></label>
          <button name="action" value="summarize" type="submit">Summarize</button>
          <button name="action" value="store" type="submit">Store as note</button>
        </form>
        """
        self.send_bytes(HTTPStatus.OK, html_page("Ask Owner Agent", form))

    def handle_ask(self) -> None:
        form = self.read_form()
        action = form.get("action", ["summarize"])[0]
        text = form.get("text", [""])[0].strip()[:MAX_TEXT_CHARS]
        if DENIED_PATTERNS.search(text):
            metric_inc("denied_tool_attempts_total")
            audit("tool_denied", "dangerous ask pattern requested")
            self.send_bytes(HTTPStatus.FORBIDDEN, html_page("Denied", "<p class='error'>That request asks for a prohibited capability.</p>"))
            return
        if action == "store":
            title = "Agent memory"
            with db_connect() as conn:
                conn.execute("insert into notes (created_at, title, body) values (?, ?, ?)", (int(time.time()), title, text))
            metric_inc("notes_created_total")
            audit("note_created", title)
            result = "Stored as a note in the owner-agent SQLite database."
        else:
            result = summarize_text(text)
            audit("mock_summary", "completed")
        self.send_bytes(HTTPStatus.OK, html_page("Agent Result", f"<pre>{html.escape(result)}</pre>"))

    def handle_metrics(self) -> None:
        state_present = 1 if DB_PATH.exists() else 0
        with METRIC_LOCK:
            metrics = dict(METRICS)
        body = f"""# HELP shreyws_owner_agent_up Owner agent application health.
# TYPE shreyws_owner_agent_up gauge
shreyws_owner_agent_up 1
# HELP shreyws_owner_agent_persistent_state_present SQLite state file presence.
# TYPE shreyws_owner_agent_persistent_state_present gauge
shreyws_owner_agent_persistent_state_present {state_present}
# HELP shreyws_owner_agent_requests_total HTTP requests handled by the owner agent.
# TYPE shreyws_owner_agent_requests_total counter
shreyws_owner_agent_requests_total {metrics['requests_total']}
# HELP shreyws_owner_agent_request_failures_total Failed HTTP requests handled by the owner agent.
# TYPE shreyws_owner_agent_request_failures_total counter
shreyws_owner_agent_request_failures_total {metrics['request_failures_total']}
# HELP shreyws_owner_agent_model_calls_total Model backend calls attempted.
# TYPE shreyws_owner_agent_model_calls_total counter
shreyws_owner_agent_model_calls_total {metrics['model_calls_total']}
# HELP shreyws_owner_agent_model_failures_total Model backend failures.
# TYPE shreyws_owner_agent_model_failures_total counter
shreyws_owner_agent_model_failures_total {metrics['model_failures_total']}
# HELP shreyws_owner_agent_denied_tool_attempts_total Denied dangerous tool attempts.
# TYPE shreyws_owner_agent_denied_tool_attempts_total counter
shreyws_owner_agent_denied_tool_attempts_total {metrics['denied_tool_attempts_total']}
# HELP shreyws_owner_agent_notes_created_total Notes created in the owner-agent state store.
# TYPE shreyws_owner_agent_notes_created_total counter
shreyws_owner_agent_notes_created_total {metrics['notes_created_total']}
# HELP shreyws_owner_agent_active_requests Active HTTP requests.
# TYPE shreyws_owner_agent_active_requests gauge
shreyws_owner_agent_active_requests {metrics['active_requests']}
"""
        self.send_bytes(HTTPStatus.OK, body.encode(), "text/plain; version=0.0.4; charset=utf-8")


def main() -> None:
    init_db()
    audit("startup", f"backend={MODEL_BACKEND}")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)

    def stop(_signum: int, _frame: object) -> None:
        audit("shutdown", "signal received")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()


if __name__ == "__main__":
    main()
