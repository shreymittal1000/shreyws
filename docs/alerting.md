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
  -> Telegram webhook sends concise Telegram messages when enabled
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

Telegram delivery is optional and uses a separate internal webhook service. The local JSONL log remains enabled as the audit trail and fallback even when Telegram is configured.

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

Telegram alert webhook:

```text
python:3.13.5-alpine3.22
```

All alerting services use fixed image tags.

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

Telegram webhook implementation:

```text
/srv/shreyws/infra/compose/monitoring/telegram-webhook/telegram-alert-webhook.py
```

Telegram chat ID helper:

```text
/srv/shreyws/infra/compose/monitoring/telegram-webhook/get-telegram-chat-id.py
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
- Inode warning above 80% usage for 30 minutes.
- Inode critical above 90% usage for 10 minutes.
- Covered mount points:
  - `/`
  - `/srv`
  - `/srv/shreyws/backups`

The `/` and `/srv` mount points use native Node Exporter filesystem metrics. The `/srv/shreyws/backups` mount is exported through the existing textfile metrics path because Node Exporter cannot currently stat that mount from its container without permission errors.

Inode alerts currently cover `/` and `/srv`, where Node Exporter exposes inode metrics. The backup disk textfile metric currently covers byte usage only.

Host:

- `HostHighCpuUsage`: CPU utilization above 90% for 30 minutes.
- `HostHighLoadAverage`: 15-minute load average above 1.5x CPU count for 30 minutes.
- `HostHighMemoryUsage`: memory usage above 90% for 30 minutes.
- `HostCriticalMemoryUsage`: memory usage above 95% for 10 minutes.

These thresholds are intentionally sustained rather than instant. Short compile jobs, container updates, and dashboard queries should not alert unless they create prolonged pressure.

Containers:

- `ShreyWSContainerUnhealthy`: a ShreyWS-managed Docker healthcheck reports unhealthy for 5 minutes.
- `ShreyWSContainerRestartLoop`: a ShreyWS-managed container restarts more than twice in 30 minutes and remains in that state for 10 minutes.
- `ShreyWSContainerUnexpectedlyDown`: an expected ShreyWS-managed container disappears from cAdvisor metrics for 5 minutes.
- `ShreyWSContainerOomEvent`: a ShreyWS-managed container reports an OOM event within a 15-minute window.
- `TelegramAlertWebhookUnavailable`: the Telegram alert webhook container disappears from cAdvisor metrics for 5 minutes.

Container filters use existing repository-owned Docker labels:

```text
container_label_diun_enable="true"
name=~"shreyws-.+"
```

This ignores unrelated containers not managed by this repository. The unexpected-down rule is intentionally explicit about expected ShreyWS container names, because cAdvisor cannot infer that a missing container should exist.

Targets and host:

- `HostMetricsMissing`: Node Exporter scrape is down for 5 minutes.
- `PrometheusTargetDown`: expected scrape target is down for 5 minutes.
- `AlertmanagerUnavailable`: Alertmanager scrape is down for 5 minutes.

`PrometheusTargetDown` is a general warning for expected scrape targets. `AlertmanagerUnavailable` is critical because Alertmanager is required for JSONL and Telegram delivery.

SMART:

- No Prometheus SMART alert is currently configured because no SMART metric series are exported to Prometheus.
- `smartmontools.service` remains the active disk-health mechanism.
- A future root-level smartd hook can export SMART status through Node Exporter's textfile collector without adding a privileged SMART exporter container.

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
- Critical inode alerts suppress matching warning inode alerts for the same mount point.
- Critical memory alerts suppress matching warning memory alerts for the same instance.
- `AlertmanagerUnavailable` suppresses the matching generic `PrometheusTargetDown` warning.
- Host metrics missing can suppress lower-severity dependent alerts where the `instance` label matches.

Alertmanager sends each alert group to two independent webhook receivers:

- `local-alert-log`
- `telegram-alerts`

The local JSONL receiver remains independent, so a Telegram API failure does not prevent local alert logging.

The Telegram secret file is mounted read-only into the container at:

```text
/run/secrets/telegram.env
```

The service reads credentials from that file instead of Docker Compose `env_file`, so rendered Compose output and Docker labels do not contain the bot token or chat ID.

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

Telegram webhook health:

```bash
docker exec shreyws-telegram-alert-webhook wget -q -O - http://127.0.0.1:8081/health
```

Telegram webhook logs:

```bash
docker logs --tail=100 shreyws-telegram-alert-webhook
```

The health endpoint reports `telegram=disabled` until valid credentials are configured and `TELEGRAM_ENABLED=true` is set.

## Telegram Notifications

Telegram delivery is optional. It is disabled by default and requires a runtime secret file outside Git:

```text
/srv/shreyws/secrets/alertmanager/telegram.env
```

Recommended permissions:

```bash
sudo install -d -m 700 /srv/shreyws/secrets/alertmanager
sudo install -m 600 /dev/null /srv/shreyws/secrets/alertmanager/telegram.env
```

Template:

```text
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<bot token>
TELEGRAM_CHAT_ID=<chat id>
```

The committed example file is:

```text
compose/monitoring/telegram-webhook/telegram.env.example
```

Do not put real Telegram credentials in the repository.

The Telegram webhook container currently runs without a custom non-root UID so it can read the restrictive `0600` host secret file. It has no Docker socket, no host ports, no devices, and no Traefik route.

### Bot Setup

1. Open Telegram and start a chat with the official `BotFather` account.
2. Create a bot with `/newbot`.
3. Copy the bot token.
4. Start a private conversation with the bot, or add the bot to a private group.
5. Send at least one message to the bot or group so Telegram creates an update.
6. Store the token in `/srv/shreyws/secrets/alertmanager/telegram.env`.
7. Determine the chat ID with the helper:

   ```bash
   cd /srv/shreyws/infra/compose/monitoring
   docker compose run --rm telegram-alert-webhook python /app/get-telegram-chat-id.py
   ```

   Private chat IDs are usually positive numbers. Group chat IDs are usually negative numbers.

8. Add `TELEGRAM_CHAT_ID` to the secret file.
9. Restart only the Telegram webhook and Alertmanager:

   ```bash
   cd /srv/shreyws/infra/compose/monitoring
   docker compose up -d --no-deps telegram-alert-webhook alertmanager
   ```

### Message Format

Telegram messages are plain text for reliability. Each message includes:

- firing or resolved status
- alert name
- severity
- summary
- description when present
- affected service or instance
- start time
- resolved time when present
- number of grouped alerts

Messages are truncated before Telegram's maximum message length.

### Disable Telegram

Set:

```text
TELEGRAM_ENABLED=false
```

Then recreate only the Telegram webhook:

```bash
cd /srv/shreyws/infra/compose/monitoring
docker compose up -d --no-deps telegram-alert-webhook
```

The webhook remains healthy and returns success to Alertmanager while disabled, so local alert logging continues without retries.

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

docker kill -s HUP shreyws-prometheus
sleep 60
tail -n 20 /srv/shreyws/logs/alertmanager/alerts.jsonl

rm prometheus/rules/shreyws-test-alert.yml
docker kill -s HUP shreyws-prometheus
sleep 180
tail -n 40 /srv/shreyws/logs/alertmanager/alerts.jsonl
```

The log should show a `firing` notification followed by a `resolved` notification. Always remove `shreyws-test-alert.yml` after testing.

When Telegram is enabled, the same test should also deliver a firing Telegram message and a resolved Telegram message. Expected Telegram output is concise plain text with:

- `ShreyWS Alertmanager: FIRING` or `ShreyWS Alertmanager: RESOLVED`
- grouped alert count
- alert name
- severity
- summary
- description when present
- affected instance or service

## External Notifications

The always-on receiver is local logging and requires no secrets. Telegram is the first optional external notification channel and reads credentials from the runtime secret file.

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

## Telegram Troubleshooting

Bot blocked:

- Open the bot chat and press Start again.
- Confirm the bot token has not been revoked.

Wrong chat ID:

- Re-run `get-telegram-chat-id.py` after sending a new message.
- Group chat IDs are commonly negative.

Bot not added to group:

- Add the bot to the group and send a message that mentions or wakes the bot.

Group privacy or settings:

- If the helper cannot see group updates, temporarily disable BotFather privacy for the bot or send a direct command/message to the bot inside the group.

Telegram API unavailable:

- The webhook returns a non-2xx response so Alertmanager can retry.
- Check `docker logs shreyws-telegram-alert-webhook`.

Malformed formatting:

- The webhook sends plain text, not Markdown or HTML, to avoid fragile escaping problems.

Token leak:

1. Revoke the bot token with BotFather.
2. Generate a new token.
3. Update `/srv/shreyws/secrets/alertmanager/telegram.env`.
4. Recreate only `telegram-alert-webhook`.
5. Run the synthetic test alert.

## Limitations

Prometheus, Alertmanager, and the alert log all run on ShreyWS. If the entire host, Docker daemon, network, storage, or power fails, local alerting cannot notify you. Total outage detection requires an external monitor outside this server, such as Uptime Kuma on another machine or an external health check.
