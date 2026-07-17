# ShreyWS Agent Platform Architecture

Last updated: 2026-07-16

This document prepares ShreyWS for a controlled owner-only agent pilot. It does not approve deployment of arbitrary code execution, browser automation, shell services, or guest workloads.

The first owner-only pilot is documented in `docs/owner-agent.md`. It uses a purpose-built notes/summarization service with a mock model backend and no command execution.

## Goals

- Define workload trust classes and security boundaries.
- Reduce avoidable lateral movement for future applications.
- Define the first owner-only agent architecture before choosing an agent framework.
- Set default expectations for authentication, secrets, monitoring, logging, updates, backups and incident response.

## Non-Goals

- Deploying an agent platform.
- Allowing friend/family access.
- Running untrusted code.
- Activating a host firewall remotely.
- Replacing Docker with a new virtualization stack in this phase.

## Trust Classes

### A. Platform Infrastructure

Examples: Traefik, Authentik, Prometheus, Alertmanager, Grafana, Loki, Alloy, cAdvisor, Node Exporter, backup helpers.

- Privileges: only what the component requires; host mounts and Docker socket access require explicit justification.
- Network: infrastructure networks only; databases must stay on private backend networks.
- Filesystem: bind mounts limited to config/state paths; host-wide mounts only for exporters that require them.
- Secrets: platform secrets only; never shared with applications.
- Resources: enough headroom to stay healthy during application failures.
- Auth: infrastructure browser routes require Authentik unless they are internal-only.
- Logging: all container logs ingested; secret redaction required.
- Backup: configs and critical state included; bulk disposable logs excluded.
- Updates: pinned images; reviewed one project at a time.
- Incident response: platform compromise is high severity; isolate affected service and preserve logs.

### B. Owner-Trusted Applications

Examples: pilot workload, future personal apps, first owner-only agent with narrowly controlled tools.

- Privileges: no privileged mode, no Docker socket, no host networking, no broad host mounts.
- Network: frontend network only plus private app network if needed; no default access to platform databases.
- Filesystem: dedicated `/srv/shreyws/services/<workload>` path; read-only config; tmpfs scratch.
- Secrets: service-specific, least-privileged, read-only secret mounts only.
- Resources: explicit CPU, memory and PID limits.
- Auth: Authentik owner-only group.
- Logging: Docker logs in Loki with low-cardinality labels.
- Backup: persistent state included unless explicitly disposable.
- Updates: Diun notification plus reviewed update script.
- Incident response: stop the Compose project, preserve logs and data, rotate service-specific tokens if exposed.

### C. Guest Or Untrusted Workloads

Examples: friend/family services, user-provided code, agents browsing hostile sites, agents executing commands, arbitrary uploads or prompts.

- Privileges: Docker alone is not a strong boundary for intentionally hostile code. Do not run this class on the current Docker-only model.
- Network: default deny; no LAN, Tailscale, Docker API or platform backend access.
- Filesystem: per-user isolated storage; no shared host paths.
- Secrets: per-user, per-service, revocable credentials only.
- Resources: strict quotas and execution time limits.
- Auth: Authentik group or policy per user/service; default deny.
- Logging: audit tool execution and policy denials without storing private prompt contents by default.
- Backup: opt-in per user/service with deletion/offboarding procedure.
- Updates: owner-reviewed only.
- Incident response: assume compromise; stop workload, revoke credentials, preserve minimal audit logs.

## Current Network Topology

Most ShreyWS browser-facing and observability containers still share `traefik_default`. Authentik PostgreSQL is isolated on `authentik_authentik_internal`. Diun has its own default network but uses the Docker socket.

| Container | Networks before this phase | Needed inbound | Needed outbound | Unnecessary reachability | Change |
| --- | --- | --- | --- | --- | --- |
| Traefik | `traefik_default` | LAN/Tailscale 80/443 | routed frontends, Authentik outpost | all containers on `traefik_default` | attach to `pilot_frontend` for pilot route |
| Authentik server | `authentik_internal`, `traefik_default` | Traefik `/authentik/` | PostgreSQL, outpost callbacks | broad app network | deferred |
| Authentik proxy | `authentik_internal`, `traefik_default` | Traefik outpost paths | Authentik server | broad app network | deferred |
| Authentik worker | `authentik_internal` | none browser-facing | PostgreSQL/server internals | none obvious | no change |
| Authentik PostgreSQL | `authentik_internal` | Authentik components | none | none obvious | no change |
| Grafana | `traefik_default` | Traefik | Prometheus, Loki | alert webhooks, pilot, cAdvisor internals | deferred |
| Prometheus | `traefik_default` | Traefik, internal scrapes | all scrape targets, Alertmanager | app internals beyond scrape targets | attach to `pilot_frontend` for pilot scrape |
| Alertmanager | `traefik_default` | Prometheus | alert webhooks | unrelated frontends | deferred |
| Alert webhooks | `traefik_default` | Alertmanager | Telegram for Telegram webhook | routed app frontends | deferred |
| Loki | `traefik_default` | Alloy, Grafana, Prometheus | none | app frontends | deferred |
| Alloy | `traefik_default` | Prometheus scrape | Loki, Docker socket, journald | app frontends | deferred |
| cAdvisor | `traefik_default` | Prometheus, Traefik | host/Docker reads | app frontends | deferred |
| Node Exporter | `traefik_default` | Prometheus | host reads | app frontends | deferred |
| Homepage | `traefik_default` | Traefik | configured widgets | backend services it does not need | deferred |
| Diun | `diun_default` | none | Docker socket, registries | none at Docker-network level | no network change |
| Pilot | `traefik_default` | Traefik, Prometheus | none required | all services on `traefik_default` | moved to `pilot_frontend` |

## Revised Network Topology

This phase introduces one practical network split:

```text
pilot_frontend:
  shreyws-pilot
  shreyws-traefik
  shreyws-prometheus
```

The pilot no longer needs `traefik_default`. Traefik still keeps `traefik_default` for existing routes. Prometheus keeps `traefik_default` for existing scrape targets.

Broader segmentation remains deferred because moving Authentik, Grafana, Loki, Alertmanager and Homepage across networks at once would touch the stable authentication and observability path.

## Future Network Pattern

Default trusted application:

```text
<workload>_frontend:
  app
  traefik
  prometheus if native metrics are exposed

<workload>_backend:
  app
  database or cache, if needed
```

Rules:

- no default attachment to broad infrastructure networks,
- no published host ports,
- Traefik reaches only frontend containers,
- Prometheus reaches only metrics endpoints,
- backend databases stay private to their app.

## Docker Socket Decision

Current consumers:

- Traefik: Docker provider discovery; needs container/network/label event visibility.
- Alloy: Docker log discovery and log reads; needs container metadata and logs.
- Diun: image inventory and registry comparison; needs container/image metadata.
- cAdvisor: does not mount `/var/run/docker.sock` directly, but reads `/var/run` and Docker state for metrics.

Important: `/var/run/docker.sock:ro` is only filesystem read-only. It does not make Docker API calls read-only.

Decision: do not introduce a socket proxy in this phase. A proxy is useful only if endpoint requirements are tested per consumer. A single broad proxy would be security theatre.

Future incremental design:

1. Start with Diun because it is non-routing and easiest to roll back.
2. Provide only container/image inspect endpoints needed by Diun.
3. Observe Diun logs for missing endpoint errors.
4. Only then evaluate Alloy and Traefik separately.
5. Do not proxy cAdvisor until its Docker access pattern is explicitly tested.

## First Owner-Only Agent Threat Model

Assume a future agent may receive adversarial prompts, browse malicious sites, download files, call APIs, persist memory, execute narrowly allowed commands, be compromised through dependencies, and consume excessive resources.

First owner-only agent must:

- run as owner-trusted class B, not guest/untrusted class C,
- be behind Authentik owner-only authorization,
- have no Docker socket,
- have no host `/`, `/srv`, `/home`, device or SSH key mounts,
- use a dedicated persistent directory,
- use read-only config and secret mounts,
- use tmpfs scratch and bounded workspace/downloads,
- have explicit CPU, memory and PID limits,
- expose only a single HTTP frontend through Traefik,
- emit logs to Loki,
- expose metrics or at least container health.

First owner-only agent must not:

- manage Docker,
- access platform secrets,
- access other users' data,
- access Tailscale peers by default,
- schedule arbitrary host tasks,
- send email/messages unless a separate revocable token is created,
- receive broad personal credentials.

Command execution decision: not enabled by default. If enabled later, use a narrow allowlist of commands inside the container only, with timeouts and no host mounts.

Network egress decision: public internet may be allowed for the owner-only pilot only if needed, but LAN/Tailscale access should be considered denied by policy until nftables/proxy controls are implemented.

## Guest Workload Threat Model

Guest workloads and arbitrary-code agents are not approved on the current Docker-only boundary. They need a stronger isolation layer before deployment.

Recommendation:

- owner-only trusted agent: Docker Compose with strict controls is acceptable for a pilot.
- guest arbitrary-code workloads: use a dedicated VM or microVM model before allowing access.

## Per-User Isolation Model

Future convention:

```text
/srv/shreyws/services/users/<user-id>/<service>
/srv/shreyws/secrets/users/<user-id>/<service>
/srv/shreyws/config/users/<user-id>/<service>
```

Rules:

- one Compose project per user-facing workload,
- separate persistent directories,
- separate secret directories,
- separate database/schema if a DB is required,
- separate Authentik groups/policies,
- separate resource quotas,
- labels use opaque owner IDs, not emails,
- per-user backup inclusion decision,
- offboarding removes Authentik access, revokes tokens, stops services, archives or deletes data.

Do not rely only on application usernames for workloads that can execute tools or code.

## Authentik Authorization Model

Recommended groups:

- `shreyws-admins`: platform infrastructure and emergency administration.
- `shreyws-owners`: owner-only applications and first agent pilot.
- `shreyws-family`: family applications after explicit approval.
- `shreyws-guests`: friend/guest applications after stronger isolation exists.

Policy:

- default deny for new applications,
- map each application to one or more explicit groups,
- no friend/family account creation during platform prep,
- removal means disabling user session, removing group membership, and rotating any exposed per-user tokens.

Emergency recovery remains Tailscale SSH plus temporary removal of a single forward-auth middleware if Authentik is unavailable.

## Secrets Model

Credential classes:

- platform credentials: never provided to agents.
- owner personal credentials: avoid; if unavoidable, use per-service revocable tokens.
- service-specific credentials: allowed only for the owning workload.
- user-specific credentials: one token per user/workload.
- disposable credentials: preferred for experiments.

Rules:

- no secrets in images or Git,
- no shared master credential across users,
- read-only secret mounts,
- short-lived tokens where supported,
- no secrets in labels or command lines,
- rotate service-specific tokens after compromise or offboarding.

Never give an agent: Borg passphrases/keys, Authentik admin secrets, Docker socket/API access, SSH private keys, Telegram bot token, database superuser credentials, or Tailscale auth keys.

## Resource Profiles

| Profile | CPU | Memory | PID limit | Storage | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| Tiny web app | 0.25 CPU | 128 MiB | 128 | 1 GiB | Pilot-style app. |
| Owner-only agent | 1 CPU | 1-2 GiB | 256 | 10-25 GiB | No host mounts; command execution disabled by default. |
| Guest agent | Not approved | Not approved | Not approved | Not approved | Requires stronger isolation first. |
| Background worker | 0.5 CPU | 512 MiB | 128 | task-specific | Must have runtime timeout/concurrency controls. |

Set limits so infrastructure keeps priority during app failures.

## Outbound Network Policy

Safe now:

- isolate workloads on app-specific Docker networks,
- avoid LAN/Tailscale credentials and host mounts,
- use application-level allowlists where available,
- log outbound failures and tool denials.

Deferred until physical access:

- nftables default-deny egress policy,
- blocking LAN and Tailscale ranges from untrusted containers,
- forced HTTP/SOCKS egress proxy,
- DNS filtering policy.

Recommended first-agent egress: allow public internet only if needed, deny Docker API and platform backends, and avoid LAN/Tailscale access by design.

## Filesystem Isolation

First agent layout:

```text
/srv/shreyws/services/agents/owner/<agent-name>
/srv/shreyws/secrets/agents/owner/<agent-name>
/srv/shreyws/config/agents/owner/<agent-name>
```

Mounts:

- app/config read-only,
- secrets read-only,
- persistent memory/workspace read-write,
- `/tmp` as tmpfs,
- no `/`, `/srv`, `/home`, `/var/run/docker.sock`, devices or host logs.

Consider filesystem quotas or a dedicated volume later if storage abuse becomes plausible.

## Stronger Isolation Options

| Technology | Fit |
| --- | --- |
| Docker rootful | Good for platform and owner-trusted apps; not enough for hostile code. |
| Rootless Docker | Better daemon boundary; needs compatibility testing with current stack. |
| Podman rootless | Good per-user model; different Compose/network operations. |
| systemd-nspawn | Useful system containers; more manual integration. |
| LXC/LXD/Incus | Stronger workload separation; operationally larger. |
| Firecracker/Kata | Strong isolation for untrusted code; higher complexity. |
| Dedicated VM | Best near-term recommendation for guest arbitrary-code workloads. |

Recommendation:

- first owner-only agent: Docker Compose with strict class-B controls.
- guest arbitrary-code workloads: dedicated VM or microVM/Incus design, not current Docker-only platform.

## Observability Requirements

Minimum before production:

- container healthcheck,
- Prometheus target or cAdvisor coverage,
- restart-loop alert coverage,
- memory/CPU visibility,
- Loki log ingestion,
- backup coverage for persistent state,
- documented restore procedure,
- update and rollback procedure.

Do not duplicate global alerts unless the workload has application-specific failure modes.

Required logging labels:

- `trust_class`,
- `workload`,
- `owner`,
- `service`,
- `container`,
- `compose_project`.

Avoid emails, real names, request IDs, session IDs or prompt contents as labels.

Audit events to capture where practical:

- deployment/update,
- authentication failures,
- privilege/config changes,
- tool execution decisions,
- secret access failures,
- resource-limit breaches,
- container restarts,
- policy denials.

## Go/No-Go Checklist For Owner Agent

Go only if:

- Authentik owner-only group is configured and tested,
- no Docker socket or broad host mounts,
- dedicated networks and storage are used,
- resource limits are set,
- logs and metrics are visible,
- backup/restore is documented,
- secrets are service-specific and revocable,
- command execution is disabled or tightly allowlisted,
- rollback is tested.

No-go for friends/family or arbitrary-code workloads until stronger isolation and firewall/egress controls exist.
