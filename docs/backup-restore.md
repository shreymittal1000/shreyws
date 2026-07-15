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

### Recovery Prerequisites

To recover ShreyWS onto a fresh Debian installation, you need:

- Debian 13 installed and bootable.
- The dedicated backup disk mounted at `/srv/shreyws/backups`.
- BorgBackup installed.
- Docker Engine and Docker Compose installed.
- The Borg repository at `/srv/shreyws/backups/borg`.
- The Borg passphrase from `/etc/shreyws-backup/borg.env` or an offline copy.
- The exported Borg repository key from `/etc/shreyws-backup/borg-repokey-export` or an offline copy.
- Tailscale access or local console access for initial administration.
- Any runtime credentials that are not in the backup, such as external API tokens.

The Borg environment file and exported repository key are secret material. Never paste their contents into chat, tickets, logs, or Git.

### Archive Discovery

1. List available archives:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; borg list "$BORG_REPO"'
```

2. Inspect the newest archive:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; latest=$(borg list --last 1 --format "{archive}" "$BORG_REPO"); borg info "$BORG_REPO::$latest"'
```

3. Confirm expected paths exist:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; latest=$(borg list --last 1 --format "{archive}" "$BORG_REPO"); borg list "$BORG_REPO::$latest" | grep -E "srv/shreyws/infra|etc/shreyws-backup|srv/shreyws/services/grafana|srv/shreyws/services/prometheus"'
```

### Safe Extraction

Create a temporary restore directory outside live service paths:

```bash
ts=$(date +%Y%m%d-%H%M%S)
sudo install -d -m 700 /srv/shreyws/restore-tests/$ts
```

Extract either a representative path or the complete archive. Do not extract into `/`, `/srv/shreyws/infra`, Docker volumes, or live application data directories.

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; latest=$(borg list --last 1 --format "{archive}" "$BORG_REPO"); cd /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS; umask 077; borg extract --numeric-ids "$BORG_REPO::$latest"'
```

For a smaller test restore:

```bash
sudo bash -c 'source /etc/shreyws-backup/borg.env; latest=$(borg list --last 1 --format "{archive}" "$BORG_REPO"); cd /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS; umask 077; borg extract --numeric-ids "$BORG_REPO::$latest" srv/shreyws/infra/compose/monitoring/prometheus/prometheus.yml'
```

### Validation Checklist

From the restored copy:

```bash
restore=/srv/shreyws/restore-tests/YYYYMMDD-HHMMSS

sudo test -d "$restore/srv/shreyws/infra"
sudo test -f "$restore/etc/shreyws-backup/borg.env"
sudo test -f "$restore/etc/systemd/system/shreyws-backup.timer"
sudo git -c safe.directory="$restore/srv/shreyws/infra" -C "$restore/srv/shreyws/infra" status --short

for project in authentik diun grafana homepage monitoring traefik; do
  (cd "$restore/srv/shreyws/infra/compose/$project" && sudo docker compose config --quiet)
done

sudo systemd-analyze verify \
  "$restore/etc/systemd/system/shreyws-backup.service" \
  "$restore/etc/systemd/system/shreyws-backup.timer" \
  "$restore/etc/systemd/system/shreyws-backup-check.service" \
  "$restore/etc/systemd/system/shreyws-backup-check.timer"
```

Representative checksum comparison:

```bash
restore=/srv/shreyws/restore-tests/YYYYMMDD-HHMMSS
sha256sum /srv/shreyws/infra/compose/traefik/compose.yaml "$restore/srv/shreyws/infra/compose/traefik/compose.yaml"
```

Files may legitimately differ if the archive predates recent commits or configuration changes.

For database-like files, inspect without importing into live services:

```bash
file "$restore/srv/shreyws/services/grafana/grafana.db"
file "$restore/var/lib/docker/volumes/grafana_grafana_data/_data/grafana.db"
find "$restore/srv/shreyws/services/prometheus" -maxdepth 2 -name meta.json
```

If `sqlite3` is installed, Grafana DB integrity can be checked safely:

```bash
sqlite3 "$restore/var/lib/docker/volumes/grafana_grafana_data/_data/grafana.db" 'pragma integrity_check;'
```

Do not start restored Compose projects from the temporary directory. Restored Compose files can contain absolute bind mounts that point at live paths.

### Complete Server Rebuild Sequence

1. Install Debian 13.
2. Mount the backup disk at `/srv/shreyws/backups`.
3. Install required packages: `borgbackup`, Docker Engine, Docker Compose plugin, Tailscale, `smartmontools`, and any host utilities documented in the infra repo.
4. Restore `/etc/shreyws-backup` from Borg or from offline copies.
5. Confirm the Borg passphrase and exported repository key are available.
6. Extract the chosen archive into a temporary restore directory.
7. Restore host configuration from `etc/` selectively. Do not blindly overwrite a fresh `/etc`; review networking, SSH, Tailscale, fstab, systemd units, and smartd config first.
8. Restore `/srv/shreyws/infra`.
9. Restore `/srv/shreyws/config`, `/srv/shreyws/agents`, and `/srv/shreyws/compose` if present.
10. Restore service data directories and Docker volume data only after inspecting them.
11. Restore systemd backup units and scripts if available.
12. Validate Compose config for every project.
13. Start services in dependency order.

### Application Data Restoration Order

Recommended order:

1. Traefik infrastructure configuration.
2. Authentik database and Authentik media/templates/certs.
3. Authentik server, worker, and proxy outpost.
4. Monitoring stack: Prometheus, Alertmanager, Node Exporter, cAdvisor, alert webhooks.
5. Grafana data and provisioning.
6. Homepage configuration.
7. Diun data.
8. Backup metrics and backup timers.

Start only the affected Compose project when restoring one service. Example for Grafana:

```bash
cd /srv/shreyws/infra/compose/grafana
sudo docker compose down
```

Copy inspected restored data into place with ownership preserved:

```bash
sudo rsync -aHAX --numeric-ids /tmp/shreyws-restore/path/to/restored/data/ /path/to/live/data/
```

Start the affected Compose project again:

```bash
cd /srv/shreyws/infra/compose/grafana
sudo docker compose up -d
```

### Service Start Order

For a full rebuild, use this order:

1. `traefik`
2. `authentik`
3. `monitoring`
4. `grafana`
5. `homepage`
6. `diun`

Verify each project with `docker compose ps` and logs before moving to the next.

### Secret Restoration

Restore secrets only to their intended root-only or restricted paths. Current examples:

- `/etc/shreyws-backup/borg.env`
- `/etc/shreyws-backup/borg-repokey-export`
- `/srv/shreyws/infra/compose/authentik/.env`
- `/srv/shreyws/secrets/alertmanager/telegram.env`

Use restrictive permissions:

```bash
sudo chmod 700 /etc/shreyws-backup
sudo chmod 600 /etc/shreyws-backup/borg.env /etc/shreyws-backup/borg-repokey-export
sudo chmod 700 /srv/shreyws/secrets/alertmanager
sudo chmod 600 /srv/shreyws/secrets/alertmanager/telegram.env
```

Do not commit real secrets to Git.

### Rollback Guidance

Before replacing live service data:

1. Stop only the affected Compose project.
2. Move the current live data aside with a timestamped suffix.
3. Restore the inspected data with `rsync -aHAX --numeric-ids`.
4. Start the affected project.
5. Verify the service.
6. If verification fails, stop the project and move the previous live data back.

### Cleanup

```bash
sudo rm -rf /srv/shreyws/restore-tests/YYYYMMDD-HHMMSS
```

## Verified Restore Test

On 2026-07-08, archive `shreyws-2026-07-08T09:35:15` was restored into `/tmp`, and `srv/shreyws/infra/docs/backup-restore.md` matched the live file byte-for-byte. The temporary restore directory was removed after verification.

On 2026-07-16, archive `shreyws-2026-07-15T03:49:49` was fully extracted into `/srv/shreyws/restore-tests/20260716-011848` for a non-destructive disaster-recovery drill. The restored data used about 515 MB and was removed after validation.

Validated successfully:

- Borg repository was accessible and encrypted with repokey mode.
- Archive metadata was readable.
- Complete archive extraction succeeded outside live paths.
- Restored `/etc/shreyws-backup` preserved root-only permissions.
- Restored systemd backup units passed `systemd-analyze verify`.
- Restored Compose projects `authentik`, `diun`, `grafana`, `homepage`, `monitoring`, and `traefik` passed `docker compose config --quiet`.
- Restored infra Git repository was readable with a one-command safe-directory override.
- Restored Traefik compose, Homepage services config, backup restore docs, and backup timer matched live checksums at the time of the drill.
- Restored Grafana database files were recognized as SQLite databases.
- Restored Prometheus data contained block metadata.
- Live containers were not stopped or restarted.
- Backup timers remained enabled and active.

Important findings from the 2026-07-16 drill:

- The newest archive was from 2026-07-15 03:49:49, before later alerting and Telegram commits. The realistic recovery point is the latest successful daily timer run, so very recent commits may not be present until the next backup.
- `/usr/local/sbin/shreyws-backup` and `/usr/local/sbin/shreyws-backup-check` are not currently included in the archive. The systemd units are included under `/etc/systemd/system`, but the executable scripts themselves must be recreated manually or added to a future backup scope.
- Authentik PostgreSQL data in Docker volume `authentik_database` is not currently included, and no Authentik database dump was found. Authentik is not fully recoverable from the current backup.
- `/srv/shreyws/secrets` is not currently included. Runtime secrets such as Telegram alert credentials must be restored from another copy or added to future backup scope.
- `/srv/shreyws/services/diun` and `/srv/shreyws/services/alertmanager` are not currently included. Diun state and Alertmanager silences/history are not fully recoverable.
- Prometheus WAL/head chunks are intentionally excluded; restored Prometheus data is a recent block-level snapshot, not an exact crash-consistent live state.
- Grafana data exists in both `/srv/shreyws/services/grafana` and the legacy Docker volume path `grafana_grafana_data`; confirm the active mount before restoring.
