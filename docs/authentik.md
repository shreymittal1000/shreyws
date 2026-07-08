# ShreyWS Authentik

Authentik is the central identity provider for ShreyWS.

URL:

```text
https://shreyws.tail1591fa.ts.net/authentik/
```

Initial setup URL:

```text
https://shreyws.tail1591fa.ts.net/authentik/setup
```

In the current Authentik release, that setup entrypoint redirects to the active initial setup flow under:

```text
https://shreyws.tail1591fa.ts.net/authentik/if/flow/initial-setup/
```

## Stack Layout

Compose stack:

```text
/srv/shreyws/infra/compose/authentik
```

Services:

- `server`: Authentik web/API service, routed through Traefik.
- `worker`: Authentik background worker.
- `postgresql`: internal PostgreSQL database.

Redis is not deployed in this first stack because the current official Authentik Docker Compose file for 2026.5.3 does not include Redis.

The stack follows the official Authentik Docker Compose pattern for the current release, with local ShreyWS adjustments:

- no host port publishing;
- Traefik handles HTTPS;
- PostgreSQL is internal only;
- the worker does not mount the Docker socket for this first deployment.

## Secrets

Secrets are stored in:

```text
/srv/shreyws/infra/compose/authentik/.env
```

This file is intentionally ignored by Git.

Generated values:

- `PG_PASS`: PostgreSQL password.
- `AUTHENTIK_SECRET_KEY`: Authentik secret key.

Do not commit `.env`.

## Persistent Data

Persistent data:

- PostgreSQL data: Docker volume `authentik_database`.
- Authentik media/runtime data: `compose/authentik/data`.
- Authentik certificates: `compose/authentik/certs`.
- Custom templates: `compose/authentik/custom-templates`.

## First Admin Setup

After the containers are healthy, open:

```text
https://shreyws.tail1591fa.ts.net/authentik/setup
```

Set the password for the default `akadmin` user. Do not use a weak or reused password.

After setup, sign in at:

```text
https://shreyws.tail1591fa.ts.net/authentik/
```

## Admin Recovery

If admin access is lost, use Authentik's management command from the server container to reset a user's password.

Example:

```bash
cd /srv/shreyws/infra/compose/authentik
docker compose exec server ak changepassword akadmin
```

If the `akadmin` account was removed, create or repair admin access from inside the server container using Authentik management commands. Keep a current backup before making account-level recovery changes.

## Future SSO / Forward Auth Plan

This deployment does not put existing ShreyWS services behind Authentik yet.

Later, Authentik can protect services such as Homepage, Grafana, Prometheus, cAdvisor, Forgejo, and Open WebUI with a Proxy Provider and an embedded outpost or a managed outpost.

Typical future flow:

1. Create an Authentik application for the service.
2. Create a Proxy Provider for that application.
3. Deploy or configure the Authentik outpost.
4. Add Traefik forward-auth middleware labels to the target service.
5. Verify the service still works, then repeat one service at a time.

Do this incrementally. Do not place all services behind Authentik in one change.

## Operations

Validate Compose:

```bash
cd /srv/shreyws/infra/compose/authentik
docker compose config --quiet
```

Start:

```bash
cd /srv/shreyws/infra/compose/authentik
docker compose up -d
```

Check health:

```bash
docker inspect --format '{{.State.Health.Status}}' shreyws-authentik-postgresql
docker inspect --format '{{.State.Health.Status}}' shreyws-authentik-server
docker ps --filter name=shreyws-authentik
```

Check logs:

```bash
cd /srv/shreyws/infra/compose/authentik
docker compose logs --tail=100
```
