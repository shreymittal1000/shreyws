# Centralized Logging

ShreyWS uses Grafana Loki and Grafana Alloy for centralized, searchable logs.

The goal is incident investigation without exposing log storage publicly or letting logs grow without bounds.

## Architecture

```text
Docker container logs
selected journald units
  -> Grafana Alloy
  -> Loki
  -> Grafana
```

Loki and Alloy run in a dedicated Compose project:

```text
/srv/shreyws/infra/compose/logging
```

They join the existing internal Docker network:

```text
traefik_default
```

No Traefik routers or host ports are configured for Loki or Alloy. Grafana and Prometheus reach them over the internal Docker network.

## Services

Loki:

```text
grafana/loki:3.5.1
container: shreyws-loki
internal URL: http://loki:3100
```

Alloy:

```text
grafana/alloy:v1.9.1
container: shreyws-alloy
internal metrics URL: http://alloy:12345/metrics
```

## Storage

Loki stores data under:

```text
/srv/shreyws/services/loki
```

Alloy state is stored under:

```text
/srv/shreyws/services/alloy
```

Retention is configured in Loki:

```text
14 days
```

This is long enough for recent incident investigation and short enough to avoid unbounded growth on a home server.

Loki uses filesystem-backed single-node storage with the compactor retention path enabled. It does not use clustering, object storage, or public access.

## Collected Docker Logs

Alloy discovers Docker containers through the read-only Docker socket and filters to:

```text
shreyws-*
```

This intentionally excludes unrelated host containers that are not managed by this repository.

Expected ShreyWS containers include Traefik, Authentik, Grafana, Prometheus, Alertmanager, Homepage, cAdvisor, Node Exporter, Diun, Loki, Alloy, and the alert webhooks.

## Collected Journald Logs

Alloy collects only selected systemd units:

- `docker.service`
- `tailscaled.service`
- `smartmontools.service`
- `shreyws-backup.service`
- `shreyws-backup-check.service`
- `shreyws-smart-metrics.service`
- systemd manager messages, labelled as `systemd-manager`
- kernel messages, labelled as `kernel`

The full journal is not forwarded by default. This avoids unnecessary volume and limits the chance of ingesting unrelated sensitive host logs.

## Labels

Low-cardinality labels are used:

```text
host
source
service
container
compose_project
stream
systemd_unit
severity
```

High-cardinality values such as request IDs, session IDs, users, URLs with arbitrary query parameters, and full log messages are kept in the log body, not labels.

## Redaction

Alloy redacts common sensitive patterns before forwarding to Loki:

- `Authorization` header values
- `Cookie` header values
- Telegram Bot API URLs containing bot tokens
- key/value fields named `token`, `password`, `passwd`, `secret`, `passphrase`, or `api_key`
- JSON string fields with those same names

Redaction is best-effort. Services should still avoid logging secrets directly.

Traefik JSON access logs are enabled with all request headers dropped. `RequestPath` and `RequestAddr` are also dropped to avoid storing sensitive query strings or client source addresses. This keeps status code, router, service, method, timings, and other operational fields without collecting cookies, Authorization headers, or full URLs.

## Grafana Integration

Grafana provisions Loki as a datasource from:

```text
/srv/shreyws/infra/compose/grafana/provisioning/datasources/prometheus.yml
```

Datasource:

```text
name: Loki
uid: loki
url: http://loki:3100
```

Dashboard:

```text
ShreyWS Logs
uid: shreyws-logs
```

Dashboard file:

```text
/srv/shreyws/infra/compose/grafana/dashboards/shreyws-logs.json
```

## Useful LogQL Queries

Pilot logs:

```logql
{host="shreyws", service="pilot"}
```

Pilot errors:

```logql
{host="shreyws", service="pilot"} |~ "(?i)(error|exception|failed)"
```

All logs for a service:

```logql
{host="shreyws", service="grafana"}
```

Errors across containers:

```logql
{host="shreyws", source="docker"} |~ "(?i)(error|exception|failed|panic|critical)"
```

Traefik and Authentik failures:

```logql
{host="shreyws", service=~"traefik|server|worker|proxy"} |~ "(?i)(error|failed|denied|unauthorized|forbidden|provider|outpost)"
```

Traefik 4xx and 5xx responses:

```logql
{host="shreyws", service="traefik"} | json | DownstreamStatus >= 400
```

Backup failures:

```logql
{host="shreyws", source="journal", systemd_unit=~"shreyws-backup.service|shreyws-backup-check.service"} |~ "(?i)(fail|error|borg|check)"
```

SMART collector issues:

```logql
{host="shreyws", source="journal", systemd_unit=~"shreyws-smart-metrics.service|systemd-manager"} |~ "shreyws-smart-metrics"
```

Telegram webhook delivery:

```logql
{host="shreyws", service="telegram-alert-webhook"} |~ "(?i)(telegram|delivery|failed|succeeded)"
```

Loki or Alloy ingestion errors:

```logql
{host="shreyws", service=~"loki|alloy"} |~ "(?i)(error|failed|drop|discard|reject)"
```

## Monitoring

Prometheus scrapes:

- `loki:3100`
- `alloy:12345`

Alerts cover:

- Loki unavailable
- Alloy unavailable
- Loki discarded log entries
- Alloy dropped log entries

The existing container-down and target-down alerts also include Loki and Alloy.

## Operations

Start or update the logging stack:

```bash
cd /srv/shreyws/infra/compose/logging
docker compose up -d
```

Restart only Alloy:

```bash
cd /srv/shreyws/infra/compose/logging
docker compose up -d --no-deps alloy
```

Restart only Loki:

```bash
cd /srv/shreyws/infra/compose/logging
docker compose up -d --no-deps loki
```

Check health:

```bash
docker exec shreyws-loki wget -q -O - http://127.0.0.1:3100/ready
docker exec shreyws-alloy alloy --version
docker exec shreyws-prometheus wget -q -O - http://alloy:12345/-/ready
```

Query Loki directly from the Docker network:

```bash
docker exec shreyws-grafana wget -q -O - 'http://loki:3100/loki/api/v1/labels'
```

Check disk usage:

```bash
du -sh /srv/shreyws/services/loki
```

If Loki grows too quickly, first reduce noisy log sources or retention. Do not delete Loki files while Loki is running unless this is a deliberate recovery action.

## Backup Policy

Configuration and dashboards are backed up because they live under:

```text
/srv/shreyws/infra
```

Bulk Loki log data under `/srv/shreyws/services/loki` is intentionally not part of the Borg backup scope. Logs are operationally disposable, can contain sensitive diagnostic context, and can grow unpredictably.

## Troubleshooting

If Docker logs are missing, verify Alloy can access the read-only Docker socket, check `docker logs shreyws-alloy`, and query `{host="shreyws", source="docker"}`.

If journald logs are missing, verify `/var/log/journal`, `/run/log/journal`, and `/etc/machine-id` are mounted read-only, then check that the unit is included in `loki.source.journal`.

If Grafana cannot query Loki, verify `shreyws-loki` is running, Grafana is on the same Docker network, and the Loki datasource is provisioned.

If alerts fire for dropped or discarded entries, inspect Alloy and Loki logs, check for large single log lines, and reduce noisy sources before increasing limits.
