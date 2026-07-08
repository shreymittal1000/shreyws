# ShreyWS Backup and Restore

ShreyWS uses BorgBackup for encrypted, compressed, deduplicated local snapshots on the dedicated 500 GB backup disk.

## Layout

- Backup disk mount: `/srv/shreyws/backups`
- Borg repository: `/srv/shreyws/backups/borg`
- Borg environment file: `/etc/shreyws-backup/borg.env`
- Backup script: `/usr/local/sbin/shreyws-backup`
- Verification script: `/usr/local/sbin/shreyws-backup-check`
- Logs: `/var/log/shreyws-backup/backup.log` and `/var/log/shreyws-backup/check.log`
- Daily timer: `shreyws-backup.timer`
- Weekly verification timer: `shreyws-backup-check.timer`

The Borg passphrase is stored root-only in `/etc/shreyws-backup/borg.env`. The Borg repository key was exported to `/etc/shreyws-backup/borg-repokey-export` with root-only permissions. Keep offline copies of both the passphrase and exported key. Without the passphrase and key material, the encrypted repository cannot be restored.

## Schedule and Retention

Backups run daily via systemd, not cron.

Retention is enforced by `borg prune`:

- 7 daily archives
- 5 weekly archives
- 12 monthly archives

A repository-only consistency check runs after each backup. A full `borg check --verify-data` runs weekly.

## Included Data

The backup includes:

- `/etc`
- `/home/shreyws`
- `/srv/shreyws/infra`
- `/srv/shreyws/config`
- `/srv/shreyws/agents`
- `/srv/shreyws/compose`
- `/srv/shreyws/services/grafana`
- `/srv/shreyws/services/prometheus`
- `/var/lib/docker/volumes/grafana_grafana_data/_data`

## Exclusions

The backup excludes regenerable or noisy data, including:

- Backup repository itself
- `/tmp`, `/run`, `/proc`, `/sys`, `/dev`, `/mnt`, `/media`
- `/var/cache`, `/var/tmp`, `/var/log`
- Docker image/build storage: `/var/lib/docker/overlay2`, `/var/lib/docker/image`, `/var/lib/docker/buildkit`
- Prometheus WAL/head chunks
- Grafana generated CSV/PDF/PNG/plugin files
- Common user/project caches and build artifacts such as `.cache`, `node_modules`, `target`, `dist`, and `build`

## Common Commands

List archives:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; borg list "$BORG_REPO"'
```

Show repository info:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; borg info "$BORG_REPO"'
```

Run a manual backup:

```bash
sudo /usr/local/sbin/shreyws-backup
```

Run a full repository verification:

```bash
sudo /usr/local/sbin/shreyws-backup-check
```

Check timer status:

```bash
systemctl list-timers 'shreyws-backup*'
systemctl status shreyws-backup.timer shreyws-backup-check.timer
```

## Restore Procedure

Do not restore directly over live service data until you have inspected the restored files.

1. List available archives:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; borg list "$BORG_REPO"'
```

2. Create a temporary restore directory:

```bash
sudo mkdir -p /tmp/shreyws-restore
sudo chmod 700 /tmp/shreyws-restore
```

3. Restore a path from an archive into the temporary directory:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; cd /tmp/shreyws-restore; borg extract "$BORG_REPO::ARCHIVE_NAME" srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml'
```

4. Inspect the restored file:

```bash
sudo ls -l /tmp/shreyws-restore/srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml
sudo diff -u /srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml /tmp/shreyws-restore/srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml
```

5. For service data restores, stop only the affected Compose project first. Example for Grafana:

```bash
cd /srv/shreyws/infra/compose/grafana
sudo docker compose down
```

6. Copy the inspected restored data into place with ownership preserved:

```bash
sudo rsync -aHAX --numeric-ids /tmp/shreyws-restore/path/to/restored/data/ /path/to/live/data/
```

7. Start the affected Compose project again:

```bash
cd /srv/shreyws/infra/compose/grafana
sudo docker compose up -d
```

8. Clean up the temporary restore directory:

```bash
sudo rm -rf /tmp/shreyws-restore
```

## Verified Restore Test

On 2026-07-08, archive `shreyws-2026-07-08T09:35:15` was restored into `/tmp`, and `srv/shreyws/infra/docs/backup-restore.md` matched the live file byte-for-byte. The temporary restore directory was removed after verification.
