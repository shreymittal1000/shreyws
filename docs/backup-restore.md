# ShreyWS Backup and Restore

ShreyWS uses BorgBackup for encrypted, compressed, deduplicated local snapshots on the dedicated 500 GB backup disk.

## Layout

- Backup disk mount: `/srv/shreyws/backups`
- Borg repository: `/srv/shreyws/backups/borg`
- Borg environment file: `/etc/shreyws-backup/borg.env`
- Backup script: `/usr/local/sbin/shreyws-backup`
- Verification script: `/usr/local/sbin/shreyws-backup-check`
- Source-controlled script copies: `/srv/shreyws/infra/scripts/shreyws-backup` and `/srv/shreyws/infra/scripts/shreyws-backup-check`
- Source-controlled systemd unit copies: `/srv/shreyws/infra/systemd/`
- Logs: `/var/log/shreyws-backup/backup.log` and `/var/log/shreyws-backup/check.log`
- Daily timer: `shreyws-backup.timer`
- Weekly verification timer: `shreyws-backup-check.timer`
- Authentik PostgreSQL dump staging path during backup: `/var/lib/shreyws-backup/staging/authentik-postgresql.dump`

The Borg passphrase is stored root-only in `/etc/shreyws-backup/borg.env`. The Borg repository key was exported to `/etc/shreyws-backup/borg-repokey-export` with root-only permissions. Keep offline copies of both the passphrase and exported key. Without the passphrase and key material, the encrypted repository cannot be restored.

The deployed copies under `/usr/local/sbin` and `/etc/systemd/system` are the runtime copies. The repository copies are the canonical recovery source and should be updated in the same commit whenever deployed backup scripts or units change.

## Schedule and Retention

Backups run daily via systemd, not cron.

Retention is enforced by `borg prune`:

- 7 daily archives
- 5 weekly archives
- 12 monthly archives

A repository-only consistency check runs after each backup. A full `borg check --verify-data` runs weekly.

## Backup Sequence

The daily backup job runs as root through systemd:

1. Loads `/etc/shreyws-backup/borg.env`.
2. Acquires `/run/shreyws-backup.lock`.
3. Confirms `/srv/shreyws/backups` is mounted.
4. Recreates `/var/lib/shreyws-backup/staging` with mode `0700`.
5. Generates an Authentik PostgreSQL custom-format dump from the running `shreyws-authentik-postgresql` container with `pg_dump -Fc`.
6. Validates the dump with `pg_restore --list`.
7. Creates a Borg archive containing static files, service data, secrets, backup scripts, and the generated dump.
8. Applies the existing retention policy.
9. Runs a repository-only Borg consistency check.
10. Removes the root-only staging directory on exit.

If the Authentik dump or validation fails, the backup job exits non-zero. Existing backup failure alerts then fire through the normal Prometheus/Alertmanager pipeline.

## Included Data

The backup includes:

- `/etc`
- `/usr/local/sbin/shreyws-backup`
- `/usr/local/sbin/shreyws-backup-check`
- `/home/shreyws`
- `/srv/shreyws/infra`
- `/srv/shreyws/config`
- `/srv/shreyws/agents`
- `/srv/shreyws/compose`
- `/srv/shreyws/secrets`
- `/srv/shreyws/services/alertmanager`
- `/srv/shreyws/services/diun`
- `/srv/shreyws/services/grafana`
- `/srv/shreyws/services/prometheus`
- `/var/lib/shreyws-backup`
- `/var/lib/docker/volumes/grafana_grafana_data/_data`

The Authentik PostgreSQL database is backed up as:

```text
var/lib/shreyws-backup/staging/authentik-postgresql.dump
```

The dump is a PostgreSQL custom-format archive generated from the live PostgreSQL container. It is intended to be restored with `pg_restore`, not copied over the raw PostgreSQL data directory.

## Service Data Coverage

| Service | Persistent data | Source path or volume | Backup method | Restore method |
| --- | --- | --- | --- | --- |
| Traefik | Compose config | `/srv/shreyws/infra/compose/traefik` | Borg file backup through `/srv/shreyws/infra` | Restore infra repo, then `docker compose up -d` |
| Authentik app | Compose, `.env`, templates, media/certs | `/srv/shreyws/infra/compose/authentik` | Borg file backup through `/srv/shreyws/infra` | Restore files before starting Authentik |
| Authentik PostgreSQL | Database | Docker volume `authentik_database` | Logical `pg_dump -Fc` to `/var/lib/shreyws-backup/staging/authentik-postgresql.dump` | Restore into a fresh PostgreSQL container with `pg_restore` |
| Grafana | SQLite data and provisioning | `grafana_grafana_data`, `/srv/shreyws/services/grafana`, repo provisioning | Borg file backup | Restore active data path with ownership preserved |
| Prometheus | TSDB blocks and config | `/srv/shreyws/services/prometheus`, repo config | Borg file backup, excluding WAL/head chunks | Restore blocks/config; Prometheus rebuilds WAL/head |
| Alertmanager | Silences/notification state | `/srv/shreyws/services/alertmanager` | Borg file backup | Restore before starting Alertmanager |
| Homepage | Configuration | `/srv/shreyws/infra/compose/homepage/config` | Borg file backup through `/srv/shreyws/infra` | Restore config before starting Homepage |
| Diun | Update-notifier state | `/srv/shreyws/services/diun/diun.db` | Borg file backup, excluding `notifications.log` | Restore data before starting Diun |
| Loki | Configuration, datasource, dashboard | `/srv/shreyws/infra/compose/logging`, Grafana provisioning files | Borg file backup through `/srv/shreyws/infra` | Restore config and recreate logging stack |
| Loki log data | Searchable operational logs | `/srv/shreyws/services/loki` | Intentionally excluded | Recreated from new logs after service restart |
| Alloy | Configuration and local state | `/srv/shreyws/infra/compose/logging`, `/srv/shreyws/services/alloy` | Config through Borg repo backup; state is disposable | Restore config and recreate Alloy |
| Telegram alerts | Runtime credentials | `/srv/shreyws/secrets/alertmanager/telegram.env` | Borg file backup of `/srv/shreyws/secrets` | Restore with restrictive permissions before starting webhook |
| Backup system | Env, repo key export, scripts, units | `/etc/shreyws-backup`, `/usr/local/sbin/shreyws-*`, repo `scripts/`, repo `systemd/` | Borg file backup and Git | Restore root-only env/key, install scripts/units |

## Exclusions

The backup excludes regenerable or noisy data, including:

- Backup repository itself
- `/tmp`, `/run`, `/proc`, `/sys`, `/dev`, `/mnt`, `/media`
- `/var/cache`, `/var/tmp`, `/var/log`
- Docker image/build storage: `/var/lib/docker/overlay2`, `/var/lib/docker/image`, `/var/lib/docker/buildkit`
- Prometheus WAL/head chunks
- Loki bulk log data: `/srv/shreyws/services/loki`
- Alloy runtime state: `/srv/shreyws/services/alloy`
- Grafana generated CSV/PDF/PNG/plugin files
- Diun notification log: `/srv/shreyws/services/diun/notifications.log`
- Common user/project caches and build artifacts such as `.cache`, `node_modules`, `target`, `dist`, and `build`

## Secrets Trust Model

The backup now includes `/srv/shreyws/secrets` because the Borg repository is encrypted at rest. This is the simplest reliable recovery design: a restored server can recover runtime secret files at the paths expected by Docker Compose without inventing a second backup mechanism.

Security assumptions:

- Theft of only the backup disk should not reveal secrets without the Borg key material and passphrase.
- Theft of the running server can expose `/etc/shreyws-backup/borg.env`, the exported repository key, and live secrets because the server must be able to run unattended backups.
- The exported repository key and Borg passphrase must also exist off-server. If the server dies with the only copy of `/etc/shreyws-backup`, the encrypted Borg repository may be unrecoverable.
- Do not store the only offline copy of the passphrase or exported key on the same physical disk as the Borg repository.
- Restored secret files must keep restrictive permissions, usually `0600` for files and `0700` for secret directories.

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

Run a manual validation backup without pruning existing archives:

```bash
sudo SHREYWS_BACKUP_SKIP_PRUNE=1 /usr/local/sbin/shreyws-backup
```

The skip-prune flag is intended for controlled validation only. Scheduled backups do not set it and continue to enforce retention.

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
file "$restore/var/lib/shreyws-backup/staging/authentik-postgresql.dump"
find "$restore/srv/shreyws/services/prometheus" -maxdepth 2 -name meta.json
```

If `sqlite3` is installed, Grafana DB integrity can be checked safely:

```bash
sqlite3 "$restore/var/lib/docker/volumes/grafana_grafana_data/_data/grafana.db" 'pragma integrity_check;'
```

Validate the Authentik PostgreSQL dump without importing it:

```bash
sudo docker exec -i shreyws-authentik-postgresql pg_restore --list < "$restore/var/lib/shreyws-backup/staging/authentik-postgresql.dump" >/dev/null
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
11. Install backup scripts from `/srv/shreyws/infra/scripts/` to `/usr/local/sbin/` with owner `root:root` and mode `0750`.
12. Install backup units from `/srv/shreyws/infra/systemd/` to `/etc/systemd/system/`, then run `systemctl daemon-reload`.
13. Validate Compose config for every project.
14. Start services in dependency order.

### Application Data Restoration Order

Recommended order:

1. Traefik infrastructure configuration.
2. Authentik media/templates/certs and runtime `.env`.
3. Authentik PostgreSQL database dump.
4. Authentik server, worker, and proxy outpost.
5. Monitoring stack: Prometheus, Alertmanager, Node Exporter, cAdvisor, alert webhooks.
6. Grafana data and provisioning.
7. Homepage configuration.
8. Diun data.
9. Backup metrics and backup timers.

### Authentik Database Restore

The backup contains a logical PostgreSQL custom-format dump at:

```text
var/lib/shreyws-backup/staging/authentik-postgresql.dump
```

Restore it only into a fresh or intentionally reset Authentik PostgreSQL database. Do not import it into the live production database during a test.

High-level rebuild procedure:

1. Restore `/srv/shreyws/infra/compose/authentik/.env` and verify permissions.
2. Restore Authentik media/templates/certs under `/srv/shreyws/infra/compose/authentik/`.
3. Start only PostgreSQL first:

   ```bash
   cd /srv/shreyws/infra/compose/authentik
   docker compose up -d postgresql
   docker compose exec postgresql pg_isready
   ```

4. Stream the dump into `pg_restore`. The exact database/user names come from the restored `.env`; do not print them in shared logs.

   ```bash
   restore=/srv/shreyws/restore-tests/YYYYMMDD-HHMMSS
   docker compose exec -T postgresql sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$restore/var/lib/shreyws-backup/staging/authentik-postgresql.dump"
   ```

5. Start Authentik server, worker, and proxy:

   ```bash
   docker compose up -d
   ```

6. Validate:

   ```bash
   docker compose ps
   docker logs --tail=100 shreyws-authentik-server
   docker exec shreyws-authentik-server python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/authentik/-/health/live/', timeout=5)"
   ```

7. Verify login through the browser and confirm the outpost still protects services.

If the original Borg key or passphrase is unavailable, the encrypted Borg repository cannot be decrypted. Recovery then depends on independent off-server copies of:

- Borg passphrase
- exported Borg repository key
- Authentik `.env`
- Telegram secret file
- any other service credentials not recoverable from Git

### Single-Service Restore Example

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
- At the time of that drill, `/usr/local/sbin/shreyws-backup`, `/usr/local/sbin/shreyws-backup-check`, Authentik PostgreSQL, `/srv/shreyws/secrets`, `/srv/shreyws/services/diun`, and `/srv/shreyws/services/alertmanager` were not fully covered.
- A later backup coverage update added those paths and an Authentik PostgreSQL logical dump to future Borg archives.
- Prometheus WAL/head chunks are intentionally excluded; restored Prometheus data is a recent block-level snapshot, not an exact crash-consistent live state.
- Grafana data exists in both `/srv/shreyws/services/grafana` and the legacy Docker volume path `grafana_grafana_data`; confirm the active mount before restoring.

On 2026-07-16, archive `shreyws-2026-07-16T09:51:21` was created with the backup gap closure changes and tested with a targeted restore into `/srv/shreyws/restore-tests/backup-gap-20260716-095121`. The temporary restore directory was removed after validation.

Validated successfully:

- `/usr/local/sbin/shreyws-backup` and `/usr/local/sbin/shreyws-backup-check` were present in the archive and restored with owner `root:root` and mode `0750`.
- `/srv/shreyws/secrets` was present in the archive. The representative restored secret file had owner `root:root` and mode `0600`; contents were not displayed.
- `/srv/shreyws/services/diun` was present, and `notifications.log` was excluded as intended.
- `/srv/shreyws/services/alertmanager` was present.
- `var/lib/shreyws-backup/staging/authentik-postgresql.dump` was present, root-only, non-empty, and inspectable with `pg_restore --list`.
- The backup staging directory was removed after the backup job completed.
- The targeted restore directory was removed after validation.
- Live Authentik, PostgreSQL, monitoring, and Traefik containers remained running.

Remaining recovery risks:

- The backup disk, Borg repository key, and Borg passphrase must not be the only copies. Keep an offline copy of the exported repository key and passphrase.
- The Authentik PostgreSQL dump is transaction-consistent at dump time, but restoring it still requires careful ordering: PostgreSQL first, then `pg_restore`, then Authentik server/worker/proxy.
- Prometheus WAL/head chunks remain intentionally excluded.
