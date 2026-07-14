# Protect Remaining Services with Authentik Forward Auth

Date: 2026-07-14

## Summary

Homepage, Grafana, and Prometheus were attached to the existing working `authentik-forward-auth@docker` Traefik middleware. cAdvisor remained protected and unchanged. Authentik and `/outpost.goauthentik.io/` remain unprotected so login and callback routing cannot loop.

## What Was Inspected

- Docker Compose projects and running containers.
- Docker networks.
- Compose labels for Authentik, cAdvisor, Homepage, Grafana, and Prometheus.
- Authentik proxy outpost runtime state and recent logs.
- Traefik recent logs.
- Homepage widget configuration.
- Grafana Prometheus datasource.
- Prometheus scrape configuration.

## Existing Working cAdvisor Flow

cAdvisor uses dedicated Traefik routers for `/cadvisor`, `/docker`, `/containers`, `/static`, and `/podman`. Each router uses:

```text
authentik-forward-auth@docker
```

Traefik sends unauthenticated browser requests to:

```text
http://shreyws-authentik-proxy:9000/outpost.goauthentik.io/auth/traefik
```

The standalone proxy outpost redirects browsers to:

```text
https://shreyws.tail1591fa.ts.net/authentik/
```

The proxy outpost remains standalone and reports `embedded=false`.

## Files Changed

- `compose/homepage/compose.yaml`
- `compose/grafana/compose.yaml`
- `compose/monitoring/compose.yaml`
- `docs/authentik-forward-auth.md`
- `logs/CHANGELOG.md`
- `docs/change-logs/authentik-forward-auth-remaining-20260714-004356.md`

## Configuration Changes

Homepage:

- Added `authentik-forward-auth@docker` to the existing host root router.
- Added `homepage-path` router for `PathPrefix('/homepage')`.
- Added `homepage-strip-prefix` middleware.
- Ordered Homepage `/homepage` middlewares as `authentik-forward-auth@docker,homepage-strip-prefix`.

This preserves the existing root-based Homepage app while making `/homepage/` usable as a protected public URL.

Grafana:

- Added `authentik-forward-auth@docker` to the existing `/grafana` router.
- Preserved `GF_SERVER_ROOT_URL=https://shreyws.tail1591fa.ts.net/grafana/`.
- Preserved `GF_SERVER_SERVE_FROM_SUB_PATH=true`.

Prometheus:

- Added `authentik-forward-auth@docker` to the existing `/prometheus` router.
- Preserved `--web.external-url=https://shreyws.tail1591fa.ts.net/prometheus/`.
- Preserved `--web.route-prefix=/prometheus`.

## Middleware Ordering

Grafana and Prometheus have no other router middlewares, so the forward-auth middleware is the only middleware.

Homepage `/homepage` uses forward-auth before strip-prefix. That order matters because Authentik must see the browser's original URL and return the user to `/homepage/`. Only after Authentik allows the request does Traefik strip `/homepage` for the root-based Homepage backend.

## Commands Executed

Representative commands:

```bash
cd /srv/shreyws/infra
git status --short --ignored=matching
docker compose ls
docker ps
docker network ls
docker compose config --quiet
docker compose up -d --no-deps homepage
docker compose up -d --no-deps grafana
docker compose up -d --no-deps prometheus
curl -k -I https://shreyws.tail1591fa.ts.net/homepage/
curl -k -I https://shreyws.tail1591fa.ts.net/grafana/
curl -k -I https://shreyws.tail1591fa.ts.net/prometheus/
curl -k -I https://shreyws.tail1591fa.ts.net/docker/
curl -k -I https://shreyws.tail1591fa.ts.net/authentik/
curl -k -I https://shreyws.tail1591fa.ts.net/outpost.goauthentik.io/ping
```

## Test Results

Server-side logged-out checks:

- `/homepage/` returns `302` to the external Authentik authorize URL.
- `/grafana/` returns `302` to the external Authentik authorize URL.
- `/prometheus/` returns `302` to the external Authentik authorize URL.
- `/docker/` returns `302` to the external Authentik authorize URL.
- `/authentik/` remains reachable and routes to Authentik's own authentication flow.
- `/outpost.goauthentik.io/ping` returns `204`.

Redirect safety checks:

- No redirect target used an internal Docker hostname.
- No redirect target used port `9000`.
- No redirect target downgraded to plain HTTP.
- Redirect following reached the Authentik login flow with HTTP 200 and did not loop.

Backend checks:

- Homepage root returns HTTP 200 inside the container.
- Grafana `/grafana/` reaches its login page inside the container.
- Prometheus `/prometheus/-/healthy` returns HTTP 200 inside the container.

Log checks:

- Recent Traefik logs did not show missing middleware errors after this rollout.
- Recent Authentik proxy logs did not show the old `/dev/shm/authentik.sock` embedded-outpost error.
- Grafana logs still show unrelated plugin auto-install permission errors on startup; Grafana remained reachable internally.

## Widgets and Internal Integrations

Prometheus scrapes use internal Docker service names. Grafana's datasource uses `http://prometheus:9090/prometheus`. Homepage widgets and site monitors use internal Docker URLs for Prometheus, Grafana, and cAdvisor. These paths do not require browser SSO and were not changed.

## Security Implications

- Browser-facing Homepage, Grafana, Prometheus, and cAdvisor access now requires a successful Authentik session.
- Authentik and the outpost callback route remain outside forward-auth by design.
- No new host ports were exposed.
- No Tailscale or TLS settings were changed.
- If `shreyws-authentik-proxy` is stopped, protected routers depend on its Docker-defined middleware and may fail closed until the proxy is healthy again.

## Rollback Procedure

Restore one service at a time from the timestamped backup created before this change.

Homepage:

```bash
cd /srv/shreyws/infra/compose/homepage
cp -a compose.yaml.bak-YYYYMMDD-HHMMSS compose.yaml
docker compose up -d --no-deps homepage
```

Grafana:

```bash
cd /srv/shreyws/infra/compose/grafana
cp -a compose.yaml.bak-YYYYMMDD-HHMMSS compose.yaml
docker compose up -d --no-deps grafana
```

Prometheus:

```bash
cd /srv/shreyws/infra/compose/monitoring
cp -a compose.yaml.bak-YYYYMMDD-HHMMSS compose.yaml
docker compose up -d --no-deps prometheus
```

This leaves Authentik data and the standalone proxy outpost intact.

## Remaining Manual Browser Test

Use a fresh incognito window and test:

```text
https://shreyws.tail1591fa.ts.net/homepage/
https://shreyws.tail1591fa.ts.net/grafana/
https://shreyws.tail1591fa.ts.net/prometheus/
https://shreyws.tail1591fa.ts.net/docker/
```

Expected behavior:

1. The service redirects to Authentik.
2. Login succeeds with an existing Authentik user.
3. The browser returns to the originally requested service URL.
4. The page, CSS, JavaScript, images, links, refresh, and direct deep links work.

This authenticated browser flow cannot be fully verified from the server without a real browser session.

## Remaining Risks

- Browser-authenticated return and asset loading still need human confirmation.
- The Homepage root URL `/` is also protected because the existing Homepage router is host-wide.
- The `/homepage` route is implemented at Traefik only; the Homepage application itself remains root-based.
