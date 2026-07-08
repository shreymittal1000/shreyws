# ShreyWS Alerts

Alert rules live in:

```text
/srv/shreyws/infra/compose/monitoring/prometheus/rules/shreyws-alerts.yml
```

Prometheus loads them through `/etc/prometheus/rules/*.yml`.

## Critical Alerts

- `HostDown`: Node Exporter cannot be scraped.
- `PrometheusDown`: Prometheus self-scrape is down.
- `TraefikDown`: Traefik metrics cannot be scraped.
- `BackupFailed`: latest backup or repository check did not report success.
- `BackupOlderThan48Hours`: latest successful backup is older than 48 hours.

## Warning Alerts

- `FilesystemUsageHigh`: filesystem above 80% used for 15 minutes.
- `FilesystemAlmostFull`: filesystem above 90% used for 10 minutes.
- `InodesLow`: inode usage above 80% for 15 minutes.
- `MemoryUsageHigh`: memory usage above 90% for 15 minutes.
- `CpuUsageHigh`: CPU usage above 95% for 15 minutes.
- `OomKillObserved`: host or container OOM event in the last 15 minutes.
- `ContainerRestartedRecently`: container start time changed repeatedly in 30 minutes.

## Noise Control

The rules intentionally avoid alerts for known monitoring gaps such as missing SMART metrics or missing systemd metrics. Those are documented as limitations instead of permanent firing alerts.

## Verification

Run:

```bash
docker exec shreyws-prometheus promtool check config /etc/prometheus/prometheus.yml
docker exec shreyws-prometheus promtool check rules /etc/prometheus/rules/shreyws-alerts.yml
```

Check active alerts:

```bash
docker exec shreyws-prometheus wget -qO- http://localhost:9090/prometheus/api/v1/alerts
```
