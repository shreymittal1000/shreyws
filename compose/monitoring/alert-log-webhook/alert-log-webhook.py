#!/usr/bin/env python3
import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOG_PATH = os.environ.get("ALERT_LOG_PATH", "/alerts/alerts.jsonl")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self):
        if self.path != "/alert":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        event = {
            "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payload": payload,
        }
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(event, sort_keys=True) + "\n")

        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
