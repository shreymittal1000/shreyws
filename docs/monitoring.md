# ShreyWS Monitoring

ShreyWS monitoring is intentionally simple: Prometheus scrapes the existing exporters, Grafana provisions a single overview dashboard, and alert rules live in the infra repository.

## What Was Already Present

Before this change, ShreyWS already had:

- Prometheus
- Grafana
- Node Exporter
- cAdvisor
- Traefik
- HTTPS routing through Traefik

Existing metric coverage was strong for host and container basics:

- CPU, memory, swap, load, uptime, boot time: Node Exporter
- Filesystems and inode usage: Node Exporter
- Network throughput: Node Exporter
- Disk IO: Node Exporter
- Hardware temperature sensors exposed through hwmon: Node Exporter
- Container CPU, memory, filesystem, network, health state, start time, and OOM events: cAdvisor

## Gaps Found

- Prometheus self-scrape was down because Prometheus uses `/prometheus` as its route prefix but was scraped at `/metrics`.
- Traefik metrics were not enabled.
- Backup status was only available in Borg logs, not Prometheus.
- Grafana had no provisioned datasource or system overview dashboard in the repo.
- SMART disk health was not available through Prometheus. It is now exported by the root-run `shreyws-smart-metrics` textfile collector.
- Failed systemd services are not exported. The current containerized Node Exporter does not have systemd/DBus access.
- TLS certificate expiry is not currently exported by Traefik metrics.

## What Was Added

### Prometheus

Prometheus now loads alert rules from:

```text
/srv/shreyws/infra/compose/monitoring/prometheus/rules/*.yml
```

Prometheus scrape jobs now include:

- `prometheus`, scraped at `/prometheus/metrics`
- `node-exporter`
- `cadvisor`
- `traefik`
- `loki`
- `alloy`

### Centralized Logs

Grafana Loki and Grafana Alloy now provide searchable logs for ShreyWS Docker containers and selected journald units.

See:

```text
/srv/shreyws/infra/docs/logging.md
```

### Traefik Metrics

Traefik now exposes Prometheus metrics on an internal-only entrypoint:

```text
--entrypoints.metrics.address=:8082
--metrics.prometheus=true
--metrics.prometheus.entrypoint=metrics
```

Port `8082` is not published on the host. It is only reachable on the Docker network by Prometheus.

### Backup Metrics

Borg does not natively export Prometheus metrics in this setup. The lightest-weight solution added here is a small Alpine sidecar, `shreyws-backup-metrics`, that reads existing backup logs and writes Prometheus textfile metrics for Node Exporter.

Metrics emitted:

- `shreyws_backup_last_success_timestamp_seconds`
- `shreyws_backup_age_seconds`
- `shreyws_backup_duration_seconds`
- `shreyws_backup_last_success`
- `shreyws_backup_repository_check_success`
- `shreyws_backup_verify_data_success`
- `shreyws_backup_archive_bytes{type="original|compressed|deduplicated"}`
- `shreyws_reboot_required`

The sidecar does not read Borg secrets and does not modify backups.

### Grafana

Grafana provisioning was added:

```text
compose/grafana/provisioning/datasources/prometheus.yml
compose/grafana/provisioning/dashboards/shreyws.yml
compose/grafana/dashboards/shreyws-overview.json
```

The dashboard URL is:

```text
https://shreyws.tail1591fa.ts.net/grafana/d/shreyws-overview/shreyws-overview
```

## Dashboard

Dashboard created:

- `ShreyWS Overview`

Sections:

- System
- Storage
- Network
- Docker
- Traefik
- Backups

The dashboard is intentionally compact and uses only metrics available from the current stack.

## Remaining Limitations

- SMART disk health is exported by `/usr/local/sbin/shreyws-smart-metrics` into Node Exporter's textfile collector. It covers the system disk, `/srv` disk, and backup disk without exposing disk serial numbers.
- Failed systemd services are not monitored yet. Recommended next step: either enable Node Exporter's systemd collector with the required host mounts/DBus access, or emit a root-owned textfile metric from a systemd timer.
- Docker restart count is approximated with changes in `container_start_time_seconds`; Docker's exact restart counter is not exported by cAdvisor.
- Most containers do not define Docker healthchecks, so cAdvisor health state is limited.
- TLS certificate expiry is not currently exported.
- Grafana logs plugin background installer permission errors for bundled Elasticsearch/Zipkin plugins; dashboard and datasource provisioning still succeeds.

## Verification

Verified on 2026-07-08:

- Prometheus config is valid with `promtool check config`.
- Alert rules are valid with `promtool check rules`.
- Prometheus targets are all healthy: Prometheus, Node Exporter, cAdvisor, Traefik.
- Backup textfile metrics are generated and scraped.
- Traefik metrics are generated and scraped.
- Grafana datasource and dashboard provisioning completes.
- Grafana dashboard route redirects to login.
- Existing HTTPS service routes still respond.
