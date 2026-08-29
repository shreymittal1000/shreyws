#!/usr/bin/env python3
"""Owner-only ShreyWS application control plane."""

from __future__ import annotations

import html
import base64
import hashlib
import ipaddress
import json
import os
import pty
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import termios
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


BASE = os.getenv("LAUNCHPAD_BASE_PATH", "/launchpad").rstrip("/")
DB_PATH = Path(os.getenv("LAUNCHPAD_DB_PATH", "/state/launchpad.db"))
APPS_ROOT = Path(os.getenv("LAUNCHPAD_APPS_ROOT", "/srv/shreyws/services/launchpad/apps"))
HOST_PROC = Path(os.getenv("LAUNCHPAD_HOST_PROC", "/host/proc"))
HOST_SRV = Path(os.getenv("LAUNCHPAD_HOST_SRV", "/host/srv"))
INTERNAL_HOST = os.getenv("LAUNCHPAD_INTERNAL_HOST", "shreyws.tail1591fa.ts.net")
OWNER_GROUP = os.getenv("LAUNCHPAD_OWNER_GROUP", "shreyws-owners")
PUBLIC_DOMAIN_SUFFIXES = tuple(item.strip().lower().lstrip(".") for item in os.getenv("LAUNCHPAD_PUBLIC_DOMAIN_SUFFIXES", "").split(",") if item.strip())
PUBLIC_ENABLED = bool(PUBLIC_DOMAIN_SUFFIXES)
GIT_CHECK_INTERVAL = int(os.getenv("LAUNCHPAD_GIT_CHECK_INTERVAL", "900"))
KNOWN_HOSTS_PATH = DB_PATH.parent / "ssh_known_hosts"
STARTED = time.time()
LOCK = threading.RLock()
TERMINAL_LOCK = threading.Lock()
TERMINAL_SESSIONS: set[str] = set()
TERMINAL_SESSION_SECONDS = 30 * 60
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})$")
APP_LABEL = "shreyws.launchpad.app"
APP_PREFIX = "shreyws-app-"
NETWORK_PREFIX = "launchpad_app_"
APP_SUBNET = ipaddress.ip_network("172.30.0.0/16")
PLATFORM_PROJECTS = {"authentik", "cloudflared", "diun", "grafana", "homepage", "launchpad", "logging", "monitoring", "pilot", "socket-proxy", "traefik"}

APPROVED_IMAGES = {
    "nginx:1.29.1-alpine": {"label": "Nginx static/web", "port": 80, "read_only": False, "capabilities": ["CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID", "NET_BIND_SERVICE"]},
    "python:3.13.5-alpine3.22": {"label": "Python 3.13", "port": 8000, "read_only": False},
    "node:24.5.0-alpine3.22": {"label": "Node.js 24", "port": 3000, "read_only": False},
    "postgres:16.14-alpine": {"label": "PostgreSQL 16", "port": 5432, "read_only": False, "capabilities": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]},
    "nousresearch/hermes-agent:v2026.8.27": {
        "label": "Hermes Agent 2026.8.27", "port": 8642, "read_only": False,
        "data_target": "/opt/data", "command": ["sleep", "infinity"],
        "setup_command": "hermes setup && hermes gateway start", "setup_label": "Set up Hermes",
        "memory_mb": 4096, "cpus": 2, "storage_gb": 25,
        "shm_size": "1g", "pids_limit": 512,
        "capabilities": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
        "environment": {"API_SERVER_ENABLED": "true", "API_SERVER_HOST": "0.0.0.0"},
        "generated_secrets": ["API_SERVER_KEY"],
        "description": "Persistent Hermes workspace. Deploy, then use Set up Hermes once.",
    },
}

COMPATIBILITY_CAPABILITIES = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "NET_BIND_SERVICE"]


class LaunchpadError(Exception):
    pass


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_state() -> None:
    APPS_ROOT.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DB_PATH.parent, 0o700)
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apps (
              name TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              source TEXT NOT NULL,
              git_ref TEXT NOT NULL,
              memory_mb INTEGER NOT NULL,
              cpus REAL NOT NULL,
              storage_gb INTEGER NOT NULL,
              visibility TEXT NOT NULL,
              domain TEXT NOT NULL,
              container_port INTEGER NOT NULL,
              ipv4 TEXT NOT NULL,
              env_json TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at INTEGER NOT NULL,
              actor TEXT NOT NULL,
              app TEXT NOT NULL,
              action TEXT NOT NULL,
              outcome TEXT NOT NULL,
              detail TEXT NOT NULL
            )
        """)
    os.chmod(DB_PATH, 0o600)


def audit(actor: str, app: str, action: str, outcome: str, detail: str = "") -> None:
    actor = re.sub(r"[^A-Za-z0-9@._-]", "_", actor)[:128] or "unknown"
    with db() as conn:
        conn.execute(
            "INSERT INTO events(created_at,actor,app,action,outcome,detail) VALUES(?,?,?,?,?,?)",
            (int(time.time()), actor, app[:64], action[:64], outcome[:32], detail[:500]),
        )


def docker(args: list[str], *, timeout: int = 120, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *args], input=input_text, text=True, capture_output=True,
        timeout=timeout, check=False,
    )
    if result.returncode:
        raise LaunchpadError((result.stderr or result.stdout or "Docker command failed").strip()[-1200:])
    return result.stdout


def validate_app_name(value: object) -> str:
    name = str(value).strip().lower()
    if not NAME_RE.fullmatch(name):
        raise LaunchpadError("App name must be 3-40 lowercase letters, digits, or hyphens")
    return name


def validate_git_source(value: object) -> str:
    source = str(value).strip()
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "gitlab.com", "codeberg.org"}:
        raise LaunchpadError("Git repositories must use HTTPS on GitHub, GitLab, or Codeberg")
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or not re.fullmatch(r"/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:\.git)?", parsed.path):
        raise LaunchpadError("Git URL must identify one owner/repository without credentials or query parameters")
    return source


def validate_git_ref(value: object) -> str:
    ref = str(value).strip() or "main"
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", ref) or ".." in ref or ref.startswith("/") or ref.endswith("/"):
        raise LaunchpadError("Invalid Git branch")
    return ref


def parse_memory_mb(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise LaunchpadError("RAM must be a whole number of MiB")
    if result < 32 or result > 16384:
        raise LaunchpadError("RAM must be between 32 and 16384 MiB")
    return result


def parse_cpus(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise LaunchpadError("CPU limit must be a number")
    if result < 0.1 or result > 4:
        raise LaunchpadError("CPU limit must be between 0.1 and 4")
    return round(result, 2)


def parse_storage(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise LaunchpadError("Storage must be a whole number of GiB")
    if result < 0 or result > 500:
        raise LaunchpadError("Storage must be between 0 and 500 GiB")
    return result


def validate_payload(raw: dict[str, object]) -> dict[str, object]:
    name = validate_app_name(raw.get("name", ""))
    source_type = str(raw.get("source_type", "image"))
    source = str(raw.get("source", "")).strip()
    git_ref = validate_git_ref(raw.get("git_ref", "main"))
    if source_type == "image":
        if source not in APPROVED_IMAGES:
            raise LaunchpadError("Container image is not on the approved list")
    elif source_type == "git":
        source = validate_git_source(source)
    else:
        raise LaunchpadError("Unknown source type")

    visibility = str(raw.get("visibility", "internal"))
    if visibility not in {"internal", "public"}:
        raise LaunchpadError("Visibility must be internal or public")
    domain = str(raw.get("domain", "")).strip().lower()
    if domain and not DOMAIN_RE.fullmatch(domain):
        raise LaunchpadError("Domain is not a valid fully-qualified hostname")
    if visibility == "public":
        if not domain:
            raise LaunchpadError("Public applications require a domain or subdomain")
        if not PUBLIC_DOMAIN_SUFFIXES:
            raise LaunchpadError("No public domain suffix is configured yet")
        if not any(domain == suffix or domain.endswith("." + suffix) for suffix in PUBLIC_DOMAIN_SUFFIXES):
            raise LaunchpadError("Domain is outside the configured public domain allowlist")
    elif domain:
        raise LaunchpadError("Custom domains are for public applications; internal apps use their private ShreyWS path")
    raw_port = raw.get("container_port", "")
    if source_type == "git" and str(raw_port).strip() == "":
        port = 0
    else:
        try:
            port = int(raw_port or APPROVED_IMAGES.get(source, {}).get("port", 0))
        except (TypeError, ValueError):
            raise LaunchpadError("Container port must be numeric or blank for automatic detection")
        if port < 1 or port > 65535:
            raise LaunchpadError("Container port must be between 1 and 65535")

    ipv4 = str(raw.get("ipv4", "")).strip()
    if ipv4:
        try:
            address = ipaddress.ip_address(ipv4)
        except ValueError:
            raise LaunchpadError("Custom IP is invalid")
        app_network = ipaddress.ip_network(subnet_for(name))
        if address.version != 4 or address not in app_network or address in {app_network.network_address, app_network.broadcast_address, app_network.network_address + 1}:
            raise LaunchpadError(f"Custom IP must be a usable address inside {app_network}")

    env_raw = raw.get("environment", {})
    secrets_raw = raw.get("secrets", {})
    if not isinstance(env_raw, dict) or not isinstance(secrets_raw, dict):
        raise LaunchpadError("Environment and secrets must be key/value objects")
    env: dict[str, str] = {}
    secrets: dict[str, str] = {}
    for target, values in ((env, env_raw), (secrets, secrets_raw)):
        for key, value in values.items():
            if not ENV_RE.fullmatch(str(key)):
                raise LaunchpadError(f"Invalid environment key: {key}")
            text = str(value)
            if len(text) > 8192 or "\x00" in text or "\n" in text or "\r" in text:
                raise LaunchpadError(f"Value for {key} is too large or invalid")
            target[str(key)] = text

    policy = APPROVED_IMAGES.get(source, {}) if source_type == "image" else {}
    env = {**policy.get("environment", {}), **env}
    for key in policy.get("generated_secrets", []):
        secrets.setdefault(str(key), __import__("secrets").token_hex(32))

    return {
        "name": name, "source_type": source_type, "source": source, "git_ref": git_ref,
        "memory_mb": parse_memory_mb(raw.get("memory_mb", policy.get("memory_mb", 256))),
        "cpus": parse_cpus(raw.get("cpus", policy.get("cpus", 0.5))),
        "storage_gb": parse_storage(raw.get("storage_gb", policy.get("storage_gb", 1))),
        "visibility": visibility, "domain": domain, "container_port": port, "ipv4": ipv4,
        "environment": env, "secrets": secrets,
    }


def app_dir(name: str) -> Path:
    path = (APPS_ROOT / name).resolve()
    if APPS_ROOT.resolve() not in path.parents:
        raise LaunchpadError("Invalid application path")
    return path


def subnet_for(name: str) -> str:
    # Stable, deterministic /24 with collision probing handled at network creation.
    octet = 10 + (sum((idx + 1) * ord(char) for idx, char in enumerate(name)) % 230)
    return f"172.30.{octet}.0/24"


def write_secret_config(config: dict[str, object]) -> None:
    path = app_dir(str(config["name"]))
    path.mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(exist_ok=True)
    os.chmod(path, 0o700)
    os.chmod(path / "data", 0o750)
    secret_file = path / "secrets.json"
    secret_file.write_text(json.dumps(config["secrets"]), encoding="utf-8")
    os.chmod(secret_file, 0o600)
    env_file = path / "runtime.env"
    combined = {**config["environment"], **config["secrets"]}
    env_file.write_text("".join(f"{key}={value}\n" for key, value in sorted(combined.items())), encoding="utf-8")
    os.chmod(env_file, 0o600)


def deploy_key_paths(name: str) -> tuple[Path, Path]:
    key_dir = app_dir(name) / ".ssh"
    return key_dir / "deploy_key", key_dir / "deploy_key.pub"


def ensure_github_known_hosts() -> None:
    if KNOWN_HOSTS_PATH.exists() and KNOWN_HOSTS_PATH.stat().st_size:
        return
    request = Request("https://api.github.com/meta", headers={"Accept":"application/vnd.github+json", "User-Agent":"ShreyWS-Launchpad"})
    try:
        with urlopen(request, timeout=20) as response:
            keys = json.load(response).get("ssh_keys", [])
    except Exception as exc:
        raise LaunchpadError(f"Could not obtain GitHub SSH host keys over HTTPS: {exc}")
    valid = [key for key in keys if re.fullmatch(r"(?:ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+", str(key))]
    if not valid:
        raise LaunchpadError("GitHub metadata returned no usable SSH host keys")
    KNOWN_HOSTS_PATH.write_text("".join(f"github.com {key}\n" for key in valid), encoding="utf-8")
    os.chmod(KNOWN_HOSTS_PATH, 0o600)


def deploy_key_info(name: str) -> dict[str, object]:
    private_key, public_key = deploy_key_paths(name)
    if not private_key.exists() or not public_key.exists():
        return {"exists": False, "public_key": "", "fingerprint": ""}
    result = subprocess.run(["ssh-keygen", "-lf", str(public_key), "-E", "sha256"], text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise LaunchpadError("Could not read deploy-key fingerprint")
    fields = result.stdout.strip().split()
    return {"exists": True, "public_key": public_key.read_text(encoding="utf-8").strip(), "fingerprint": fields[1] if len(fields) > 1 else "unknown"}


def generate_deploy_key(name: str, *, rotate: bool = False) -> dict[str, object]:
    name = validate_app_name(name)
    private_key, public_key = deploy_key_paths(name)
    app_dir(name).mkdir(parents=True, exist_ok=True)
    os.chmod(app_dir(name), 0o700)
    private_key.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(private_key.parent, 0o700)
    if private_key.exists() and not rotate:
        return deploy_key_info(name)
    for path in (private_key, public_key):
        path.unlink(missing_ok=True)
    result = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"launchpad:{name}@shreyws", "-f", str(private_key)],
        text=True, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise LaunchpadError((result.stderr or "Deploy-key generation failed").strip())
    os.chmod(private_key, 0o600); os.chmod(public_key, 0o600)
    ensure_github_known_hosts()
    return deploy_key_info(name)


def revoke_deploy_key(name: str) -> None:
    private_key, public_key = deploy_key_paths(validate_app_name(name))
    private_key.unlink(missing_ok=True); public_key.unlink(missing_ok=True)


def git_remote(source: str, name: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(validate_git_source(source))
    private_key, _ = deploy_key_paths(name)
    if not private_key.exists():
        return source, {}
    if parsed.hostname != "github.com":
        raise LaunchpadError("Per-app deploy keys currently support GitHub repositories; use public HTTPS for GitLab or Codeberg")
    ensure_github_known_hosts()
    repo_path = parsed.path.removesuffix(".git") + ".git"
    command = f"ssh -i {private_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={KNOWN_HOSTS_PATH} -o LogLevel=ERROR"
    return f"git@github.com:{repo_path.lstrip('/')}", {"GIT_SSH_COMMAND": command}


def run_git(args: list[str], name: str, source: str, *, timeout: int = 180) -> str:
    remote, extra_env = git_remote(source, name)
    rendered = [remote if value == "{remote}" else value for value in args]
    result = subprocess.run(rendered, text=True, capture_output=True, timeout=timeout, env={**os.environ, **extra_env})
    if result.returncode:
        message = (result.stderr or result.stdout or "Git command failed").strip()[-1200:]
        if "Repository not found" in message or "Permission denied" in message:
            message += " — add this app's public deploy key to the repository with read-only access"
        raise LaunchpadError(message)
    return result.stdout


def list_git_branches(name: str, source: str) -> list[str]:
    output = run_git(["git", "ls-remote", "--heads", "{remote}"], name, source)
    branches = []
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            branches.append(parts[1].removeprefix("refs/heads/"))
    return sorted(set(branches))


def build_source(config: dict[str, object], update: bool = False) -> str:
    if config["source_type"] == "image":
        docker(["pull", str(config["source"])], timeout=600)
        return str(config["source"])
    name = str(config["name"])
    source_dir = app_dir(name) / "source"
    source = str(config["source"])
    remote, extra_env = git_remote(source, name)
    if update and (source_dir / ".git").exists():
        subprocess.run(["git", "-C", str(source_dir), "remote", "set-url", "origin", remote], text=True, capture_output=True, timeout=30, env={**os.environ, **extra_env})
        dockerless = ["git", "-C", str(source_dir), "fetch", "--depth", "1", "origin", str(config["git_ref"])]
        result = subprocess.run(dockerless, text=True, capture_output=True, timeout=180, env={**os.environ, **extra_env})
        if result.returncode:
            raise LaunchpadError(result.stderr.strip()[-1200:])
        subprocess.run(["git", "-C", str(source_dir), "reset", "--hard", "FETCH_HEAD"], check=True, timeout=30)
    else:
        if source_dir.exists():
            shutil.rmtree(source_dir)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", str(config["git_ref"]), remote, str(source_dir)],
            text=True, capture_output=True, timeout=180, env={**os.environ, **extra_env},
        )
        if result.returncode:
            raise LaunchpadError(result.stderr.strip()[-1200:])
    if not (source_dir / "Dockerfile").is_file():
        raise LaunchpadError("Git repository must contain a Dockerfile at its root")
    normalize_build_context_permissions(source_dir)
    tag = f"shreyws-launchpad/{name}:managed"
    # The host currently uses Docker's legacy builder. --force-rm prevents a
    # failed Dockerfile RUN step from leaking a randomly named stopped
    # container into Launchpad's external workload inventory.
    docker(["build", "--pull", "--force-rm", "--tag", tag, str(source_dir)], timeout=1200)
    return tag


def normalize_build_context_permissions(source_dir: Path) -> None:
    """Make tracked build inputs readable without weakening the private app root."""
    result = subprocess.run(
        ["git", "-C", str(source_dir), "ls-files", "-z"],
        capture_output=True, timeout=30, check=True,
    )
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        path = source_dir / relative
        if path.is_symlink() or not path.exists():
            continue
        parent = path.parent
        while parent != source_dir:
            os.chmod(parent, 0o755)
            parent = parent.parent
        if path.is_dir():
            os.chmod(path, 0o755)
        elif path.is_file():
            executable = bool(path.stat().st_mode & 0o100)
            os.chmod(path, 0o755 if executable else 0o644)


def image_exposed_ports(image: str) -> list[int]:
    details = json.loads(docker(["image", "inspect", image], timeout=30))[0]
    exposed = details.get("Config", {}).get("ExposedPorts") or {}
    ports = []
    for value in exposed:
        match = re.fullmatch(r"(\d{1,5})/(?:tcp|udp)", str(value))
        if match and value.endswith("/tcp"):
            port = int(match.group(1))
            if 1 <= port <= 65535:
                ports.append(port)
    return sorted(set(ports))


def resolve_container_port(config: dict[str, object], image: str) -> int:
    configured = int(config.get("container_port") or 0)
    if configured:
        return configured
    ports = image_exposed_ports(image)
    if len(ports) == 1:
        return ports[0]
    if not ports:
        raise LaunchpadError(
            "The image does not declare a TCP port. Add EXPOSE <port> to the Dockerfile "
            "or enter the container port under Advanced deployment settings."
        )
    rendered = ", ".join(str(port) for port in ports)
    raise LaunchpadError(
        f"The image exposes multiple TCP ports ({rendered}). Choose the web port under Advanced deployment settings."
    )


def git_status_path(name: str) -> Path:
    return app_dir(name) / "git_status.json"


def check_git_update(config: dict[str, object]) -> dict[str, object]:
    name, source, branch = str(config["name"]), str(config["source"]), str(config["git_ref"])
    status: dict[str, object] = {"checked_at": int(time.time()), "branch": branch, "update_available": False, "remote_sha": "", "local_sha": "", "error": ""}
    try:
        output = run_git(["git", "ls-remote", "{remote}", f"refs/heads/{branch}"], name, source)
        remote_sha = output.split()[0] if output.split() else ""
        if not remote_sha:
            raise LaunchpadError(f"Branch not found: {branch}")
        source_dir = app_dir(name) / "source"
        result = subprocess.run(["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True, capture_output=True, timeout=15)
        local_sha = result.stdout.strip() if result.returncode == 0 else ""
        status.update(remote_sha=remote_sha, local_sha=local_sha, update_available=bool(local_sha and local_sha != remote_sha))
    except Exception as exc:
        status["error"] = str(exc)[-500:]
    path = git_status_path(name)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status), encoding="utf-8"); os.chmod(temporary, 0o600); temporary.replace(path)
    return status


def read_git_status(name: str) -> dict[str, object]:
    path = git_status_path(name)
    if not path.exists():
        return {"checked_at": 0, "update_available": False, "error": "not checked yet"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"checked_at": 0, "update_available": False, "error": "status unavailable"}


def git_details(name: str) -> dict[str, object]:
    config = load_config(name)
    if config["source_type"] != "git":
        raise LaunchpadError("Application is not Git-based")
    branches: list[str] = []
    branch_error = ""
    try:
        branches = list_git_branches(name, str(config["source"]))
    except Exception as exc:
        branch_error = str(exc)
    return {
        "name": name, "source": config["source"], "branch": config["git_ref"],
        "deploy_key": deploy_key_info(name), "branches": branches,
        "branch_error": branch_error, "update": read_git_status(name),
    }


def git_check_loop() -> None:
    while True:
        time.sleep(30 if time.time() - STARTED < 60 else max(60, GIT_CHECK_INTERVAL))
        try:
            with db() as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM apps WHERE source_type='git'")]
            for name in names:
                check_git_update(load_config(name))
        except Exception as exc:
            print(f'level=warning component=launchpad-git-check message="{str(exc)[:500]}"', flush=True)


def ensure_network(name: str) -> str:
    network = NETWORK_PREFIX + name
    existing = docker(["network", "ls", "--filter", f"name=^{network}$", "--format", "{{.Name}}"])
    if not existing.strip():
        docker(["network", "create", "--driver", "bridge", "--subnet", subnet_for(name), network])
    connected = docker(["inspect", "--format", "{{json .NetworkSettings.Networks}}", "shreyws-traefik"])
    if network not in connected:
        docker(["network", "connect", network, "shreyws-traefik"])
    return network


def load_config(name: str) -> dict[str, object]:
    with db() as conn:
        row = conn.execute("SELECT * FROM apps WHERE name=?", (name,)).fetchone()
    if not row:
        raise LaunchpadError("Application not found")
    result = dict(row)
    result["environment"] = json.loads(result.pop("env_json"))
    secret_file = app_dir(name) / "secrets.json"
    result["secrets"] = json.loads(secret_file.read_text(encoding="utf-8")) if secret_file.exists() else {}
    return result


def route_labels(config: dict[str, object], network: str) -> list[str]:
    name = str(config["name"])
    port = int(config["container_port"])
    domain = str(config["domain"])
    labels = [
        f"traefik.enable=true", f"traefik.docker.network={network}",
        f"traefik.http.services.launchpad-{name}.loadbalancer.server.port={port}",
        f"{APP_LABEL}={name}", "shreyws.trust_class=owner", "diun.enable=true",
    ]
    if config["visibility"] == "public":
        labels.extend([
            f"traefik.http.routers.launchpad-{name}.entrypoints=cloudflare",
            f"traefik.http.routers.launchpad-{name}.rule=Host(`{domain}`)",
        ])
    else:
        path = f"/apps/{name}"
        labels.extend([
            f"traefik.http.routers.launchpad-{name}.tls=true",
            f"traefik.http.routers.launchpad-{name}.entrypoints=websecure",
            f"traefik.http.routers.launchpad-{name}.rule=Host(`{INTERNAL_HOST}`) && PathPrefix(`{path}`)",
            f"traefik.http.middlewares.launchpad-{name}-strip.stripprefix.prefixes={path}",
            f"traefik.http.routers.launchpad-{name}.middlewares=authentik-forward-auth@docker,launchpad-{name}-strip@docker",
        ])
    return labels


def create_container(
    config: dict[str, object], image: str, *, container: str | None = None,
    routed: bool = True, restart: str = "unless-stopped", data_path: Path | None = None,
) -> None:
    name = str(config["name"])
    container = container or APP_PREFIX + name
    network = ensure_network(name)
    data = data_path or app_dir(name) / "data"
    data.mkdir(parents=True, exist_ok=True)
    image_policy = APPROVED_IMAGES.get(str(config["source"]), {}) if config["source_type"] == "image" else {}
    args = [
        "create", "--name", container, "--restart", restart,
        "--memory", f"{config['memory_mb']}m", "--cpus", str(config["cpus"]),
        "--pids-limit", str(image_policy.get("pids_limit", 256)), "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--network", network,
        "--env-file", str(app_dir(name) / "runtime.env"),
        "--mount", f"type=bind,src={data},dst={image_policy.get('data_target', '/data')}",
    ]
    if image_policy.get("shm_size"):
        args.extend(["--shm-size", str(image_policy["shm_size"])])
    # A fixed address may still belong to the live container during an update;
    # the unrouted preflight should always use an automatically assigned IP.
    if config["ipv4"] and routed:
        args.extend(["--ip", str(config["ipv4"])])
    capabilities = list(image_policy.get("capabilities", []))
    if config.get("runtime_profile") == "compatibility":
        capabilities.extend(COMPATIBILITY_CAPABILITIES)
    for capability in sorted(set(capabilities)):
        args.extend(["--cap-add", capability])
    labels = route_labels(config, network) if routed else ["shreyws.workload=launchpad-preflight", "traefik.enable=false"]
    for label in labels:
        args.extend(["--label", label])
    args.extend([image, *[str(value) for value in image_policy.get("command", [])]])
    docker(args)
    docker(["start", container])


def startup_failure(container: str) -> str:
    details = json.loads(docker(["inspect", container], timeout=20))[0]
    state = details.get("State", {})
    if state.get("Running"):
        return ""
    logs = docker(["logs", "--tail", "80", container], timeout=20).strip()[-1600:]
    reason = state.get("Error") or f"container exited with code {state.get('ExitCode', 'unknown')}"
    if "Operation not permitted" in logs and ("chown(" in logs or "permission" in logs.lower()):
        guidance = (
            "The container requires root privileges that Launchpad intentionally removes. "
            "Use a rootless/unprivileged runtime image and a port above 1024."
        )
    else:
        guidance = "Review the startup log below or provide the environment values the application requires."
    return f"Startup check failed: {reason}. {guidance}\n\n{logs}".strip()


def container_exists_by_name(container: str) -> bool:
    output = docker(["ps", "-a", "--filter", f"name=^{container}$", "--format", "{{.Names}}"])
    return output.strip() == container


def preflight_container(config: dict[str, object], image: str) -> list[str]:
    name = str(config["name"])
    container = f"{APP_PREFIX}{name}-preflight"
    data = app_dir(name) / ".preflight-data"
    if container_exists_by_name(container):
        docker(["rm", "-f", container], timeout=30)
    if data.exists():
        shutil.rmtree(data)
    warnings: list[str] = []
    try:
        create_container(config, image, container=container, routed=False, restart="no", data_path=data)
        time.sleep(3)
        failure = startup_failure(container)
        if failure and "requires root privileges" in failure:
            docker(["rm", "-f", container], timeout=30)
            config["runtime_profile"] = "compatibility"
            create_container(config, image, container=container, routed=False, restart="no", data_path=data)
            time.sleep(3)
            failure = startup_failure(container)
            if not failure:
                warnings.append(
                    "The image needed Launchpad's limited compatibility capabilities; "
                    "no privileged mode or host access was granted."
                )
        if failure:
            raise LaunchpadError(failure)
    finally:
        if container_exists_by_name(container):
            docker(["rm", "-f", container], timeout=30)
        if data.exists():
            shutil.rmtree(data)
    return warnings


def save_config(config: dict[str, object]) -> None:
    now = int(time.time())
    with db() as conn:
        conn.execute("""
          INSERT INTO apps(name,source_type,source,git_ref,memory_mb,cpus,storage_gb,visibility,domain,container_port,ipv4,env_json,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(name) DO UPDATE SET source_type=excluded.source_type,source=excluded.source,
            git_ref=excluded.git_ref,memory_mb=excluded.memory_mb,cpus=excluded.cpus,
            storage_gb=excluded.storage_gb,visibility=excluded.visibility,domain=excluded.domain,
            container_port=excluded.container_port,ipv4=excluded.ipv4,env_json=excluded.env_json,updated_at=excluded.updated_at
        """, (
            config["name"], config["source_type"], config["source"], config["git_ref"],
            config["memory_mb"], config["cpus"], config["storage_gb"], config["visibility"],
            config["domain"], config["container_port"], config["ipv4"],
            json.dumps(config["environment"], sort_keys=True), now, now,
        ))


def deploy(config: dict[str, object], actor: str, update: bool = False) -> dict[str, object]:
    name = str(config["name"])
    with LOCK:
        if not update:
            with db() as conn:
                if conn.execute("SELECT 1 FROM apps WHERE name=?", (name,)).fetchone():
                    raise LaunchpadError("Application name already exists")
        write_secret_config(config)
        try:
            image = build_source(config, update=update)
            config["container_port"] = resolve_container_port(config, image)
            warnings = preflight_container(config, image)
            container = APP_PREFIX + name
            rollback = container + "-rollback"
            had_previous = container_exists(name)
            if container_exists_by_name(rollback):
                docker(["rm", "-f", rollback], timeout=30)
            if had_previous:
                docker(["stop", container], timeout=60)
                docker(["rename", container, rollback], timeout=30)
            try:
                create_container(config, image)
                time.sleep(3)
                failure = startup_failure(container)
                if failure:
                    raise LaunchpadError(failure)
            except Exception:
                if container_exists_by_name(container):
                    docker(["rm", "-f", container], timeout=30)
                if had_previous and container_exists_by_name(rollback):
                    docker(["rename", rollback, container], timeout=30)
                    docker(["start", container], timeout=60)
                raise
            if had_previous and container_exists_by_name(rollback):
                docker(["rm", "-f", rollback], timeout=30)
            save_config(config)
            if config["source_type"] == "git":
                check_git_update(config)
            audit(actor, name, "update" if update else "deploy", "success")
            return {
                "status": "deployed", "name": name,
                "container_port": config["container_port"], "warnings": warnings,
            }
        except Exception as exc:
            audit(actor, name, "update" if update else "deploy", "failed", str(exc))
            raise


def container_exists(name: str) -> bool:
    return container_exists_by_name(APP_PREFIX + name)


def app_rows() -> list[dict[str, object]]:
    with db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM apps ORDER BY name")]
    states: dict[str, dict[str, object]] = {}
    output = docker(["ps", "-a", "--filter", f"label={APP_LABEL}", "--format", "{{json .}}"])
    for line in output.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        states[item["Names"].removeprefix(APP_PREFIX)] = item
    stats: dict[str, dict[str, str]] = {}
    running = [APP_PREFIX + row["name"] for row in rows if states.get(str(row["name"]), {}).get("State") == "running"]
    if running:
        raw = docker(["stats", "--no-stream", "--format", "{{json .}}", *running], timeout=30)
        for line in raw.splitlines():
            item = json.loads(line)
            stats[item["Name"].removeprefix(APP_PREFIX)] = {"cpu": item["CPUPerc"], "memory": item["MemUsage"], "memory_percent": item["MemPerc"]}
    result = []
    for row in rows:
        name = str(row["name"])
        state = states.get(name, {})
        policy = APPROVED_IMAGES.get(str(row["source"]), {}) if row["source_type"] == "image" else {}
        result.append({
            **row, "env_json": None, "state": state.get("State", "missing"),
            "status": state.get("Status", "container missing"), "stats": stats.get(name, {}),
            "url": f"https://{row['domain']}" if row["domain"] else f"https://{INTERNAL_HOST}/apps/{name}/",
            "git_status": read_git_status(name) if row["source_type"] == "git" else None,
            "setup_command": policy.get("setup_command", ""), "setup_label": policy.get("setup_label", ""),
        })
    return result


def external_rows() -> list[dict[str, object]]:
    """Discover non-platform containers without assuming lifecycle ownership."""
    ids = [line for line in docker(["ps", "-aq"]).splitlines() if line]
    if not ids:
        return []
    containers = json.loads(docker(["inspect", *ids], timeout=30))
    running_ids = [item["Id"] for item in containers if item.get("State", {}).get("Running")]
    stats: dict[str, dict[str, str]] = {}
    if running_ids:
        raw = docker(["stats", "--no-stream", "--format", "{{json .}}", *running_ids], timeout=30)
        for line in raw.splitlines():
            if line.strip():
                item = json.loads(line)
                stats[item["ID"]] = {"cpu": item.get("CPUPerc", ""), "memory": item.get("MemUsage", ""), "memory_percent": item.get("MemPerc", "")}
    result = []
    for item in containers:
        labels = item.get("Config", {}).get("Labels") or {}
        project = labels.get("com.docker.compose.project", "manual")
        if labels.get(APP_LABEL) or labels.get("shreyws.workload") or project in PLATFORM_PROJECTS:
            continue
        host = item.get("HostConfig", {})
        networks = item.get("NetworkSettings", {}).get("Networks") or {}
        memory = int(host.get("Memory") or 0)
        nano_cpus = int(host.get("NanoCpus") or 0)
        state = item.get("State", {})
        container_id = str(item.get("Id", ""))
        result.append({
            "id": container_id, "short_id": container_id[:12], "name": str(item.get("Name", "")).removeprefix("/"),
            "image": item.get("Config", {}).get("Image", "unknown"),
            "project": project, "service": labels.get("com.docker.compose.service", ""),
            "state": state.get("Status", "unknown"), "status": state.get("Health", {}).get("Status", state.get("Status", "unknown")),
            "memory_mb": round(memory / 1048576) if memory else None,
            "cpus": round(nano_cpus / 1_000_000_000, 2) if nano_cpus else None,
            "networks": [{"network": name, "ipv4": value.get("IPAddress", ""), "ipv6": value.get("GlobalIPv6Address", "")} for name, value in sorted(networks.items())],
            "stats": stats.get(container_id[:12], stats.get(container_id, {})),
        })
    return sorted(result, key=lambda value: (str(value["project"]), str(value["name"])))


def memory_summary() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in (HOST_PROC / "meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    return {
        "total": values["MemTotal"], "available": values["MemAvailable"],
        "used": values["MemTotal"] - values["MemAvailable"],
        "swap_total": values.get("SwapTotal", 0), "swap_free": values.get("SwapFree", 0),
    }


def system_summary() -> dict[str, object]:
    mem = memory_summary()
    disk = shutil.disk_usage(HOST_SRV)
    load = (HOST_PROC / "loadavg").read_text().split()[:3]
    cpu_count = sum(1 for line in (HOST_PROC / "stat").read_text().splitlines() if re.match(r"^cpu\d+ ", line)) or 1
    with db() as conn:
        assigned = conn.execute("SELECT COALESCE(SUM(memory_mb),0),COALESCE(SUM(storage_gb),0) FROM apps").fetchone()
    return {
        "memory": mem,
        "storage": {"total": disk.total, "used": disk.used, "free": disk.free},
        "load": load, "cpu_count": cpu_count, "uptime_seconds": int(float((HOST_PROC / "uptime").read_text().split()[0])),
        "assigned_memory_mb": assigned[0], "assigned_storage_gb": assigned[1],
        "launchpad_uptime_seconds": int(time.time() - STARTED), "public_enabled": PUBLIC_ENABLED,
        "public_domain_suffixes": list(PUBLIC_DOMAIN_SUFFIXES),
    }


def recent_events() -> list[dict[str, object]]:
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]


def assistant_request(path: str, body: dict[str, object] | None = None) -> dict[str, object]:
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(f"http://launchpad-assistant:8090{path}", data=payload,
                      headers={"Content-Type": "application/json"} if payload else {})
    with urlopen(request, timeout=190) as response:
        return json.load(response)


def websocket_accept(key: str) -> str:
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def websocket_frame(payload: bytes, opcode: int = 1) -> bytes:
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, length))
    elif length <= 65535:
        header = bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", length)
    return header + payload


def websocket_read_exact(connection: socket.socket, length: int) -> bytes:
    value = bytearray()
    while len(value) < length:
        chunk = connection.recv(length - len(value))
        if not chunk:
            raise ConnectionError("WebSocket closed")
        value.extend(chunk)
    return bytes(value)


def websocket_receive(connection: socket.socket) -> tuple[int, bytes]:
    header = websocket_read_exact(connection, 2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", websocket_read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", websocket_read_exact(connection, 8))[0]
    if not masked:
        raise LaunchpadError("Browser WebSocket frames must be masked")
    if length > 8192:
        raise LaunchpadError("Terminal input frame is too large")
    mask = websocket_read_exact(connection, 4)
    payload = websocket_read_exact(connection, length)
    return opcode, bytes(value ^ mask[index % 4] for index, value in enumerate(payload))


def terminal_origin_allowed(origin: str) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme == "https" and parsed.netloc == INTERNAL_HOST and parsed.path == ""


INDEX = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShreyWS Launchpad</title><style>
:root{--bg:#020503;--panel:#050a07;--panel2:#08100b;--line:#183a24;--line-hot:#2b6b3d;--text:#d8ffe1;--muted:#6fa87c;--accent:#42ff72;--green:#42ff72;--red:#ff5c70;--amber:#d7ff54}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:linear-gradient(rgba(66,255,114,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(66,255,114,.018) 1px,transparent 1px),radial-gradient(circle at 18% -10%,#0b2815 0,transparent 34%),var(--bg);background-size:32px 32px,32px 32px,auto,auto;color:var(--text);font:14px/1.5 "IBM Plex Mono","JetBrains Mono","SFMono-Regular",Consolas,"Liberation Mono",monospace}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,transparent 0,transparent 3px,rgba(0,0,0,.08) 4px);z-index:10}@keyframes cursor-blink{0%,46%{opacity:1}47%,100%{opacity:0}}@keyframes button-spin{to{transform:rotate(360deg)}}header{padding:32px clamp(18px,5vw,72px) 20px;display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid #0c2112}h1{margin:2px 0 0;font-size:clamp(28px,4vw,48px);font-weight:650;letter-spacing:-.06em;color:#ecfff0;text-shadow:0 0 24px #42ff7240}h1:after{content:"_";display:inline-block;margin-left:.08em;color:var(--accent);animation:cursor-blink 1.05s steps(1,end) infinite;text-shadow:0 0 12px var(--accent)}sup{font-size:.48em;color:var(--accent);letter-spacing:0;vertical-align:super}.eyebrow{color:var(--accent);text-transform:uppercase;letter-spacing:.18em;font-size:11px;font-weight:800}.eyebrow:before{content:"[ "}.eyebrow:after{content:" ]"}.muted{color:var(--muted)}main{padding:24px clamp(18px,5vw,72px) 70px;max-width:1600px;margin:auto}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{position:relative;background:linear-gradient(145deg,rgba(8,16,11,.98),rgba(3,8,5,.98));border:1px solid var(--line);border-radius:3px;padding:20px;box-shadow:inset 0 1px #49ff7110,0 16px 50px #0008}.card:before{content:"";position:absolute;width:8px;height:8px;left:-1px;top:-1px;border-left:1px solid var(--accent);border-top:1px solid var(--accent)}.metric{grid-column:span 3}.metric b{display:block;font-size:23px;margin:8px 0;color:#eaffee;font-weight:600}.apps{grid-column:span 8}.deploy{grid-column:span 4}.events{grid-column:span 12}h2{margin:0 0 16px;font-size:15px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);font-weight:650}h2:before{content:"> ";color:#297e41}button,.button{border:1px solid var(--line-hot);border-radius:2px;padding:9px 12px;background:#09150d;color:#a9ffbc;cursor:pointer;font:700 12px/1.2 inherit;text-transform:uppercase;letter-spacing:.04em;transition:transform .08s ease,background .15s ease,border-color .15s ease,box-shadow .15s ease,opacity .15s ease}button.primary{background:var(--accent);border-color:var(--accent);color:#011405;box-shadow:0 0 18px #42ff7228}button.danger{background:#1b080b;border-color:#7d2633;color:#ff8f9d}button:hover{border-color:var(--accent);color:#eaffee;background:#102819;box-shadow:0 0 14px #42ff721b}button.primary:hover{color:#001504;background:#73ff94;filter:none}button.ack{transform:translateY(1px) scale(.97);background:#173d21;border-color:var(--accent);box-shadow:inset 0 0 14px #42ff7240}button.busy{cursor:wait;opacity:.78}button.busy:before{content:"";display:inline-block;width:10px;height:10px;margin-right:7px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;vertical-align:-1px;animation:button-spin .7s linear infinite}button:disabled{pointer-events:none}input,select,textarea{width:100%;background:#020604;border:1px solid var(--line);border-radius:2px;color:var(--text);padding:10px;margin:5px 0 12px;font:13px/1.4 inherit;outline:none}input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 2px #42ff7214}input::placeholder,textarea::placeholder{color:#416d4a}label{color:#7fc98f;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.app{border-top:1px dashed var(--line);padding:16px 0}.app:first-of-type{border-top:0}.app-head,.actions{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}.pill{padding:3px 8px;border:1px solid #244b2e;border-radius:2px;background:#071009;color:#76a981;font-size:10px;text-transform:uppercase}.pill.running{border-color:#278c42;background:#092212;color:var(--green);box-shadow:inset 0 0 12px #42ff7210}.pill.running:after{content:"\25a0";display:inline-block;margin-left:6px;font-size:7px;animation:cursor-blink 1.35s steps(1,end) infinite}.pill.exited,.pill.missing{border-color:#63303a;color:#ff8291;background:#18080b}pre{white-space:pre-wrap;max-height:280px;overflow:auto;background:#010302;padding:14px;border:1px solid #102a18;border-radius:2px;color:#9ceead}.bar{height:5px;background:#0b1a0f;border:1px solid #17341f;border-radius:0;overflow:hidden}.bar i{display:block;height:100%;background:var(--accent);box-shadow:0 0 10px var(--accent)}dialog{background:var(--panel);color:var(--text);border:1px solid var(--line-hot);border-radius:3px;width:min(850px,92vw);box-shadow:0 0 80px #000}dialog::backdrop{background:#000d}@media(prefers-reduced-motion:reduce){h1:after,.pill.running:after,button.busy:before{animation:none}button.ack{transform:none}}@media(max-width:950px){.metric{grid-column:span 6}.apps,.deploy{grid-column:span 12}}@media(max-width:520px){header{align-items:start;gap:18px}.metric{grid-column:span 12}.row{grid-template-columns:1fr}}
input,textarea{caret-color:var(--accent);caret-shape:block}input[type=number]{appearance:textfield;-moz-appearance:textfield;padding-right:40px}input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}.stepper{position:relative}.stepper input{margin-bottom:12px}.step-controls{position:absolute;right:1px;top:6px;bottom:13px;width:31px;display:grid;grid-template-rows:1fr 1fr;border-left:1px solid var(--line)}.step-controls button{min-width:0;padding:0;border:0;border-radius:0;background:#07140b;color:var(--accent);font-size:9px;line-height:1}.step-controls button:first-child{border-bottom:1px solid var(--line)}.step-controls button:hover{background:#12301a;box-shadow:inset 0 0 10px #42ff7222}
.loading-value{color:var(--accent)!important;letter-spacing:.18em;animation:cursor-blink 1.05s steps(1,end) infinite}.loading-bar{width:14%!important;animation:cursor-blink 1.05s steps(1,end) infinite}.terminal-output{height:min(58vh,560px);max-height:none;overflow:auto;margin:14px 0;background:#000;color:#baffc9;white-space:pre-wrap;word-break:break-word}.terminal-form{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:start}.terminal-form input{margin:0}.terminal-status{color:var(--muted)}@media(prefers-reduced-motion:reduce){.loading-value,.loading-bar{animation:none}}
</style></head><body><header><div><div class="eyebrow">Personal cloud control plane</div><h1>ShreyWS Launchpad</h1><div class="muted">Control panel for apps running on ShreyWS<sup>TM</sup></div></div><button onclick="loadAll()">Refresh</button></header><main><div id="metrics" class="grid"></div><div class="grid" style="margin-top:16px"><section class="card apps"><h2>Applications</h2><div id="apps">Loading…</div></section><section class="card deploy"><h2>Launch application</h2><form id="form"><label>App name</label><input name="name" placeholder="my-app" required pattern="[a-z][a-z0-9-]{1,38}[a-z0-9]"><label>Source type</label><select name="source_type" id="sourceType"><option value="image">Approved image</option><option value="git">Git repository with Dockerfile</option></select><label>Image or HTTPS repository</label><select name="image" id="image"></select><input name="git" id="git" placeholder="https://github.com/user/repo.git" hidden><div class="row"><div><label>RAM (MiB)</label><input type="number" name="memory_mb" value="256" min="32" max="16384"></div><div><label>CPU cores</label><input type="number" name="cpus" value="0.5" min="0.1" max="4" step="0.1"></div></div><div class="row"><div><label>Persistent storage (GiB)</label><input type="number" name="storage_gb" value="1" min="0" max="500"></div><div><label>Container port</label><input type="number" name="container_port" value="80" min="1" max="65535" placeholder="Detected from Dockerfile EXPOSE"></div></div><p id="portHint" class="muted">Approved images use their configured port.</p><label>Visibility</label><select name="visibility"><option value="internal">Internal · Tailscale</option><option value="public">Public · requires ingress</option></select><label>Domain/subdomain (optional)</label><input name="domain" placeholder="app.example.com"><label>Fixed internal IP (advanced, optional)</label><input name="ipv4" placeholder="172.30.x.x"><label>Environment · one KEY=value per line</label><textarea name="environment" rows="3"></textarea><label>Secrets · one KEY=value per line</label><textarea name="secrets" rows="3"></textarea><p id="notice" class="muted"></p><button class="primary" type="submit">Build, check & deploy</button></form></section><section class="card events"><h2>Recent activity</h2><div id="events"></div></section></div></main><dialog id="logs"><div class="app-head"><h2 id="logTitle">Logs</h2><button onclick="logs.close()">Close</button></div><pre id="logText"></pre></dialog><dialog id="terminalDialog"><div class="app-head"><h2 id="terminalTitle">Container terminal</h2><button onclick="closeTerminal()">Close</button></div><div id="terminalStatus" class="terminal-status">Disconnected</div><pre id="terminalOutput" class="terminal-output"></pre><form id="terminalForm" class="terminal-form"><input id="terminalInput" autocomplete="off" spellcheck="false" aria-label="Terminal command" placeholder="Enter a shell command"><button class="primary" type="submit">Send</button><button type="button" onclick="interruptTerminal()">Ctrl-C</button></form></dialog><script>
const metricSkeleton=[['Host memory','Reading host memory…'],['Assigned RAM','Reading app limits…'],['Storage','Reading main storage…'],['System load · 1m / 5m / 15m','Reading CPU load…']];metrics.innerHTML=metricSkeleton.map(x=>`<section class="card metric"><span class="muted">${x[0]}</span><b class="loading-value">▮</b><div class="muted" style="min-height:21px">${x[1]}</div><div class="bar"><i class="loading-bar"></i></div></section>`).join('');
const B="''' + BASE + r'''/api"; const fmt=n=>{const u=['B','KiB','MiB','GiB','TiB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return `${n.toFixed(i?1:0)} ${u[i]}`};
let pendingButton=null,pendingButtonTimer=0;function acknowledgeButton(button){if(!button||button.disabled)return;pendingButton=button;button.classList.add('ack');clearTimeout(pendingButtonTimer);pendingButtonTimer=setTimeout(()=>{button.classList.remove('ack');if(pendingButton===button)pendingButton=null},650)}function beginButtonRequest(){const button=pendingButton;pendingButton=null;if(!button)return null;clearTimeout(pendingButtonTimer);button.classList.remove('ack');button.classList.add('busy');button.disabled=true;button.setAttribute('aria-busy','true');return button}function endButtonRequest(button){if(!button)return;button.classList.remove('busy');button.disabled=false;button.removeAttribute('aria-busy')}document.addEventListener('click',event=>{const button=event.target.closest('button');if(button)acknowledgeButton(button)},true);document.addEventListener('submit',event=>{if(event.submitter)acknowledgeButton(event.submitter)},true);
async function api(path,opt={}){const button=beginButtonRequest();opt.headers={...(opt.headers||{}),'X-Requested-With':'launchpad'};if(opt.body)opt.headers['Content-Type']='application/json';try{const r=await fetch(B+path,opt);const j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}finally{endButtonRequest(button)}}
function pairs(s){const o={};for(const line of s.split('\n')){if(!line.trim())continue;const i=line.indexOf('=');if(i<1)throw Error('Use KEY=value lines');o[line.slice(0,i).trim()]=line.slice(i+1)}return o}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function loadState(load,cores){const ratio=load/cores;if(ratio<.25)return 'idle';if(ratio<.7)return 'normal';if(ratio<1)return 'busy';if(ratio<1.25)return 'saturated';return 'work queued'}
function externalHtml(items){if(!items.length)return '<h2 style="margin-top:24px">Externally managed applications</h2><p class="muted">None currently detected.</p>';return '<h2 style="margin-top:24px">Externally managed applications</h2><p class="muted">Discovered automatically. Lifecycle controls stay with their owner or Compose project.</p>'+items.map(a=>{const nets=a.networks.map(n=>`${esc(n.network)}: ${esc(n.ipv4||n.ipv6||'—')}`).join(' · ');const limits=[a.memory_mb?a.memory_mb+' MiB':'RAM unlimited',a.cpus?a.cpus+' CPU':'CPU unlimited',a.stats.memory||'—'].join(' · ');return `<div class="app"><div class="app-head"><div><b>${esc(a.name)}</b> <span class="pill ${esc(a.state)}">${esc(a.state)}</span> <span class="pill">external</span><div class="muted">${esc(a.image)} · project ${esc(a.project)}${a.service?' / '+esc(a.service):''}</div><div class="muted">${limits}</div><div class="muted">${nets}</div></div><button onclick="showExternalLogs('${a.id}')">Logs</button></div></div>`}).join('')}
const ipInput=form.elements.ipv4;ipInput.previousElementSibling.textContent='Fixed container IP (advanced, optional)';ipInput.placeholder='Automatically assigned (recommended)';const ipHint=document.createElement('p');ipHint.className='muted';ipHint.textContent='Private Docker-network address only. Public access is configured with Visibility and Domain.';ipInput.after(ipHint);
form.elements.visibility.options[0].textContent='Internal · Tailscale only';form.elements.visibility.options[1].textContent='Public · Cloudflare Tunnel';const domainInput=form.elements.domain;const domainHint=document.createElement('p');domainHint.className='muted';domainHint.textContent='Optional. Entering an approved domain automatically selects public tunnel routing; leaving it blank stays Tailscale-only.';domainInput.after(domainHint);domainInput.addEventListener('input',()=>{form.elements.visibility.value=domainInput.value.trim()?'public':'internal'});form.elements.visibility.addEventListener('change',()=>{if(form.elements.visibility.value==='internal'&&domainInput.value.trim())domainInput.value=''});
const gitPanel=document.createElement('div');gitPanel.id='gitPanel';gitPanel.hidden=true;git.after(gitPanel);gitPanel.innerHTML='<label>Branch</label><input name="git_ref" id="gitRef" value="main" list="gitBranches" placeholder="main"><datalist id="gitBranches"></datalist><div class="actions"><button type="button" onclick="prepareFormKey()">Prepare private-repo key</button><button type="button" onclick="loadFormBranches()">Load branches</button></div><pre id="formKeyOutput" hidden></pre><p class="muted">For a private GitHub repo: prepare the key, add it under Repository settings → Deploy keys without write access, then load branches.</p>';
function requireGitFields(){const name=form.name.value.trim(),source=git.value.trim();if(!name)throw Error('Enter the app name first.');if(!source)throw Error('Enter the HTTPS GitHub repository URL first.');return {name,source}}
async function prepareFormKey(){notice.textContent='Preparing per-app key…';formKeyOutput.hidden=true;try{const {name,source}=requireGitFields();const d=await api('/git/key/prepare',{method:'POST',body:JSON.stringify({name,source})});formKeyOutput.hidden=false;formKeyOutput.textContent=d.deploy_key.public_key+'\n\nFingerprint: '+d.deploy_key.fingerprint;notice.textContent='Add this public key to the GitHub repository as a read-only deploy key.'}catch(e){formKeyOutput.hidden=false;formKeyOutput.textContent=e.message;notice.textContent='Private-repository setup needs attention.'}}
async function loadFormBranches(){notice.textContent='Loading branches…';try{const d=await api('/git/branches',{method:'POST',body:JSON.stringify({name:form.name.value,source:git.value})});gitBranches.innerHTML=d.branches.map(b=>`<option value="${esc(b)}"></option>`).join('');if(d.branches.length&&!d.branches.includes(gitRef.value))gitRef.value=d.branches.includes('main')?'main':d.branches[0];notice.textContent=d.branches.length?`${d.branches.length} branches loaded.`:'No branches found.'}catch(e){notice.textContent=e.message}}
const gitManager=document.createElement('section');gitManager.className='card';gitManager.style.gridColumn='span 12';gitManager.innerHTML='<h2>Git application control</h2><div id="gitApps"><span class="muted">Loading Git applications…</span></div>';document.querySelector('.events').before(gitManager);
const gitDialog=document.createElement('dialog');gitDialog.innerHTML='<div class="app-head"><h2 id="gitDialogTitle">Git settings</h2><button onclick="gitDialog.close()">Close</button></div><div id="gitDialogStatus" class="muted"></div><label>Branch</label><select id="existingBranch"></select><div class="actions"><button class="primary" onclick="switchExistingBranch()">Switch & deploy</button><button onclick="checkExistingGit()">Check update</button><button onclick="keyAction(\'rotate\')">Rotate key</button><button class="danger" onclick="keyAction(\'revoke\')">Revoke local key</button></div><pre id="existingKey"></pre><p class="muted">Deploy keys are read-only. Rotating or revoking locally does not remove the old public key from GitHub; remove it in repository settings too.</p>';document.body.append(gitDialog);let selectedGitApp=null;
function updateText(s){if(s.error&&s.error!=='not checked yet')return 'check error · '+s.error;if(!s.checked_at)return 'not checked yet';return `${s.update_available?'update available':'up to date'} · checked ${new Date(s.checked_at*1000).toLocaleString()}`}
async function loadGitApps(){try{const d=await api('/git/apps');gitApps.innerHTML=d.apps.length?d.apps.map(a=>`<div class="app app-head"><div><b>${esc(a.name)}</b> <span class="pill">${esc(a.git_ref)}</span><div class="muted">${esc(a.source)} · ${esc(updateText(a.update))}</div></div><button onclick="openGit('${a.name}')">Git settings</button></div>`).join(''):'<span class="muted">No Git-based applications deployed.</span>'}catch(e){gitApps.textContent=e.message}}
async function openGit(name){gitDialogStatus.textContent='Loading branches…';gitDialog.showModal();try{const d=await api(`/apps/${name}/git`);selectedGitApp=d;gitDialogTitle.textContent=name+' · Git settings';gitDialogStatus.textContent=d.branch_error||updateText(d.update);existingBranch.innerHTML=(d.branches.length?d.branches:[d.branch]).map(b=>`<option value="${esc(b)}" ${b===d.branch?'selected':''}>${esc(b)}</option>`).join('');existingKey.textContent=d.deploy_key.exists?d.deploy_key.public_key+'\n\nFingerprint: '+d.deploy_key.fingerprint:'No deploy key prepared. Public HTTPS access is in use.'}catch(e){gitDialogStatus.textContent=e.message}}
async function switchExistingBranch(){if(!selectedGitApp)return;if(!confirm(`Switch ${selectedGitApp.name} to ${existingBranch.value} and deploy it?`))return;gitDialogStatus.textContent='Switching branch and rebuilding…';try{await api(`/apps/${selectedGitApp.name}/switch-branch`,{method:'POST',body:JSON.stringify({branch:existingBranch.value})});await openGit(selectedGitApp.name);await loadAll();await loadGitApps()}catch(e){gitDialogStatus.textContent=e.message}}
async function checkExistingGit(){if(!selectedGitApp)return;gitDialogStatus.textContent='Checking remote…';try{await api(`/apps/${selectedGitApp.name}/check-update`,{method:'POST',body:'{}'});await openGit(selectedGitApp.name);await loadGitApps()}catch(e){gitDialogStatus.textContent=e.message}}
async function keyAction(action){if(!selectedGitApp)return;if(action==='revoke'&&!confirm('Destroy the local private deploy key? Also remove its public key from GitHub.'))return;gitDialogStatus.textContent=action==='rotate'?'Generating replacement key…':'Revoking local key…';try{await api(`/git/key/${action}`,{method:'POST',body:JSON.stringify({name:selectedGitApp.name,source:selectedGitApp.source})});await openGit(selectedGitApp.name)}catch(e){gitDialogStatus.textContent=e.message}}
async function loadAll(){try{const d=await api('/overview');const m=d.system.memory,st=d.system.storage,c=d.system.cpu_count,l=+d.system.load[0];metrics.innerHTML=[['Host memory',fmt(m.used)+' / '+fmt(m.total),m.used/m.total,'Available memory included'],['Assigned RAM',d.system.assigned_memory_mb+' MiB',d.system.assigned_memory_mb/(m.total/1048576),'Limits assigned to Launchpad apps'],['Storage',fmt(st.used)+' / '+fmt(st.total),st.used/st.total,'Used on main application storage'],['System load · 1m / 5m / 15m',d.system.load.join(' · '),Math.min(1,l/c),`${loadState(l,c)} · ${c} CPU cores available`]].map(x=>`<section class="card metric"><span class="muted">${x[0]}</span><b>${x[1]}</b><div class="muted" style="min-height:21px">${x[3]}</div><div class="bar"><i style="width:${Math.round(x[2]*100)}%"></i></div></section>`).join('');const managed=d.apps.length?d.apps.map(a=>`<div class="app"><div class="app-head"><div><b>${esc(a.name)}</b> <span class="pill ${esc(a.state)}">${esc(a.state)}</span><div class="muted">${esc(a.source)} · ${a.memory_mb} MiB · ${a.cpus} CPU · ${esc(a.stats.memory||'—')}</div><a href="${esc(a.url)}" target="_blank" class="muted">${esc(a.url)}</a></div><div class="actions"><button onclick="act('${a.name}','start')">Start</button><button onclick="act('${a.name}','stop')">Stop</button><button onclick="act('${a.name}','restart')">Restart</button><button onclick="act('${a.name}','update')">Update</button><button onclick="openTerminal('${a.name}')" ${a.state==='running'?'':'disabled'}>Terminal</button><button onclick="showLogs('${a.name}')">Logs</button><button class="danger" onclick="removeApp('${a.name}')">Remove</button></div></div></div>`).join(''):'<p class="muted">No Launchpad-managed applications yet.</p>';apps.innerHTML=managed+externalHtml(d.external_apps||[]);events.innerHTML=d.events.map(e=>`<span class="pill">${new Date(e.created_at*1000).toLocaleString()} · ${esc(e.actor)} · ${esc(e.app||'platform')} · ${esc(e.action)}: ${esc(e.outcome)}</span>`).join(' ');image.innerHTML=d.approved_images.map(i=>`<option value="${esc(i.image)}" data-port="${i.port}">${esc(i.label)} · ${esc(i.image)}</option>`).join('');image.onchange=()=>{if(sourceType.value==='image')form.container_port.value=image.selectedOptions[0].dataset.port};image.onchange()}catch(e){notice.textContent=e.message}}
const HERMES_IMAGE='nousresearch/hermes-agent:v2026.8.27';const baseLoadAll=loadAll;loadAll=async function(){await baseLoadAll();for(const card of apps.querySelectorAll('.app')){if(!card.textContent.includes(HERMES_IMAGE))continue;const actions=card.querySelector('.actions'),name=card.querySelector('b')?.textContent;if(!actions||!name||actions.querySelector('.hermes-setup'))continue;const button=document.createElement('button');button.className='primary hermes-setup';button.textContent='Set up Hermes';button.onclick=()=>setupHermes(name);const terminal=[...actions.querySelectorAll('button')].find(item=>item.textContent==='Terminal');actions.insertBefore(button,terminal||null)}};function configureImageDefaults(){if(sourceType.value!=='image'||image.value!==HERMES_IMAGE)return;form.memory_mb.value=4096;form.cpus.value=2;form.storage_gb.value=25;form.container_port.value=8642;portHint.textContent='Persistent Hermes workspace. Deploy it, then click Set up Hermes once.'}image.addEventListener('change',configureImageDefaults);function setupHermes(name){openTerminal(name);const deadline=Date.now()+10000,timer=setInterval(()=>{if(terminalSocket?.readyState===WebSocket.OPEN){clearInterval(timer);const command='hermes setup && hermes gateway start';appendTerminal('$ '+command+'\n');terminalSocket.send(command+'\r')}else if(Date.now()>deadline)clearInterval(timer)},100)}
sourceType.onchange=()=>{const g=sourceType.value==='git';git.hidden=!g;gitPanel.hidden=!g;image.hidden=g;form.container_port.value=g?'':image.selectedOptions[0]?.dataset.port||80;portHint.textContent=g?'Leave blank to detect the web port from Dockerfile EXPOSE. Launchpad builds and startup-checks the container before replacing anything live.':'Approved images use their configured port.';configureImageDefaults()};form.onsubmit=async e=>{e.preventDefault();notice.textContent='Cloning, building and checking startup…';try{const f=new FormData(form),g=f.get('source_type')==='git';if(g)requireGitFields();const d=await api('/apps',{method:'POST',body:JSON.stringify({name:f.get('name'),source_type:f.get('source_type'),source:g?f.get('git'):f.get('image'),git_ref:g?f.get('git_ref'):'main',memory_mb:f.get('memory_mb'),cpus:f.get('cpus'),storage_gb:f.get('storage_gb'),visibility:f.get('visibility'),domain:f.get('domain'),ipv4:f.get('ipv4'),container_port:f.get('container_port'),environment:pairs(f.get('environment')),secrets:pairs(f.get('secrets'))})});const warning=(d.warnings||[]).join(' ');notice.textContent=`Deployed successfully on detected container port ${d.container_port}.${warning?' '+warning:''}`;form.reset();sourceType.onchange();await loadAll();await loadGitApps()}catch(e){notice.textContent=e.message}};
async function act(n,a){try{await api(`/apps/${n}/${a}`,{method:'POST'});await loadAll()}catch(e){alert(e.message)}}async function removeApp(n){if(!confirm(`Remove ${n}? Persistent data will be preserved.`))return;try{await api(`/apps/${n}`,{method:'DELETE'});await loadAll()}catch(e){alert(e.message)}}async function showLogs(n){try{const d=await api(`/apps/${n}/logs`);logTitle.textContent=n+' logs';logText.textContent=d.logs||'No logs';logs.showModal()}catch(e){alert(e.message)}}
async function showExternalLogs(id){try{const d=await api(`/external/${id}/logs`);logTitle.textContent=d.name+' logs (external)';logText.textContent=d.logs||'No logs';logs.showModal()}catch(e){alert(e.message)}}
let terminalSocket=null;function appendTerminal(value){terminalOutput.textContent=(terminalOutput.textContent+value).slice(-120000);terminalOutput.scrollTop=terminalOutput.scrollHeight}function openTerminal(name){closeTerminal(false);terminalTitle.textContent=name+' · terminal';terminalOutput.textContent='Connecting…\n';terminalStatus.textContent='Authenticating owner session…';terminalDialog.showModal();const scheme=location.protocol==='https:'?'wss':'ws';terminalSocket=new WebSocket(`${scheme}://${location.host}${B}/apps/${name}/terminal`);terminalSocket.onopen=()=>{terminalStatus.textContent='Connected · runs as the container configured user';terminalOutput.textContent='';terminalInput.focus()};terminalSocket.onmessage=event=>appendTerminal(event.data);terminalSocket.onerror=()=>{terminalStatus.textContent='Connection failed';appendTerminal('\nTerminal connection failed. Refresh your Authentik session and try again.\n')};terminalSocket.onclose=event=>{terminalStatus.textContent=`Disconnected${event.code&&event.code!==1000?' · code '+event.code:''}`;terminalSocket=null}}function closeTerminal(closeDialog=true){if(terminalSocket){terminalSocket.close(1000,'owner closed terminal');terminalSocket=null}if(closeDialog&&terminalDialog.open)terminalDialog.close()}function interruptTerminal(){if(terminalSocket?.readyState===WebSocket.OPEN)terminalSocket.send('\x03')}terminalForm.onsubmit=event=>{event.preventDefault();const command=terminalInput.value;if(!command||terminalSocket?.readyState!==WebSocket.OPEN)return;appendTerminal(command+'\n');terminalSocket.send(command+'\r');terminalInput.value=''};terminalDialog.addEventListener('cancel',event=>{event.preventDefault();closeTerminal()});
const assistant=document.createElement('section');assistant.className='card';assistant.style.gridColumn='span 12';assistant.innerHTML='<div class="app-head"><h2>Codex operator</h2><span id="aiStatus" class="pill">checking…</span></div><pre id="chatOutput">Ask about the server, a deployment, or what to do next. Codex runs read-only and cannot silently mutate Docker.</pre><form id="chatForm"><textarea id="chatInput" rows="3" placeholder="What can I safely deploy with 512 MiB?"></textarea><button class="primary">Ask Codex</button></form>';document.querySelector('.events').before(assistant);
async function loadAssistant(){try{const s=await api('/assistant/status');aiStatus.textContent=s.authenticated?s.version+' · signed in':s.version+' · sign-in required'}catch(e){aiStatus.textContent='assistant unavailable'}}chatForm.onsubmit=async e=>{e.preventDefault();const message=chatInput.value.trim();if(!message)return;chatOutput.textContent='Codex is thinking…';try{const d=await api('/assistant/chat',{method:'POST',body:JSON.stringify({message})});chatOutput.textContent=d.reply}catch(e){chatOutput.textContent=e.message}};document.querySelectorAll('input[type=number]').forEach(input=>{const wrap=document.createElement('div');wrap.className='stepper';input.before(wrap);wrap.append(input);const controls=document.createElement('span');controls.className='step-controls';for(const [label,dir] of [['▲',1],['▼',-1]]){const button=document.createElement('button');button.type='button';button.textContent=label;button.tabIndex=-1;button.setAttribute('aria-label',dir>0?'Increase':'Decrease');button.onclick=()=>{dir>0?input.stepUp():input.stepDown();input.dispatchEvent(new Event('input',{bubbles:true}))};controls.append(button)}wrap.append(controls)});sourceType.onchange();loadAll();loadGitApps();loadAssistant();setInterval(loadAll,15000);setInterval(loadGitApps,60000);setInterval(loadAssistant,60000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = "ShreyWSLaunchpad/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f'level=info component=launchpad client="{self.client_address[0]}" message="{fmt % args}"', flush=True)

    def send_json(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers(); self.wfile.write(payload)

    def send_html(self) -> None:
        payload = INDEX.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload))); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers(); self.wfile.write(payload)

    def actor(self) -> str:
        return self.headers.get("X-authentik-username", self.headers.get("X-authentik-email", "owner"))

    def require_mutation(self) -> None:
        if self.headers.get("X-Requested-With") != "launchpad":
            raise LaunchpadError("Invalid mutation request")
        groups = {item.strip() for item in self.headers.get("X-authentik-groups", "").split("|") if item.strip()}
        if OWNER_GROUP not in groups:
            raise LaunchpadError("Owner role required")

    def require_terminal(self) -> None:
        groups = {item.strip() for item in self.headers.get("X-authentik-groups", "").split("|") if item.strip()}
        if OWNER_GROUP not in groups:
            raise LaunchpadError("Owner role required")
        if not terminal_origin_allowed(self.headers.get("Origin", "")):
            raise LaunchpadError("Terminal WebSocket origin is not allowed")
        if self.headers.get("Upgrade", "").lower() != "websocket":
            raise LaunchpadError("WebSocket upgrade required")
        if "upgrade" not in self.headers.get("Connection", "").lower():
            raise LaunchpadError("Invalid WebSocket connection header")
        key = self.headers.get("Sec-WebSocket-Key", "")
        try:
            decoded = base64.b64decode(key, validate=True)
        except Exception as exc:
            raise LaunchpadError("Invalid WebSocket key") from exc
        if len(decoded) != 16:
            raise LaunchpadError("Invalid WebSocket key")

    def terminal_send(self, payload: bytes, lock: threading.Lock, opcode: int = 1) -> None:
        with lock:
            self.connection.sendall(websocket_frame(payload, opcode))

    def handle_terminal(self, name: str) -> None:
        self.require_terminal()
        name = validate_app_name(name)
        load_config(name)
        container = APP_PREFIX + name
        if not container_exists(name):
            raise LaunchpadError("Application container is missing")
        details = json.loads(docker(["inspect", container], timeout=20))[0]
        labels = details.get("Config", {}).get("Labels") or {}
        if labels.get(APP_LABEL) != name:
            raise LaunchpadError("Terminal access is limited to Launchpad-managed applications")
        if not details.get("State", {}).get("Running"):
            raise LaunchpadError("Start the application before opening a terminal")
        with TERMINAL_LOCK:
            if name in TERMINAL_SESSIONS:
                raise LaunchpadError("A terminal session is already active for this application")
            TERMINAL_SESSIONS.add(name)

        actor = self.actor()
        key = self.headers["Sec-WebSocket-Key"]
        try:
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", websocket_accept(key))
            self.end_headers()
        except Exception:
            with TERMINAL_LOCK:
                TERMINAL_SESSIONS.discard(name)
            raise

        audit(actor, name, "terminal-open", "success")
        master_fd = -1
        slave_fd = -1
        process: subprocess.Popen[bytes] | None = None
        send_lock = threading.Lock()
        stop = threading.Event()
        try:
            master_fd, slave_fd = pty.openpty()
            terminal_settings = termios.tcgetattr(slave_fd)
            terminal_settings[3] &= ~termios.ECHO
            termios.tcsetattr(slave_fd, termios.TCSANOW, terminal_settings)
            process = subprocess.Popen(
                ["docker", "exec", "-i", "-e", "TERM=dumb", container, "/bin/sh", "-i"],
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = -1

            def relay_output() -> None:
                try:
                    while not stop.is_set():
                        chunk = os.read(master_fd, 4096)
                        if not chunk:
                            break
                        self.terminal_send(chunk.decode("utf-8", "replace").encode(), send_lock, 1)
                except (ConnectionError, OSError):
                    pass
                finally:
                    stop.set()

            threading.Thread(target=relay_output, name=f"terminal-{name}", daemon=True).start()
            self.terminal_send(
                f"Connected to {container} as its configured user. Session limit: 30 minutes.\r\n".encode(),
                send_lock,
            )
            self.connection.settimeout(1)
            deadline = time.monotonic() + TERMINAL_SESSION_SECONDS
            while not stop.is_set() and time.monotonic() < deadline:
                try:
                    opcode, payload = websocket_receive(self.connection)
                except socket.timeout:
                    continue
                if opcode == 8:
                    break
                if opcode == 9:
                    self.terminal_send(payload, send_lock, 10)
                    continue
                if opcode not in {1, 2}:
                    continue
                os.write(master_fd, payload.replace(b"\r", b"\n"))
            if time.monotonic() >= deadline:
                self.terminal_send(b"\r\nSession expired after 30 minutes.\r\n", send_lock)
        except Exception as exc:
            try:
                self.terminal_send(f"\r\nTerminal error: {exc}\r\n".encode(), send_lock)
            except Exception:
                pass
        finally:
            stop.set()
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            if master_fd >= 0:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            if slave_fd >= 0:
                try:
                    os.close(slave_fd)
                except OSError:
                    pass
            with TERMINAL_LOCK:
                TERMINAL_SESSIONS.discard(name)
            audit(actor, name, "terminal-close", "success")
            try:
                self.terminal_send(b"", send_lock, 8)
            except Exception:
                pass

    def body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise LaunchpadError("Invalid body length")
        if length < 1 or length > 131072:
            raise LaunchpadError("Request body must be between 1 byte and 128 KiB")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise LaunchpadError("JSON body must be an object")
        return value

    def route_path(self) -> str:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == BASE: return "/"
        if path.startswith(BASE + "/"): return path[len(BASE):]
        return path

    def do_GET(self) -> None:
        try:
            path = self.route_path()
            if path == "/-/health": self.send_json(200, {"status":"ok"}); return
            if path == "/": self.send_html(); return
            if path == "/api/overview":
                self.send_json(200, {"system":system_summary(),"apps":app_rows(),"external_apps":external_rows(),"events":recent_events(),"approved_images":[{"image":k,**v} for k,v in APPROVED_IMAGES.items()]}); return
            if path == "/api/assistant/status":
                self.send_json(200, assistant_request("/status")); return
            if path == "/api/git/apps":
                with db() as conn:
                    items = [{**dict(row), "update":read_git_status(str(row["name"]))} for row in conn.execute("SELECT name,source,git_ref FROM apps WHERE source_type='git' ORDER BY name")]
                self.send_json(200, {"apps":items}); return
            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/terminal", path)
            if match:
                self.handle_terminal(match.group(1)); return
            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/git", path)
            if match:
                self.send_json(200, git_details(match.group(1))); return
            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/logs", path)
            if match:
                name = match.group(1); load_config(name)
                logs = docker(["logs", "--tail", "250", APP_PREFIX + name], timeout=20) if container_exists(name) else ""
                self.send_json(200, {"logs": logs[-100000:]}); return
            match = re.fullmatch(r"/api/external/([a-f0-9]{64})/logs", path)
            if match:
                container_id = match.group(1)
                details = json.loads(docker(["inspect", container_id], timeout=20))[0]
                labels = details.get("Config", {}).get("Labels") or {}
                project = labels.get("com.docker.compose.project", "manual")
                if labels.get(APP_LABEL) or labels.get("shreyws.workload") or project in PLATFORM_PROJECTS:
                    raise LaunchpadError("Container is not an external application")
                name = str(details.get("Name", "")).removeprefix("/")
                logs = docker(["logs", "--tail", "250", container_id], timeout=20)
                self.send_json(200, {"name": name, "logs": logs[-100000:]}); return
            self.send_json(404, {"error":"not found"})
        except Exception as exc: self.send_json(400, {"error":str(exc)})

    def do_POST(self) -> None:
        try:
            self.require_mutation(); path = self.route_path(); actor = self.actor()
            if path == "/api/apps":
                config = validate_payload(self.body()); result = deploy(config, actor); self.send_json(201,result); return
            if path == "/api/git/branches":
                body = self.body(); name = validate_app_name(body.get("name", "")); source = validate_git_source(body.get("source", ""))
                self.send_json(200, {"branches":list_git_branches(name, source)}); return
            match = re.fullmatch(r"/api/git/key/(prepare|rotate|revoke)", path)
            if match:
                body = self.body(); name = validate_app_name(body.get("name", "")); source = validate_git_source(body.get("source", ""))
                if urlparse(source).hostname != "github.com": raise LaunchpadError("Deploy keys currently support GitHub repositories")
                action = match.group(1)
                with LOCK:
                    if action == "revoke": revoke_deploy_key(name); result = deploy_key_info(name)
                    else: result = generate_deploy_key(name, rotate=action == "rotate")
                audit(actor, name, f"deploy-key-{action}", "success")
                self.send_json(200, {"deploy_key":result}); return
            if path == "/api/assistant/chat":
                body = self.body(); message = str(body.get("message", "")).strip()
                if not message or len(message) > 12000: raise LaunchpadError("Message must be 1-12000 characters")
                result = assistant_request("/chat", {"message":message})
                audit(actor, "assistant", "chat", "success")
                self.send_json(200, result); return
            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/(check-update|switch-branch)", path)
            if match:
                name, action = match.groups(); config = load_config(name)
                if config["source_type"] != "git": raise LaunchpadError("Application is not Git-based")
                if action == "check-update":
                    result = check_git_update(config); audit(actor,name,action,"success")
                else:
                    branch = validate_git_ref(self.body().get("branch", ""))
                    if branch not in list_git_branches(name, str(config["source"])): raise LaunchpadError("Branch does not exist on the remote")
                    config["git_ref"] = branch; deploy(config, actor, update=True); result = {"branch":branch,"status":"deployed"}
                    check_git_update(config)
                self.send_json(200, result); return
            match = re.fullmatch(r"/api/apps/([a-z0-9-]+)/(start|stop|restart|update)", path)
            if not match: self.send_json(404,{"error":"not found"}); return
            name, action = match.groups(); config = load_config(name)
            if action == "update": deploy(config, actor, update=True)
            else:
                if not container_exists(name): raise LaunchpadError("Container is missing; use Update to recreate it")
                docker([action, APP_PREFIX + name], timeout=60); audit(actor,name,action,"success")
            self.send_json(200,{"status":action})
        except Exception as exc: self.send_json(400,{"error":str(exc)})

    def do_DELETE(self) -> None:
        try:
            self.require_mutation(); match = re.fullmatch(r"/api/apps/([a-z0-9-]+)", self.route_path())
            if not match: self.send_json(404,{"error":"not found"}); return
            name = match.group(1); load_config(name)
            with LOCK:
                if container_exists(name): docker(["rm","-f",APP_PREFIX+name])
                network = NETWORK_PREFIX + name
                subprocess.run(["docker","network","disconnect",network,"shreyws-traefik"],capture_output=True,timeout=30)
                subprocess.run(["docker","network","rm",network],capture_output=True,timeout=30)
                with db() as conn: conn.execute("DELETE FROM apps WHERE name=?",(name,))
                revoke_deploy_key(name)
                git_status_path(name).unlink(missing_ok=True)
                audit(self.actor(),name,"remove","success","persistent data preserved; deploy key destroyed")
            self.send_json(200,{"status":"removed","data_preserved":True})
        except Exception as exc: self.send_json(400,{"error":str(exc)})


def main() -> None:
    os.umask(0o077); init_state()
    threading.Thread(target=git_check_loop, name="git-update-checker", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print('level=info component=launchpad message="service started" port=8080', flush=True)
    server.serve_forever()


if __name__ == "__main__": main()
