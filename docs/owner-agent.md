# Owner Agent Pilot Reference

Last updated: 2026-07-17

Status: decommissioned from live runtime on 2026-07-17.

The owner-agent pilot was a deliberately limited class-B workload. It validated the ShreyWS agent platform controls without deploying a general arbitrary-code execution platform. It is retained in Git as a reference implementation only; `/agent/` is no longer served by the live platform.

The future direction for ShreyWS is a multi-user Hermes provisioning platform with isolated agent instances for selected users.

Final state archive:

```text
/srv/shreyws/agents/archive/owner-agent-pilot-state-20260717-114527.tar.gz
```

Archive manifest:

```text
/srv/shreyws/agents/archive/owner-agent-pilot-state-20260717-114527.manifest.txt
```

Archive SHA-256:

```text
4e00a4e7246b1709916272893f83e69b681b60245ebb548bc1b0a9631ca1848b
```

## Use Case

The pilot provides a private owner-only notes and summarization workspace:

- store short notes in its own SQLite database,
- list recent notes,
- summarize user-supplied text with a deterministic mock model backend,
- reject requests that ask for shell, Docker, package installation, host filesystem, backup, Authentik secret, or Telegram-token access.

It does not browse the web, execute commands, run Python supplied by a user, install packages, control Docker, modify ShreyWS infrastructure, or access personal cloud accounts.

## Framework Selection

| Option | Decision | Reason |
| --- | --- | --- |
| Purpose-built stdlib Python service | Selected | Small, inspectable, no dependency install step, no built-in shell/browser/Docker tools, straightforward metrics and SQLite state. |
| Open WebUI or similar chat UI | Rejected for this pilot | Useful later for model interaction, but broader than the bounded notes/summarization goal and likely to introduce extra auth, plugin and model-management surface. |
| Hermes/OpenClaw-style agent framework | Rejected for this pilot | No existing deployment was present, and full agent frameworks require more proof that dangerous tools can be disabled. |

The reference implementation uses `python:3.13.5-alpine3.22` and runs `/app/owner_agent.py` from a read-only bind mount.

## Route And Authorization

Historical route:

```text
https://shreyws.tail1591fa.ts.net/agent/
```

When deployed, Traefik applied `authentik-forward-auth@docker`. The application also enforced owner authorization from the forwarded Authentik group header:

```text
X-authentik-groups must include shreyws-owners
```

Authentik sends group names as a pipe-separated header value, for example:

```text
authentik Admins|shreyws-owners
```

The owner-agent parses exact group names from the forwarded header and does not use substring matching.

The Authentik group `shreyws-owners` contains the owner account. This adds a second owner-only check on top of the existing domain-level forward-auth provider. It does not change the existing provider mode.

If redeployed for reference, browser validation requires an authenticated owner session. A non-owner authenticated user should receive HTTP 403 from the application after forward-auth succeeds.

## Model Backend And Secrets

Historical backend:

```text
mock
```

No model API key is required yet. If a real model API is enabled later:

- create a dedicated low-limit key for this workload only,
- store it under `/srv/shreyws/secrets/agents/owner-pilot`,
- mount it read-only,
- never put it in Compose labels, command arguments or Git,
- revoke and rotate it independently from platform credentials.

Do not reuse an admin, broad personal, GitHub, Gmail, Calendar, Authentik, Borg, Telegram or Tailscale credential.

## Deployment Layout

Compose project:

```text
/srv/shreyws/infra/compose/owner-agent
```

Container:

```text
shreyws-owner-agent
```

State:

```text
/srv/shreyws/services/agents/owner-pilot
```

Secrets placeholder:

```text
/srv/shreyws/secrets/agents/owner-pilot
```

Network:

```text
owner_agent_frontend
```

When the pilot was live, only `shreyws-owner-agent`, `shreyws-traefik` and `shreyws-prometheus` were attached to that network. The live `owner_agent_frontend` network is removed during decommissioning.

## Security Controls

Container controls:

- non-root user `1000:1000`,
- `read_only: true`,
- no privileged mode,
- no host networking,
- no host PID/IPC/user namespace sharing,
- no Docker socket,
- no devices,
- no broad host paths,
- `cap_drop: [ALL]`,
- `no-new-privileges:true`,
- tmpfs `/tmp` with `noexec,nosuid,nodev`,
- `pids_limit: 128`,
- `mem_limit: 512m`,
- `cpus: "0.50"`.

Allowed tools:

- SQLite note create/list/search behavior inside its own state directory,
- deterministic mock summarization of user-supplied text,
- internal health and Prometheus metrics.

Prohibited tools:

- shell commands,
- subprocess execution,
- package installation,
- SSH,
- Docker,
- browser automation,
- URL fetching,
- host filesystem access,
- ShreyWS infrastructure modification,
- Borg/Auth/Telegram/Tailscale credential access,
- other workload storage access.

The service uses pattern-based request denial for obvious dangerous requests. This is a practical guard, not a complete prompt-injection solution.

## Monitoring

Historical Prometheus job:

```text
job="owner-agent"
target="owner-agent:8080"
```

Metrics:

- `shreyws_owner_agent_up`
- `shreyws_owner_agent_persistent_state_present`
- `shreyws_owner_agent_requests_total`
- `shreyws_owner_agent_request_failures_total`
- `shreyws_owner_agent_model_calls_total`
- `shreyws_owner_agent_model_failures_total`
- `shreyws_owner_agent_denied_tool_attempts_total`
- `shreyws_owner_agent_notes_created_total`
- `shreyws_owner_agent_active_requests`

Alerts:

- `OwnerAgentUnavailable`
- `OwnerAgentMetricsMissing`
- `OwnerAgentPersistentStateUnavailable`
- `OwnerAgentHighFailureRate`
- `OwnerAgentDeniedToolAttempts`

Global cAdvisor alerts still cover container restart/resource issues.

The active Prometheus scrape job and owner-agent alert rules were removed during decommissioning.

## Logging

When deployed, Alloy ingested Docker logs for `shreyws-owner-agent` automatically because it discovers `shreyws-*` containers.

The service logs structured JSON metadata for requests and lifecycle events. It does not log full prompts, note contents, model outputs, Authorization headers, cookies or secrets by default.

Useful LogQL:

```logql
{host="shreyws", container="shreyws-owner-agent"}
```

```logql
{host="shreyws", container="shreyws-owner-agent"} |~ "(?i)(error|denied|failed|timeout)"
```

## Backup And Restore

Borg no longer includes the live state path directly:

```text
/srv/shreyws/services/agents/owner-pilot
```

The final state archive is stored under `/srv/shreyws/agents/archive`, which remains in Borg backup scope through the existing `/srv/shreyws/agents` include.

The former live state directory `/srv/shreyws/services/agents/owner-pilot` and empty secret directory `/srv/shreyws/secrets/agents/owner-pilot` were removed after the archive was verified.

The SQLite database was small and suitable for normal file backup during this pilot. For a future heavier database or concurrent write workload, add application-level dump/backup hooks.

Targeted restore test:

```bash
sudo install -d -m 700 /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS
sudo bash -c 'source /etc/shreyws-backup/borg.env; latest=$(borg list --last 1 --format "{archive}" "$BORG_REPO"); cd /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS; umask 077; borg extract --numeric-ids "$BORG_REPO::$latest" srv/shreyws/services/agents/owner-pilot'
sudo sqlite3 /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS/srv/shreyws/services/agents/owner-pilot/owner-agent.sqlite3 'PRAGMA integrity_check;'
```

Do not restore over a live state directory while the container is running.

## Operations

Redeploy for reference:

```bash
docker network create owner_agent_frontend
# Reattach Traefik and Prometheus to owner_agent_frontend if route/scrape testing is required.
cd /srv/shreyws/infra/compose/owner-agent
docker compose --profile reference up -d
```

Restart:

```bash
cd /srv/shreyws/infra/compose/owner-agent
docker compose restart owner-agent
```

Stop/start:

```bash
cd /srv/shreyws/infra/compose/owner-agent
docker compose stop owner-agent
docker compose start owner-agent
```

SQLite integrity check:

```bash
sudo sqlite3 /srv/shreyws/services/agents/owner-pilot/owner-agent.sqlite3 'PRAGMA integrity_check;'
```

## Update And Rollback

If redeployed, use the controlled update workflow:

```bash
/srv/shreyws/infra/scripts/shreyws-container-update --dry-run owner-agent
/srv/shreyws/infra/scripts/shreyws-container-update owner-agent
```

Rollback to the current pinned image by restoring the previous Compose file from Git or setting the previous image digest, then:

```bash
cd /srv/shreyws/infra/compose/owner-agent
docker compose --profile reference up -d
```

Do not downgrade across incompatible state formats without a tested restore copy.

## Decommissioning

1. Stop and remove the Compose project:

```bash
cd /srv/shreyws/infra/compose/owner-agent
docker compose down
```

2. Preserve or deliberately remove state only after an archive is verified:

```bash
tar -C /srv/shreyws/services/agents -czf /srv/shreyws/agents/archive/owner-agent-pilot-state-YYYYMMDD-HHMMSS.tar.gz owner-pilot
```

3. Remove monitoring scrape/rules and run `promtool check config`.

4. Remove the dedicated Docker network after Traefik and Prometheus no longer attach to it:

```bash
docker network rm owner_agent_frontend
```

## Residual Risks

- Docker network isolation does not enforce strict outbound internet/LAN denial without host firewall or egress proxy controls.
- Prompt-injection resistance is partial because the pilot uses pattern denial and a very small tool surface rather than a formal policy engine.
- The application-level group check depends on Authentik outpost headers being present and trustworthy behind Traefik.
- Guest/family access and command execution remain no-go.

## Go / No-Go

Owner-only pilot use: complete; decommissioned after proving platform integration.

Command execution: NO-GO until a separate sandbox and explicit allowlist are implemented and tested.

Friends/family access: NO-GO on the current Docker-only boundary.
