# ShreyWS Homepage

Homepage is the central landing page for ShreyWS.

URL:

```text
https://shreyws.tail1591fa.ts.net/
```

The service is deployed as a dedicated Docker Compose stack:

```text
/srv/shreyws/infra/compose/homepage
```

## Routing

Homepage is routed by Traefik on the root host rule:

```text
Host(`shreyws.tail1591fa.ts.net`)
```

The router priority is intentionally low so existing path-based routes such as `/grafana`, `/prometheus`, `/docker`, `/containers`, and `/cadvisor` continue to win.

## Configuration

Homepage configuration lives in:

```text
/srv/shreyws/infra/compose/homepage/config
```

Important files:

- `settings.yaml`: page title, layout, theme, and search behavior.
- `services.yaml`: visible service cards and widgets.
- `widgets.yaml`: top-page information widgets.
- `bookmarks.yaml`: currently empty.
- `docker.yaml`: currently empty because direct Docker socket access is not enabled.

The config directory is mounted into the container at `/app/config`.

## Widgets

The dashboard uses existing internal endpoints only:

- Prometheus service widget for target status.
- Prometheus Metric widgets for Traefik, cAdvisor, Node Exporter, backups, Docker summary, and system health.
- Site monitors against internal service URLs where a simple HTTP check is useful.

No API tokens or secrets are currently required.

Docker socket integration is intentionally not enabled. The existing platform summary mentions Docker Socket Proxy, but no socket proxy container is currently running. Directly mounting `/var/run/docker.sock` would give Homepage broad Docker control and would be a security regression.

## Adding Future Services

To add a real service later:

1. Add or update the service's own Compose stack.
2. Route it through Traefik using the existing `traefik_default` network convention.
3. Add a card under the appropriate group in `compose/homepage/config/services.yaml`.
4. Prefer a safe internal widget URL if Homepage supports that service.
5. Use environment variables or files for API tokens instead of committing secrets.
6. Recreate Homepage:

```bash
cd /srv/shreyws/infra/compose/homepage
docker compose up -d
```

7. Verify the card, widget, and existing routes.

## Operations

Validate the Compose file:

```bash
docker compose -f /srv/shreyws/infra/compose/homepage/compose.yaml config
```

Check health:

```bash
docker inspect --format '{{.State.Health.Status}}' shreyws-homepage
```

Recreate the container:

```bash
cd /srv/shreyws/infra/compose/homepage
docker compose up -d --force-recreate
```
