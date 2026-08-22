#!/usr/bin/env python3
"""Isolated, read-only Codex adapter for Launchpad."""

from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYSTEM = """You are the ShreyWS Launchpad assistant. Be concise and practical.
You may inspect the read-only /workspace infrastructure repository. You cannot
change the host, Docker, or Launchpad. If asked to deploy or mutate something,
explain the exact Launchpad action and say clearly that you did not perform it.
Never reveal credentials, tokens, or secrets. This is an owner-operated hobby
server for websites and carefully bounded agents.
"""


def run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def status() -> dict[str, object]:
    version = run(["codex", "--version"], 10)
    login = run(["codex", "login", "status"], 15)
    return {
        "installed": version.returncode == 0,
        "version": (version.stdout or version.stderr).strip(),
        "authenticated": login.returncode == 0,
        "authentication": (login.stdout or login.stderr).strip(),
        "credits_remaining": None,
        "credits_note": "Codex CLI does not expose a stable remaining-credit value.",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(f'level=info component=launchpad-assistant message="{fmt % args}"', flush=True)

    def reply(self, code: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/-/health": self.reply(200, {"status":"ok"}); return
        if self.path == "/status": self.reply(200, status()); return
        self.reply(404, {"error":"not found"})

    def do_POST(self) -> None:
        if self.path != "/chat": self.reply(404, {"error":"not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 16384: raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length)); prompt = str(body.get("message", "")).strip()
            if not prompt or len(prompt) > 12000: raise ValueError("Message must be 1-12000 characters")
            result = run([
                "codex", "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
                "--skip-git-repo-check", "--color", "never", "-C", "/workspace", SYSTEM + "\n\nUser:\n" + prompt,
            ], 180)
            if result.returncode: raise RuntimeError((result.stderr or result.stdout)[-2000:])
            self.reply(200, {"reply":result.stdout.strip(), "usage":status()})
        except subprocess.TimeoutExpired: self.reply(504, {"error":"Codex timed out after 180 seconds"})
        except Exception as exc: self.reply(400, {"error":str(exc)})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
