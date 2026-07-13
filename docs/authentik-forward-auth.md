# Authentik Forward Auth for ShreyWS

## Status

Forward authentication is configured for cAdvisor only.

Prometheus, Grafana, and Homepage are intentionally not protected yet because the cAdvisor browser login and return flow still needs to be verified interactively with a real Authentik user session.

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
- Outpost: `authentik Embedded Outpost`
- Policy: any successfully authenticated Authentik user may access the protected services.

## Outpost Deployment

The embedded outpost database object exists, but the Authentik server container did not serve `/outpost.goauthentik.io/auth/traefik`; it returned `404` even after provider assignment and an Authentik server restart.

Because of that, ShreyWS uses the official Authentik proxy outpost sidecar:

```text
shreyws-authentik-proxy
```

The sidecar:

- uses `ghcr.io/goauthentik/proxy:2026.5.3`;
- publishes no host ports;
- joins `traefik_default` so Traefik can reach it;
- joins `authentik_authentik_internal` so it can use PostgreSQL for proxy sessions;
- stores its token in ignored `compose/authentik/.env` as `AUTHENTIK_TOKEN`.

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
  - `/docker`
  - `/containers`
  - `/static`
  - `/podman`

Not yet protected:

- Prometheus
- Grafana
- Homepage

## Configuration Locations

Tracked files:

- `compose/authentik/compose.yaml`
- `compose/monitoring/compose.yaml`
- `docs/authentik-forward-auth.md`

Ignored secret/runtime files:

- `compose/authentik/.env`
- `logs/authentik-forward-auth-20260713-152313.log`
- `logs/CHANGELOG.md`

Backups created:

- `compose/authentik/compose.yaml.bak.20260713-153543`
- `compose/authentik/.env.bak.*`
- `compose/monitoring/compose.yaml.bak.20260713-174441`

## Verification Performed

Server-side verification completed:

- Authentik server healthy.
- Authentik worker healthy.
- Authentik PostgreSQL healthy.
- Authentik proxy sidecar healthy.
- Authentik proxy outpost route returns `204` at `/outpost.goauthentik.io/ping`.
- cAdvisor container healthy after label-only recreation.
- Unauthenticated cAdvisor requests return `302` to the external Authentik login URL.
- Redirect-following reaches the Authentik authentication flow without a redirect loop.
- Homepage, Prometheus, Grafana, and Authentik still respond on their existing URLs.

Not completed automatically:

- Browser login with `akadmin`.
- Return from Authentik to cAdvisor after login.
- cAdvisor page and asset loading after authenticated session establishment.

## Manual Browser Validation

Use a private/incognito browser window to avoid stale cookies.

1. Open:

   ```text
   https://shreyws.tail1591fa.ts.net/docker
   ```

2. Confirm the browser redirects to:

   ```text
   https://shreyws.tail1591fa.ts.net/authentik/
   ```

3. Log in with an existing Authentik user, currently expected to be `akadmin`.

4. Confirm the browser returns to:

   ```text
   https://shreyws.tail1591fa.ts.net/docker
   ```

5. Confirm cAdvisor loads normally.

6. Click into at least one container page and confirm assets load.

7. Open:

   ```text
   https://shreyws.tail1591fa.ts.net/containers
   https://shreyws.tail1591fa.ts.net/cadvisor
   ```

8. Confirm both are accessible without another login prompt in the same browser session.

Do not protect Prometheus, Grafana, or Homepage until this succeeds.

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

## Temporarily Disable Authentication for cAdvisor

Restore the monitoring Compose backup and recreate only cAdvisor:

```bash
cd /srv/shreyws/infra/compose/monitoring
cp -a compose.yaml.bak.20260713-174441 compose.yaml
docker compose up -d --no-deps cadvisor
```

This leaves Authentik and the proxy sidecar intact.

## Full Rollback

Restore Authentik and monitoring Compose backups:

```bash
cd /srv/shreyws/infra/compose/authentik
cp -a compose.yaml.bak.20260713-153543 compose.yaml
docker compose up -d --remove-orphans

cd /srv/shreyws/infra/compose/monitoring
cp -a compose.yaml.bak.20260713-174441 compose.yaml
docker compose up -d --no-deps cadvisor
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

- cAdvisor is the only protected service so far.
- Full browser login and return-to-service validation is pending.
- Authentik is still path-prefixed at `/authentik/`; do not change service base paths as part of forward-auth rollout.
- Traefik still uses the existing self-signed/default certificate; this task intentionally did not modify TLS certificates.
