# Alerting

ShreyWS uses Prometheus alert rules and Alertmanager for local, low-noise infrastructure alerting.

The goal is practical operational signal, not enterprise-scale paging.

## Architecture

```text
Prometheus
  -> evaluates source-controlled rules
  -> sends firing/resolved alerts to Alertmanager
  -> Alertmanager groups, deduplicates and inhibits alerts
  -> local webhook writes JSON lines to disk
```

Alertmanager and the alert-log webhook are internal-only Docker Compose services in:

```text
/srv/shreyws/infra/compose/monitoring
```

They have no Traefik routers and no published host ports.

Local alert log:

```text
/srv/shreyws/logs/alertmanager/alerts.jsonl
```

View recent notifications:

```bash
tail -n 50 /srv/shreyws/logs/alertmanager/alerts.jsonl
```

## Services

Alertmanager:

```text
prom/alertmanager:v0.28.1
```

Alert log webhook:

```text
python:3.13.5-alpine3.22
```

Both use fixed image tags.

## Rule Files

Rules live in:

```text
/srv/shreyws/infra/compose/monitoring/prometheus/rules/shreyws-alerts.yml
```

Prometheus configuration:

```text
/srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml
```

Alertmanager configuration:

```text
/srv/shreyws/infra/compose/monitoring/alertmanager/alertmanager.yml
```

## Thresholds

Backups:

- `BackupJobFailed`: latest Borg backup did not report success for 20 minutes.
- `BackupRepositoryCheckFailed`: repository-only check from the daily backup did not report success for 20 minutes.
- `BackupVerifyDataFailed`: weekly verify-data check did not report success for 1 hour.
- `BackupTooOld`: no successful daily backup for 36 hours.
- `BackupCheckTooOld`: no successful weekly verification for 9 days.

The backup timer runs daily around 04:00. The 36-hour threshold allows one missed run plus grace. The verification timer runs weekly, so 9 days allows a missed or delayed weekly run before alerting.

Disk:

- Warning above 80% usage for 30 minutes.
- Critical above 90% usage for 10 minutes.
- Covered mount points:
  - `/`
  - `/srv`
  - `/srv/shreyws/backups`

The `/` and `/srv` mount points use native Node Exporter filesystem metrics. The `/srv/shreyws/backups` mount is exported through the existing textfile metrics path because Node Exporter cannot currently stat that mount from its container without permission errors.

Containers:

- `ShreyWSContainerUnhealthy`: a ShreyWS-managed Docker healthcheck reports unhealthy for 5 minutes.
- `ShreyWSContainerRestartLoop`: a ShreyWS-managed container restarts more than twice in 30 minutes and remains in that state for 10 minutes.

Container filters use existing repository-owned Docker labels:

```text
container_label_diun_enable="true"
name=~"shreyws-.+"
```

This ignores unrelated containers not managed by this repository.

Targets and host:

- `HostMetricsMissing`: Node Exporter scrape is down for 5 minutes.
- `PrometheusTargetDown`: expected scrape target is down for 5 minutes.

## Grouping and Inhibition

Alertmanager groups by:

```text
alertname, severity, job, instance
```

Timing:

- `group_wait`: 30 seconds
- `group_interval`: 5 minutes
- `repeat_interval`: 12 hours
- `resolve_timeout`: 2 minutes

Inhibition:

- Critical filesystem alerts suppress matching warning filesystem alerts for the same mount point.
- Host metrics missing can suppress lower-severity dependent alerts where the `instance` label matches.

## Backup Metrics

Backup status is exported by the existing `backup-metrics` container through Node Exporter's textfile collector.

Metrics include:

```text
shreyws_backup_last_success_timestamp_seconds
shreyws_backup_age_seconds
shreyws_backup_last_success
shreyws_backup_repository_check_success
shreyws_backup_verify_data_success
shreyws_backup_check_last_success_timestamp_seconds
shreyws_backup_check_age_seconds
```

The backup scripts themselves remain root-owned and were not weakened. The metrics exporter reads backup logs and writes textfile metrics atomically.

## SMART Monitoring

`smartmontools.service` remains the primary SMART monitoring mechanism.

Current status:

```bash
systemctl status smartmontools --no-pager
```

I tested `smartctl-exporter` with explicit `/dev/sda`, `/dev/sdb`, and `/dev/sdc` mappings. The container could not read SMART data even with explicit device mappings, SCSI generic devices, `SYS_RAWIO`, and privileged mode in this Docker environment. Because of that, this alerting rollout does not add a broken SMART exporter.

Recommended next improvement: add a root-level smartd hook under `/etc/smartmontools/run.d/` that writes a Node Exporter textfile metric when smartd reports a disk failure. That requires root access to `/etc/smartmontools` and should be implemented as a separate, focused change.

## Viewing Alerts

Prometheus active alerts:

```bash
docker exec shreyws-prometheus wget -q -O - http://127.0.0.1:9090/prometheus/api/v1/alerts
```

Alertmanager status:

```bash
docker exec shreyws-alertmanager wget -q -O - http://127.0.0.1:9093/api/v2/status
```

Alertmanager alerts:

```bash
docker exec shreyws-alertmanager wget -q -O - http://127.0.0.1:9093/api/v2/alerts
```

Local notification log:

```bash
tail -n 50 /srv/shreyws/logs/alertmanager/alerts.jsonl
```

## Safe Test Alert

Use a temporary Prometheus rule to test the full Prometheus-to-Alertmanager pipeline without breaking production services:

```bash
cd /srv/shreyws/infra/compose/monitoring
cat > prometheus/rules/shreyws-test-alert.yml <<'EOF'
groups:
  - name: shreyws-test-alert
    rules:
      - alert: ShreyWSTestRuleAlert
        expr: vector(1)
        for: 0m
        labels:
          severity: info
          instance: synthetic-rule
        annotations:
          summary: Synthetic Prometheus rule alert test
EOF

docker run --rm --entrypoint promtool \
  -v /srv/shreyws/infra/compose/monitoring/prometheus/rules:/rules:ro \
  prom/prometheus:v3.13.0 check rules /rules/shreyws-test-alert.yml

docker compose restart prometheus
sleep 60
tail -n 20 /srv/shreyws/logs/alertmanager/alerts.jsonl

rm prometheus/rules/shreyws-test-alert.yml
docker compose restart prometheus
sleep 180
tail -n 40 /srv/shreyws/logs/alertmanager/alerts.jsonl
```

The log should show a `firing` notification followed by a `resolved` notification. Always remove `shreyws-test-alert.yml` after testing.

## External Notifications

The initial receiver is local logging only and requires no secrets.

To add an external channel later:

1. Add the receiver to `alertmanager.yml`.
2. Store credentials in an ignored `.env` file or another existing secret pattern.
3. Do not commit tokens, webhooks, passwords, or API keys.
4. Test with a synthetic alert before relying on it.

## Silencing and Tuning

For temporary planned work, use Alertmanager silences through the API or `amtool` inside the Alertmanager container.

Tune noisy alerts in:

```text
compose/monitoring/prometheus/rules/shreyws-alerts.yml
```

Prefer increasing `for:` durations before weakening thresholds.

## Limitations

Prometheus, Alertmanager, and the alert log all run on ShreyWS. If the entire host, Docker daemon, network, storage, or power fails, local alerting cannot notify you. Total outage detection requires an external monitor outside this server, such as Uptime Kuma on another machine or an external health check.
