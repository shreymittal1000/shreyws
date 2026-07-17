#!/usr/bin/env python3
"""ShreyWS agent instance provisioner.

This is intentionally small and dependency-free. It owns the platform layer:
manifest validation, deterministic plans, generated Compose metadata, storage
and secret directory registration, and safe lifecycle commands.

Runtime-specific details belong in adapters. The Hermes adapter is currently a
placeholder and refuses to start unless a real, pinned image/port are provided
in a future task.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any


API_VERSION = "shreyws.io/v1alpha1"
KIND = "AgentInstance"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,61}[a-z0-9]$")
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,126}[A-Za-z0-9]$")
PATH_RE = re.compile(r"^/agents/[a-z0-9][a-z0-9-]*/$")
MEMORY_RE = re.compile(r"^[1-9][0-9]*(MiB|M|GiB|G)$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


class AgentctlError(Exception):
    pass


def die(message: str) -> None:
    raise AgentctlError(message)


def strip_comment(line: str) -> str:
    in_quote = False
    quote = ""
    for idx, char in enumerate(line):
        if char in ("'", '"'):
            if not in_quote:
                in_quote = True
                quote = char
            elif quote == char:
                in_quote = False
        if char == "#" and not in_quote:
            return line[:idx].rstrip()
    return line.rstrip()


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return ""
    if value in ("true", "false"):
        return value == "true"
    if value == "null":
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", value):
        return float(value)
    return value


def parse_manifest_text(text: str) -> dict[str, Any]:
    """Parse a strict YAML subset: mappings, scalar values and scalar lists."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending: tuple[int, dict[str, Any], str] | None = None

    for lineno, original in enumerate(text.splitlines(), start=1):
        if "\t" in original:
            die(f"line {lineno}: tabs are not allowed")
        line = strip_comment(original)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2 != 0:
            die(f"line {lineno}: indentation must use multiples of two spaces")
        content = line.strip()

        if content.startswith("- "):
            if pending is None or pending[0] != indent:
                die(f"line {lineno}: list item without matching key")
            _pending_indent, parent, key = pending
            if key not in parent:
                parent[key] = []
            if isinstance(parent[key], dict) and not parent[key]:
                parent[key] = []
            if not isinstance(parent[key], list):
                die(f"line {lineno}: mixed list and mapping values are not supported")
            item = content[2:].strip()
            if ":" in item:
                die(f"line {lineno}: list items must be scalar values")
            parent[key].append(parse_scalar(item))
            continue

        pending = None
        if ":" not in content:
            die(f"line {lineno}: expected key: value")
        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            die(f"line {lineno}: empty key")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack or not isinstance(stack[-1][1], dict):
            die(f"line {lineno}: invalid nesting")
        parent = stack[-1][1]
        if key in parent:
            die(f"line {lineno}: duplicate key {key}")

        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            pending = (indent + 2, parent, key)
        else:
            parent[key] = parse_scalar(value)

    return root


def load_manifest(path: Path) -> dict[str, Any]:
    return parse_manifest_text(path.read_text(encoding="utf-8"))


def require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        die(f"{path} must be a mapping")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        die(f"{path} must be a list")
    return value


def require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        die(f"{path} must be true or false")
    return value


def require_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        die(f"{path} must be a non-empty string")
    return value


def approved_child(root: Path, child: str, field: str) -> Path:
    path = Path(child)
    if not path.is_absolute():
        die(f"{field} must be an absolute path")
    resolved_root = root.resolve()
    resolved_child = path.resolve()
    try:
        resolved_child.relative_to(resolved_root)
    except ValueError:
        die(f"{field} must stay under {resolved_root}")
    return resolved_child


def validate_manifest(manifest: dict[str, Any], services_root: Path, secrets_root: Path) -> dict[str, Any]:
    if manifest.get("apiVersion") != API_VERSION:
        die(f"apiVersion must be {API_VERSION}")
    if manifest.get("kind") != KIND:
        die(f"kind must be {KIND}")

    metadata = require_dict(manifest.get("metadata"), "metadata")
    spec = require_dict(manifest.get("spec"), "spec")
    name = require_str(metadata.get("name"), "metadata.name")
    if not NAME_RE.fullmatch(name):
        die("metadata.name must be lowercase DNS-style: letters, digits and hyphens")

    owner = require_dict(spec.get("owner"), "spec.owner")
    username = require_str(owner.get("authentikUsername"), "spec.owner.authentikUsername")
    groups = [require_str(g, "spec.owner.authentikGroups[]") for g in require_list(owner.get("authentikGroups"), "spec.owner.authentikGroups")]
    if not groups:
        die("spec.owner.authentikGroups must contain at least one group")
    for group in groups:
        if not GROUP_RE.fullmatch(group):
            die(f"invalid Authentik group name: {group}")

    runtime = require_dict(spec.get("runtime"), "spec.runtime")
    runtime_type = require_str(runtime.get("type"), "spec.runtime.type")
    runtime_version = require_str(runtime.get("version"), "spec.runtime.version")
    if runtime_type not in ("hermes", "placeholder"):
        die("spec.runtime.type must be hermes or placeholder")
    if not VERSION_RE.fullmatch(runtime_version):
        die("spec.runtime.version must be a pinned runtime version string")
    runtime_enabled = bool(runtime.get("enabled", False))
    image = runtime.get("image")
    port = runtime.get("port")
    if runtime_enabled:
        if runtime_type == "hermes":
            die("Hermes runtime start is disabled until official image, port and health contract are documented")
        if not isinstance(image, str) or ":" not in image or image.endswith(":latest"):
            die("enabled runtimes require a pinned non-latest image")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            die("enabled runtimes require a valid integer port")

    interfaces = require_dict(spec.get("interfaces"), "spec.interfaces")
    web = require_dict(interfaces.get("web"), "spec.interfaces.web")
    web_enabled = require_bool(web.get("enabled"), "spec.interfaces.web.enabled")
    web_path = require_str(web.get("path"), "spec.interfaces.web.path")
    if web_enabled and not PATH_RE.fullmatch(web_path):
        die("spec.interfaces.web.path must look like /agents/name/")
    telegram = require_dict(interfaces.get("telegram"), "spec.interfaces.telegram")
    require_bool(telegram.get("enabled"), "spec.interfaces.telegram.enabled")

    storage = require_dict(spec.get("storage"), "spec.storage")
    data_path = approved_child(services_root, require_str(storage.get("dataPath"), "spec.storage.dataPath"), "spec.storage.dataPath")
    backup = require_bool(storage.get("backup"), "spec.storage.backup")
    secret_path = approved_child(secrets_root, require_str(storage.get("secretPath"), "spec.storage.secretPath"), "spec.storage.secretPath")

    resources = require_dict(spec.get("resources"), "spec.resources")
    cpus = resources.get("cpus")
    if not isinstance(cpus, (int, float)) or float(cpus) <= 0 or float(cpus) > 4:
        die("spec.resources.cpus must be >0 and <=4")
    memory = require_str(resources.get("memory"), "spec.resources.memory")
    if not MEMORY_RE.fullmatch(memory):
        die("spec.resources.memory must use M, MiB, G or GiB")
    pids = resources.get("pids")
    if not isinstance(pids, int) or pids < 32 or pids > 2048:
        die("spec.resources.pids must be an integer between 32 and 2048")

    security = require_dict(spec.get("security"), "spec.security")
    required_false = ("commandExecution", "dockerSocket", "hostNetwork", "privileged", "hostFilesystem")
    for key in required_false:
        if require_bool(security.get(key), f"spec.security.{key}") is not False:
            die(f"spec.security.{key} must be false")

    return {
        "name": name,
        "username": username,
        "groups": groups,
        "runtime_type": runtime_type,
        "runtime_version": runtime_version,
        "runtime_enabled": runtime_enabled,
        "image": image,
        "port": port,
        "web_enabled": web_enabled,
        "web_path": web_path,
        "data_path": str(data_path),
        "secret_path": str(secret_path),
        "backup": backup,
        "cpus": str(cpus),
        "memory": memory,
        "pids": pids,
    }


def instance_dir(infra_root: Path, name: str) -> Path:
    return infra_root / "platform" / "instances" / name


def deterministic_plan(config: dict[str, Any], infra_root: Path) -> dict[str, Any]:
    name = config["name"]
    frontend_network = f"agent_{name}_frontend"
    service_name = f"agent-{name}"
    container_name = f"shreyws-agent-{name}"
    inst_dir = instance_dir(infra_root, name)
    return {
        "instance": name,
        "runtime": {
            "type": config["runtime_type"],
            "version": config["runtime_version"],
            "enabled": config["runtime_enabled"],
            "image": config["image"],
            "port": config["port"],
        },
        "paths": {
            "instanceDir": str(inst_dir),
            "composeFile": str(inst_dir / "compose.yaml"),
            "metadataFile": str(inst_dir / "metadata.json"),
            "dataPath": config["data_path"],
            "secretPath": config["secret_path"],
        },
        "auth": {
            "authentikUsername": config["username"],
            "requiredGroups": config["groups"],
            "matching": "exact",
        },
        "network": {
            "frontend": frontend_network,
            "publishedPorts": [],
        },
        "service": {
            "name": service_name,
            "container": container_name,
        },
        "route": {
            "enabled": config["web_enabled"],
            "path": config["web_path"],
            "middleware": "authentik-forward-auth@docker",
        },
        "backup": {
            "enabled": config["backup"],
            "registration": str(inst_dir / "backup-registration.json"),
        },
        "security": {
            "dockerSocket": False,
            "privileged": False,
            "hostNetwork": False,
            "commandExecution": False,
            "hostFilesystem": False,
        },
        "resources": {
            "cpus": config["cpus"],
            "memory": config["memory"],
            "pids": config["pids"],
        },
    }


def compose_text(plan: dict[str, Any]) -> str:
    runtime = plan["runtime"]
    profile = "runtime" if runtime["enabled"] else "reference"
    image = runtime["image"] or "registry.example.invalid/hermes:PINNED_VERSION_REQUIRED"
    port = runtime["port"] or 8080
    route = plan["route"]["path"].rstrip("/")
    required_group = plan["auth"]["requiredGroups"][0]
    return f"""# Generated by shreyws-agentctl. Do not edit by hand.
# Runtime starts only when the manifest enables it and a real adapter image/port
# are supplied. The current Hermes example is intentionally reference-only.
services:
  {plan['service']['name']}:
    profiles:
      - {profile}
    image: {image}
    container_name: {plan['service']['container']}
    restart: unless-stopped
    read_only: true
    mem_limit: {plan['resources']['memory']}
    cpus: \"{plan['resources']['cpus']}\"
    pids_limit: {plan['resources']['pids']}
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    networks:
      - frontend
    environment:
      SHREYWS_AGENT_INSTANCE: {plan['instance']}
      SHREYWS_AGENT_RUNTIME: {runtime['type']}
      SHREYWS_AGENT_REQUIRED_GROUP: {required_group}
      SHREYWS_AGENT_BASE_PATH: {route}
      SHREYWS_COMMAND_EXECUTION: \"false\"
    tmpfs:
      - /tmp:size=256m,noexec,nosuid,nodev
    volumes:
      - {plan['paths']['dataPath']}:/data
      - {plan['paths']['secretPath']}:/run/secrets/agent:ro
    healthcheck:
      test: [\"CMD\", \"wget\", \"-q\", \"--spider\", \"http://127.0.0.1:{port}/-/health\"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    labels:
      - traefik.enable=true
      - traefik.docker.network={plan['network']['frontend']}
      - traefik.http.routers.{plan['service']['name']}.tls=true
      - traefik.http.routers.{plan['service']['name']}.rule=Host(`shreyws.tail1591fa.ts.net`) && PathPrefix(`{route}`)
      - traefik.http.routers.{plan['service']['name']}.entrypoints=websecure
      - traefik.http.routers.{plan['service']['name']}.middlewares=authentik-forward-auth@docker
      - traefik.http.services.{plan['service']['name']}.loadbalancer.server.port={port}
      - diun.enable=true
      - shreyws.agent.instance={plan['instance']}
      - shreyws.agent.runtime={runtime['type']}
      - shreyws.trust_class=agent-instance

networks:
  frontend:
    name: {plan['network']['frontend']}
    external: true
"""


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_instance(plan: dict[str, Any], manifest_path: Path, dry_run: bool) -> None:
    paths = plan["paths"]
    inst_dir = Path(paths["instanceDir"])
    if dry_run:
        print(json.dumps({"dryRun": True, "wouldCreate": plan}, indent=2, sort_keys=True))
        return
    inst_dir.mkdir(parents=True, exist_ok=True)
    Path(paths["dataPath"]).mkdir(parents=True, exist_ok=True)
    Path(paths["secretPath"]).mkdir(parents=True, exist_ok=True)
    os.chmod(paths["secretPath"], 0o750)
    shutil.copy2(manifest_path, inst_dir / "manifest.yaml")
    (inst_dir / "compose.yaml").write_text(compose_text(plan), encoding="utf-8")
    write_json(inst_dir / "metadata.json", plan)
    write_json(inst_dir / "backup-registration.json", {"instance": plan["instance"], "path": paths["dataPath"], "backup": plan["backup"]["enabled"]})
    (inst_dir / "README.md").write_text(
        f"# Agent Instance: {plan['instance']}\n\nGenerated by `shreyws-agentctl`.\n\nRuntime enabled: `{plan['runtime']['enabled']}`\n",
        encoding="utf-8",
    )
    print(f"created {plan['instance']} at {inst_dir}")


def compose_cmd(plan: dict[str, Any], args: list[str], dry_run: bool) -> None:
    compose_file = Path(plan["paths"]["composeFile"])
    if not compose_file.exists():
        die(f"compose file does not exist: {compose_file}")
    if dry_run:
        print("dry-run:", "docker compose", " ".join(args), "in", compose_file.parent)
        return
    subprocess.run(["docker", "compose", *args], cwd=compose_file.parent, check=True)


def start_instance(plan: dict[str, Any], dry_run: bool) -> None:
    if not plan["runtime"]["enabled"]:
        die("runtime is disabled/reference-only; refusing to start")
    subprocess.run(["docker", "network", "inspect", plan["network"]["frontend"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not dry_run:
        subprocess.run(["docker", "network", "create", plan["network"]["frontend"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    compose_cmd(plan, ["--profile", "runtime", "up", "-d"], dry_run)


def stop_instance(plan: dict[str, Any], dry_run: bool) -> None:
    compose_cmd(plan, ["--profile", "runtime", "stop"], dry_run)


def status_instance(plan: dict[str, Any]) -> None:
    container = plan["service"]["container"]
    result = subprocess.run(["docker", "ps", "-a", "--filter", f"name={container}", "--format", "{{.Names}}\t{{.Status}}"], text=True, capture_output=True, check=False)
    status = result.stdout.strip() or "not-created"
    print(json.dumps({"instance": plan["instance"], "container": container, "status": status, "runtimeEnabled": plan["runtime"]["enabled"]}, indent=2, sort_keys=True))


def archive_state(plan: dict[str, Any], infra_root: Path) -> Path:
    archive_dir = infra_root.parent / "agents" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    archive = archive_dir / f"{plan['instance']}-state-{ts}.tar.gz"
    data_path = Path(plan["paths"]["dataPath"])
    if not data_path.exists():
        die(f"state path does not exist: {data_path}")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(data_path, arcname=data_path.name)
    return archive


def destroy_instance(plan: dict[str, Any], infra_root: Path, dry_run: bool, archive: bool, delete_state: bool, yes: bool) -> None:
    if not archive and not yes:
        die("destroy requires --archive-state or --yes to confirm state preservation decision")
    if dry_run:
        print(json.dumps({"dryRun": True, "wouldDestroy": plan["instance"], "deleteState": delete_state, "archiveState": archive}, indent=2, sort_keys=True))
        return
    if archive:
        archived = archive_state(plan, infra_root)
        print(f"archived state to {archived}")
    compose_file = Path(plan["paths"]["composeFile"])
    if compose_file.exists():
        subprocess.run(
            ["docker", "compose", "--profile", "runtime", "down"],
            cwd=compose_file.parent,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    shutil.rmtree(Path(plan["paths"]["instanceDir"]), ignore_errors=True)
    if delete_state:
        shutil.rmtree(Path(plan["paths"]["dataPath"]), ignore_errors=True)
        shutil.rmtree(Path(plan["paths"]["secretPath"]), ignore_errors=True)
    print(f"destroyed generated files for {plan['instance']}; deleteState={delete_state}")


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    manifest_path = Path(args.manifest).resolve()
    manifest = load_manifest(manifest_path)
    config = validate_manifest(manifest, Path(args.services_root), Path(args.secrets_root))
    return deterministic_plan(config, Path(args.infra_root)), manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shreyws-agentctl")
    parser.add_argument("--infra-root", default=os.environ.get("SHREYWS_INFRA_ROOT", "/srv/shreyws/infra"))
    parser.add_argument("--services-root", default=os.environ.get("SHREYWS_SERVICES_ROOT", "/srv/shreyws/services/agents"))
    parser.add_argument("--secrets-root", default=os.environ.get("SHREYWS_SECRETS_ROOT", "/srv/shreyws/secrets/agents"))
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "plan", "create", "start", "stop", "status"):
        sp = sub.add_parser(command)
        sp.add_argument("manifest")
    destroy = sub.add_parser("destroy")
    destroy.add_argument("manifest")
    destroy.add_argument("--archive-state", action="store_true")
    destroy.add_argument("--delete-state", action="store_true")
    destroy.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    try:
        plan, manifest_path = build_plan(args)
        if args.command == "validate":
            print(f"valid {plan['instance']}")
        elif args.command == "plan":
            print(json.dumps(plan, indent=2, sort_keys=True))
        elif args.command == "create":
            create_instance(plan, manifest_path, args.dry_run)
        elif args.command == "start":
            start_instance(plan, args.dry_run)
        elif args.command == "stop":
            stop_instance(plan, args.dry_run)
        elif args.command == "status":
            status_instance(plan)
        elif args.command == "destroy":
            destroy_instance(plan, Path(args.infra_root), args.dry_run, args.archive_state, args.delete_state, args.yes)
        return 0
    except AgentctlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: command failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
