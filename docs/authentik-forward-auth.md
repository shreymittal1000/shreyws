# Authentik Forward Auth for ShreyWS

## Status

Forward authentication is configured for:

- cAdvisor
- Prometheus
- Grafana
- Homepage

Authentik itself and the proxy outpost callback paths are intentionally not protected by forward-auth.

## Architecture

Request flow:

```text
Browser
  -> Traefik HTTPS router
  -> authentik-forward-auth middleware
  -> Authentik proxy outpost
  -> protected service
```

Authentik itself remains unprotected by forward-auth.

## Authentik Objects

- Application: `ShreyWS Internal Services`
- Application slug: `shreyws-internal-services`
- Proxy provider: `ShreyWS Forward Auth`
- Provider mode: `forward_domain`
- External host: `https://shreyws.tail1591fa.ts.net`
- Outpost: `ShreyWS Proxy Outpost`
- Policy: any successfully authenticated Authentik user may access the protected services.

## Outpost Deployment

The embedded outpost database object exists, but the Authentik server container did not serve `/outpost.goauthentik.io/auth/traefik`; it returned `404` even after provider assignment and an Authentik server restart.

The first sidecar attempt reused the embedded outpost token. That made the proxy run in embedded mode and the callback failed after login because it tried to redeem the OAuth code through `/dev/shm/authentik.sock`, which is not present in the standalone sidecar container.

Because of that, ShreyWS uses a dedicated non-embedded Authentik proxy outpost:

```text
ShreyWS Proxy Outpost
```

The provider `ShreyWS Forward Auth` is assigned to that standalone outpost, not to `authentik Embedded Outpost`.

ShreyWS runs the official Authentik proxy outpost sidecar:

```text
shreyws-authentik-proxy
```

The sidecar:

- uses `ghcr.io/goauthentik/proxy:2026.5.3`;
- publishes no host ports;
- joins `traefik_default` so Traefik can reach it;
- joins `authentik_authentik_internal` so it can use PostgreSQL for proxy sessions;
- stores the `ShreyWS Proxy Outpost` token in ignored `compose/authentik/.env` as `AUTHENTIK_TOKEN`;
- uses `AUTHENTIK_HOST=http://shreyws-authentik-server:9000/authentik/` for Docker-internal API communication;
- uses `AUTHENTIK_HOST_BROWSER=https://shreyws.tail1591fa.ts.net/authentik/` for browser-facing redirects.

## Traefik Middleware

Middleware name:

```text
authentik-forward-auth@docker
```

Forward-auth endpoint:

```text
http://shreyws-authentik-proxy:9000/outpost.goauthentik.io/auth/traefik
```

Outpost public route:

```text
https://shreyws.tail1591fa.ts.net/outpost.goauthentik.io/
```

Headers passed back to protected services:

```text
X-authentik-username
X-authentik-groups
X-authentik-entitlements
X-authentik-email
X-authentik-name
X-authentik-uid
X-authentik-jwt
X-authentik-meta-jwks
X-authentik-meta-outpost
X-authentik-meta-provider
X-authentik-meta-app
X-authentik-meta-version
```

## Protected Services

Currently protected:

- cAdvisor:
  - `/cadvisor`
  - `/cadvisor/`
  - `/docker`
  - `/containers`
  - `/static`
  - `/podman`
- Prometheus
  - `/prometheus/`
- Grafana
  - `/grafana/`
- Homepage
  - `/`
  - `/homepage/`

Homepage is still configured as a root-based application. The `homepage` router protects the existing host root route, and the `homepage-path` router protects `/homepage` as a compatibility URL. The `/homepage` router applies forward-auth first and then strips `/homepage` before sending the request to the Homepage container.

Not protected:

- Authentik:
  - `/authentik/`
- Authentik proxy outpost:
  - `/outpost.goauthentik.io/`

## Configuration Locations

Tracked files:

- `compose/authentik/compose.yaml`
- `compose/homepage/compose.yaml`
- `compose/grafana/compose.yaml`
- `compose/monitoring/compose.yaml`
- `docs/authentik-forward-auth.md`
- `docs/change-logs/`

Ignored secret/runtime files:

- `compose/authentik/.env`
- `logs/authentik-forward-auth-20260713-152313.log`
- `logs/CHANGELOG.md`

Backups created:

- `compose/authentik/compose.yaml.bak.20260713-153543`
- `compose/authentik/.env.bak.*`
- `compose/monitoring/compose.yaml.bak.20260713-174441`
- `compose/homepage/compose.yaml.bak-*`
- `compose/grafana/compose.yaml.bak-*`
- `compose/monitoring/compose.yaml.bak-*`

## Middleware Ordering

Most protected routers use only:

```text
authentik-forward-auth@docker
```

Homepage's `/homepage` compatibility router uses:

```text
authentik-forward-auth@docker,homepage-strip-prefix
```

Forward-auth runs first so Authentik sees and preserves the original browser URL. The strip-prefix middleware runs only after the user is allowed through, so the root-based Homepage container receives a path it can serve.

## Internal Integrations

Prometheus scrapes internal Docker service names and does not use the authenticated public URL.

Grafana's Prometheus datasource uses:

```text
http://prometheus:9090/prometheus
```

Homepage widgets and site monitors use internal Docker service URLs such as:

```text
http://prometheus:9090/prometheus
http://grafana:3000/grafana/login
http://cadvisor:8080/containers/
```

These internal paths avoid sending service-to-service monitoring traffic through interactive browser SSO.

## Verification Performed

Server-side verification completed:

- Authentik server healthy.
- Authentik worker healthy.
- Authentik PostgreSQL healthy.
- Authentik proxy sidecar healthy.
- Authentik proxy sidecar reports `embedded=false`.
- Authentik proxy outpost route returns `204` at `/outpost.goauthentik.io/ping`.
- cAdvisor container healthy.
- Homepage container healthy after label-only recreation.
- Grafana and Prometheus containers running after label-only recreation.
- Unauthenticated cAdvisor, Homepage, Grafana, and Prometheus requests return `302` to the external Authentik login URL.
- Redirect-following reaches the Authentik authentication flow without a redirect loop.
- The previous callback error `dial unix /dev/shm/authentik.sock` no longer appears after switching to `ShreyWS Proxy Outpost`.
- Authentik and the proxy outpost remain reachable without forward-auth loops.
- Homepage, Prometheus, Grafana, and cAdvisor backend paths respond internally.

Not completed automatically:

- Browser login with an existing Authentik user for each protected service.
- Return from Authentik to each protected service after login.
- Page and asset loading after authenticated session establishment.

## Manual Browser Validation

Use a private/incognito browser window to avoid stale cookies.

1. Open each URL in a private/incognito browser window:

   ```text
   https://shreyws.tail1591fa.ts.net/homepage/
   https://shreyws.tail1591fa.ts.net/grafana/
   https://shreyws.tail1591fa.ts.net/prometheus/
   https://shreyws.tail1591fa.ts.net/docker/
   ```

2. Confirm each one redirects to:

   ```text
   https://shreyws.tail1591fa.ts.net/authentik/
   ```

3. Log in with an existing Authentik user, currently expected to be `akadmin`.

4. Confirm the browser returns to the originally requested URL.

5. Confirm the page, assets, links, and refresh behavior work for each service.

6. Confirm Authentik remains directly reachable at `/authentik/` and the outpost ping remains `204`.

## Add Another Protected Service

1. Back up the relevant Compose file.
2. Add this label to the service's Traefik router:

   ```yaml
   - traefik.http.routers.<router-name>.middlewares=authentik-forward-auth@docker
   ```

3. Recreate only that service:

   ```bash
   docker compose up -d --no-deps <service>
   ```

4. Verify unauthenticated redirect.
5. Verify browser login and return.
6. Verify service assets and internal links.

## Temporarily Disable Authentication for One Service

Restore that service's Compose backup and recreate only the affected container.

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

This leaves Authentik and the proxy sidecar intact.

## Full Rollback

Restore Authentik and monitoring Compose backups only if you want to remove the forward-auth infrastructure itself:

```bash
cd /srv/shreyws/infra/compose/authentik
cp -a compose.yaml.bak.20260713-153543 compose.yaml
docker compose up -d --remove-orphans
```

Remove the Authentik objects if you want to fully undo the Authentik-side configuration:

```bash
docker exec shreyws-authentik-server ak shell -c "
from authentik.core.models import Application
from authentik.providers.proxy.models import ProxyProvider
Application.objects.filter(slug='shreyws-internal-services').delete()
ProxyProvider.objects.filter(name='ShreyWS Forward Auth').delete()
"
```

Do not delete Authentik volumes or PostgreSQL data.

## Troubleshooting

- If protected services return `500`, check that `shreyws-authentik-proxy` is healthy.
- If redirects point to `shreyws-authentik-server:9000`, check the outpost `authentik_host` and `authentik_host_browser` values.
- If `/outpost.goauthentik.io/ping` does not return `204`, check Traefik labels on `shreyws-authentik-proxy`.
- If login works but return fails, check callback URL handling at `/outpost.goauthentik.io/callback`.
- If a service's assets fail, make sure every asset path router for that service has the same middleware.

## Known Limitations

- Full browser login and return-to-service validation must be confirmed interactively with an Authentik user session.
- Authentik is still path-prefixed at `/authentik/`; do not change service base paths as part of forward-auth rollout.
- Traefik still uses the existing self-signed/default certificate; this task intentionally did not modify TLS certificates.
- If `shreyws-authentik-proxy` is stopped, protected routers depend on middleware defined by that container and may fail closed until the proxy is healthy again.
