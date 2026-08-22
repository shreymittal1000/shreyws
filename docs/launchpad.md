# ShreyWS Launchpad

Launchpad is the owner-only application control plane at:

```text
https://shreyws.tail1591fa.ts.net/launchpad/
```

It provides host capacity, managed-application status, resource usage, logs,
audited lifecycle actions, and validated application creation from an approved
image or an HTTPS Git repository containing a root `Dockerfile`.

## Security boundary

Launchpad controls Docker and therefore has platform-level authority. Its direct
Docker socket mount is intentional and materially privileged. The browser route
must remain behind Authentik and limited to the exact `shreyws-owners` group.
Do not expose Launchpad publicly. Do not grant friend/family mutation access in
version 0.1.

Workloads receive no host ports, Docker socket, host network, privileged mode,
or Linux capabilities. They receive explicit RAM, CPU and PID limits and a
dedicated Docker network. Persistent data is limited to:

```text
/srv/shreyws/services/launchpad/apps/<name>/data
```

Removing an application preserves this directory. Manual deletion is required
for destructive state removal.

Image deployments are restricted to `APPROVED_IMAGES` in `launchpad.py`. Git
deployments are owner-trusted code and accept only HTTPS repositories hosted on
GitHub, GitLab or Codeberg. Git repositories must contain a root `Dockerfile`.

## Networking

The optional fixed IP is an internal address inside `172.30.0.0/16`; it is not a
LAN or public address. Automatic addressing is recommended. Each app receives a
dedicated `launchpad_app_<name>` network, which Traefik joins for routing.

Without a domain, the internal route is:

```text
https://shreyws.tail1591fa.ts.net/apps/<name>/
```

Public deployments are rejected until `LAUNCHPAD_PUBLIC_ENABLED=true` and a
public ingress/domain design is configured.

## Secrets

Secret values are not returned by the API or rendered in the UI. Version 0.1
stores them in mode `0600` files under the app control directory and passes
them as container environment variables. Docker administrators can see
container environment values. External secret-manager integration remains a
future improvement.

The requested persistent-storage size is currently a reservation used for
capacity planning. Bind-mounted directories on the host filesystem do not have
independent hard quotas. Launchpad reports assigned versus physical capacity;
filesystem/project quota enforcement is a later storage phase.

## Assistant integration

The Codex operator runs in a separate container with no Docker socket and a
read-only infrastructure checkout. Codex CLI is installed for the `shreyws`
account using OpenAI's standalone installer and authenticated through its
official device flow. Chat is advisory in version 0.1: it can inspect and
explain the platform but cannot mutate Docker or invoke Launchpad lifecycle
operations. A future plan/confirmation layer may translate chat intent into the
same validated API operations.

Launchpad displays Codex version and authentication state. The CLI does not
provide a stable machine-readable value for remaining ChatGPT subscription
credits, so the UI must not invent one. API-provider usage can be added later
through the official organization usage/cost endpoints.

## Deploy

```bash
docker network create launchpad_frontend
docker network connect launchpad_frontend shreyws-traefik
mkdir -p /srv/shreyws/services/launchpad/apps
cd /srv/shreyws/infra/compose/launchpad
docker compose up -d --build
```

## Test

```bash
python3 compose/launchpad/app/test_launchpad.py
docker compose -f compose/launchpad/compose.yaml config --quiet
```
