# Container Update Management

ShreyWS uses a controlled container update workflow. Containers are not upgraded automatically.

The workflow has two parts:

- Diun checks for newer images and records update notifications locally.
- `scripts/shreyws-container-update` applies reviewed updates to one Compose project at a time.

## Architecture

Diun runs as a normal Docker Compose project:

```text
/srv/shreyws/infra/compose/diun
```

It has no Traefik labels, no published ports, and no public URL. It only needs outbound registry access and Docker API access.

Docker socket access:

```text
/var/run/docker.sock:/var/run/docker.sock:ro
```

The socket mount is read-only at the filesystem level. The Docker socket is still a high-trust interface, so only this update-checking container should receive it.

Persistent Diun data:

```text
/srv/shreyws/services/diun
```

Local notification log:

```text
/srv/shreyws/services/diun/notifications.log
```

Docker container logs are also available with:

```bash
docker logs shreyws-diun
```

## Schedule

Diun checks daily at 06:00 Europe/Zurich:

```yaml
watch:
  schedule: "0 6 * * *"
```

The Diun container uses `restart: unless-stopped`, so the schedule survives reboots.

## Opt-In Model

Diun is configured with:

```yaml
providers:
  docker:
    watchByDefault: false
```

Only containers with this Docker label are monitored:

```yaml
- diun.enable=true
```

This avoids reporting unrelated containers from other experiments or services outside the ShreyWS infrastructure repository.

## Image Pinning Audit

Current image classifications:

| Project | Service | Image | Classification |
| --- | --- | --- | --- |
| authentik | postgresql | `postgres:16-alpine` | major-version floating |
| authentik | server | `ghcr.io/goauthentik/server:2026.5.3` | fixed version |
| authentik | proxy | `ghcr.io/goauthentik/proxy:2026.5.3` | fixed version |
| authentik | worker | `ghcr.io/goauthentik/server:2026.5.3` | fixed version |
| diun | diun | `crazymax/diun:4.33.0` | fixed version |
| grafana | grafana | `grafana/grafana:latest` | latest / unbounded |
| homepage | homepage | `ghcr.io/gethomepage/homepage:latest` | latest / unbounded |
| monitoring | prometheus | `prom/prometheus:v3.13.0` | fixed version |
| monitoring | node-exporter | `prom/node-exporter:v1.11.1` | fixed version |
| monitoring | backup-metrics | `alpine:3.20` | minor-version floating |
| monitoring | cadvisor | `gcr.io/cadvisor/cadvisor:latest` | latest / unbounded |
| traefik | traefik | `traefik:v3.6.1` | fixed version |

Recommended future pinning:

- Pin Grafana to a tested major/minor version before applying future updates.
- Prometheus and Node Exporter are pinned to the versions that were already running when update management was introduced.
- Pin cAdvisor to a known-good release if the `latest` tag becomes noisy or unstable.
- Keep Authentik fixed-version pinned and upgrade it deliberately because it is now part of the authentication path.
- Keep Traefik fixed-version pinned and upgrade deliberately because it is the HTTPS entrypoint.

No broad pinning rewrite was done. Diun, Prometheus, and Node Exporter are fixed-version pinned; the remaining `latest` references are documented above as future pinning candidates.

## Manual Update Check

Run Diun immediately without waiting for the next schedule:

```bash
cd /srv/shreyws/infra/compose/diun
docker compose run --rm diun serve --config /diun.yml
```

Stop it with `Ctrl+C` after the check completes, or use a timeout for a one-shot operational check:

```bash
timeout 90 docker compose run --rm diun serve --config /diun.yml
```

View recent Diun logs:

```bash
docker logs --tail=200 shreyws-diun
```

View local notification events:

```bash
sudo tail -n 200 /srv/shreyws/services/diun/notifications.log
```

Generate a current image audit report:

```bash
/srv/shreyws/infra/scripts/shreyws-container-image-audit
```

The audit script reports:

- Compose project
- service
- container
- configured image reference
- pinning classification
- current local digest
- current remote digest
- whether a newer digest appears to exist

Install a convenience symlink only if desired:

```bash
sudo ln -sfn /srv/shreyws/infra/scripts/shreyws-container-update /usr/local/sbin/shreyws-container-update
```

The repository path is the canonical command and does not require this symlink.

## Review an Update

Check Diun output first:

```bash
docker logs --tail=200 shreyws-diun
```

Then inspect the relevant Compose file and release notes for the image you plan to update.

Do not update every stack at once. Pick one Compose project.

## Dry Run

The update script supports a dry-run stage that records current state and verifies the selected Compose project without pulling or restarting anything:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update --dry-run grafana
```

or:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update --dry-run --compose-dir /srv/shreyws/infra/compose/grafana
```

## Apply an Update

Apply an update to one selected Compose project:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update grafana
```

The script:

1. Verifies Docker access.
2. Validates the selected Compose file.
3. Shows current running images.
4. Records previous image state under `/srv/shreyws/infra/logs/container-updates/`.
5. Writes a rollback override file using previous image digests where available.
6. Pulls images for the selected Compose project only.
7. Shows which local image tags changed.
8. Requires `yes` before running `docker compose up -d`.
9. Verifies container state afterward.
10. Shows recent logs for unhealthy containers.

For automation after human review, use:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update --yes grafana
```

Use `--yes` only when you have already reviewed the planned update.

## Rollback

Every non-dry-run update records rollback metadata under:

```text
/srv/shreyws/infra/logs/container-updates/YYYYMMDD-HHMMSS-project/
```

Rollback example:

```bash
sudo /srv/shreyws/infra/scripts/shreyws-container-update --rollback /srv/shreyws/infra/logs/container-updates/YYYYMMDD-HHMMSS-grafana
```

Rollback uses the generated `rollback-compose.override.yaml` file to recreate the selected project with previously recorded image digests.

Rollback is only as good as the recorded image digests. If an image has no repository digest, the script records that limitation and does not pretend rollback is fully reliable for that service.

## Health Verification

After applying or rolling back an update:

```bash
docker compose ps
docker ps
docker logs --tail=100 <container>
```

For browser-facing services, unauthenticated checks should redirect to Authentik:

```bash
curl -kI https://shreyws.tail1591fa.ts.net/homepage/
curl -kI https://shreyws.tail1591fa.ts.net/grafana/
curl -kI https://shreyws.tail1591fa.ts.net/prometheus/
curl -kI https://shreyws.tail1591fa.ts.net/docker/
```

These checks prove forward-auth is still active. Full page rendering after login still requires an authenticated browser session.

## Optional External Notifications

The initial deployment intentionally uses local logs only. No email, Discord, Slack, Telegram, or webhook credential is required.

External notifications can be added later by extending `compose/diun/diun.yml` with one of Diun's notification backends and storing any credentials in an ignored `.env` file or another existing secret pattern. Do not commit notification credentials.

## Failure Modes

- If Diun cannot read Docker metadata, check the Docker socket mount and container logs.
- If registry checks fail, check outbound network access and registry rate limits.
- If Diun is noisy for an intentionally floating image, remove `diun.enable=true` from that service or pin the image more tightly.
- If an update fails health checks, use the recorded rollback directory immediately.
- If Authentik or Traefik are being updated, keep an SSH session open and update only that single project.
