# ShreyWS Pilot Workload

Last updated: 2026-07-16

## Purpose

The ShreyWS pilot workload is a deliberately small internal HTTP service used to validate the full platform path:

```text
Docker Compose
  -> Traefik
  -> Authentik forward-auth
  -> pilot app
  -> Prometheus metrics
  -> Alloy/Loki logs
  -> Borg backup and restore
```

It is not a user-facing product and does not provide shell access, code execution, plugins, external integrations, signup, or privileged host access.

## Selection

Chosen workload: a purpose-built Python standard-library HTTP service with SQLite persistence.

Why this was selected:

- no external database,
- no extra credentials,
- no Docker socket,
- no broad host mounts,
- supports path-prefix routing under `/pilot/`,
- exposes `/-/health` and `/metrics`,
- persists a small SQLite file under `/srv/shreyws/services/pilot`,
- easy to back up, restore and remove,
- uses the already-present pinned image `python:3.13.5-alpine3.22`.

Rejected alternatives:

- Memos: useful notes app, but larger, more user-oriented, and introduces account/signup/admin concerns for a simple platform test.
- Uptime Kuma: useful monitoring app, but duplicates existing Prometheus/Alertmanager responsibilities and stores operational checks that are not needed for this pilot.

## Architecture

Compose project:

```text
/srv/shreyws/infra/compose/pilot
```

Container:

```text
shreyws-pilot
```

Image:

```text
python:3.13.5-alpine3.22
```

Internal port:

```text
8000/tcp
```

Public route:

```text
https://shreyws.tail1591fa.ts.net/pilot/
```

The service joins only:

```text
traefik_default
```

No host ports are published.

## Authentication

Traefik applies the existing middleware:

```text
authentik-forward-auth@docker
```

The middleware is attached directly to the `/pilot` router. There is no strip-prefix middleware because the app natively understands `PILOT_BASE_PATH=/pilot`.

Expected unauthenticated behavior:

```text
/pilot/
  -> Authentik authorize URL
  -> /outpost.goauthentik.io/callback
  -> /pilot/
```

Do not protect `/authentik/` or `/outpost.goauthentik.io/` behind this service.

## Persistence

Persistent data lives at:

```text
/srv/shreyws/services/pilot
```

The app writes:

```text
/srv/shreyws/services/pilot/pilot.db
/srv/shreyws/services/pilot/pilot.db-wal
/srv/shreyws/services/pilot/pilot.db-shm
```

SQLite uses WAL mode. For normal recovery, restore the directory while the container is stopped so the database and WAL files are consistent.

## Security Controls

The container uses:

- non-root UID/GID `1000:1000`,
- no Docker socket,
- no privileged mode,
- no host networking,
- no host ports,
- no broad host filesystem mounts,
- `read_only: true`,
- `tmpfs: /tmp`,
- `cap_drop: [ALL]`,
- `security_opt: no-new-privileges:true`,
- `pids_limit: 128`,
- `mem_limit: 128m`,
- `cpus: "0.25"`.

Residual access:

- any container on `traefik_default` can reach the internal HTTP port unless Docker network segmentation is improved later,
- Authentik is the browser-facing access control.

## Monitoring

Prometheus scrapes:

```text
job="pilot"
target="pilot:8000"
metrics_path="/metrics"
```

Metrics:

```text
shreyws_pilot_up
shreyws_pilot_uptime_seconds
shreyws_pilot_requests_total
shreyws_pilot_sqlite_present
```

Alerts:

- `PilotTargetDown`
- `PilotMetricsMissing`
- `PilotSqliteMissing`

The global cAdvisor alerts also cover health state, OOM events and restart loops for the container.

## Logging

Alloy discovers the container because its name starts with `shreyws-`.

Useful LogQL:

```logql
{host="shreyws", service="pilot"}
```

```logql
{host="shreyws", service="pilot"} |~ "(?i)(error|exception|failed)"
```

## Backup

Borg includes:

```text
/srv/shreyws/services/pilot
```

The app has no cache directories and no secrets. Stop the container before restoring over the live data path.

## Operations

Deploy:

```bash
cd /srv/shreyws/infra/compose/pilot
docker compose up -d
```

Restart:

```bash
cd /srv/shreyws/infra/compose/pilot
docker compose restart pilot
```

Stop/start:

```bash
cd /srv/shreyws/infra/compose/pilot
docker compose stop pilot
docker compose start pilot
```

Review an image update:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update /srv/shreyws/infra/compose/pilot --dry-run
```

Apply a reviewed update:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update /srv/shreyws/infra/compose/pilot
```

Rollback a recorded update state:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update --rollback /srv/shreyws/infra/logs/container-updates/<timestamp>-pilot
```

Rollback configuration:

```bash
cd /srv/shreyws/infra
git revert <commit>
cd compose/pilot
docker compose up -d
```

Restore persistent data:

```bash
cd /srv/shreyws/infra/compose/pilot
docker compose stop pilot
sudo borg extract /srv/shreyws/backups/borg::<archive> srv/shreyws/services/pilot
docker compose start pilot
```

Remove:

```bash
cd /srv/shreyws/infra/compose/pilot
docker compose down
```

Data removal is intentionally separate:

```bash
sudo rm -rf /srv/shreyws/services/pilot
```

## Lessons

This pilot proves the platform can host a small stateful service through the established ShreyWS path without weakening Authentik, exposing ports, or adding privileged mounts. It does not prove readiness for untrusted users or arbitrary code execution.
