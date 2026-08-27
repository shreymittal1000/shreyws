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
deployments are owner-trusted code and accept HTTPS repository identifiers on
GitHub, GitLab or Codeberg. Git repositories must contain a root `Dockerfile`.

## Git repositories and deploy keys

Public repositories clone over HTTPS. Private GitHub repositories use a unique
Ed25519 deploy key stored under the application's mode-`0700` control directory;
the private key is mode `0600`, is never returned by the API, and is never
committed to this repository. Launchpad obtains GitHub's current SSH host keys
from `https://api.github.com/meta` over verified HTTPS, stores them outside Git,
and invokes SSH with `StrictHostKeyChecking=yes` and `IdentitiesOnly=yes`.

Private-repository workflow:

1. Enter the application name and GitHub HTTPS repository URL.
2. Select **Prepare private-repo key**.
3. Copy the displayed public key into GitHub repository **Settings → Deploy
   keys**. Leave write access disabled.
4. Select **Load branches**, choose a branch, and deploy.

The Git application panel can list and switch branches, rebuild the container,
show the deploy-key fingerprint, rotate keys, revoke the local private key, and
check for upstream commits. Rotation generates a new key; add the new public key
to GitHub and remove the old one. Revocation destroys the local private key, but
the public key should also be removed from GitHub repository settings.

Launchpad checks Git applications for upstream commits every 15 minutes and
reports whether an update is available. Checks never deploy code automatically.
Deployment remains an explicit **Update** or **Switch & deploy** action. GitLab
and Codeberg remain available for public HTTPS repositories; per-app private
deploy-key support is currently limited to GitHub.

## Networking

The optional fixed IP is an internal address inside `172.30.0.0/16`; it is not a
LAN or public address. Automatic addressing is recommended. Each app receives a
dedicated `launchpad_app_<name>` network, which Traefik joins for routing.

Without a domain, the internal route is:

```text
https://shreyws.tail1591fa.ts.net/apps/<name>/
```

Public deployments use the isolated Cloudflare Tunnel design documented in
[`public-ingress.md`](public-ingress.md). They remain rejected until the tunnel
is active and the requested hostname belongs to
`LAUNCHPAD_PUBLIC_DOMAIN_SUFFIXES`. There is no separate global enable switch:
blank domain means internal, while an allowlisted domain means public.

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
