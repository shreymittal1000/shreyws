# ShreyWS Architecture

Last updated: 2026-07-16

ShreyWS is organized around small Docker Compose projects under:

```text
/srv/shreyws/infra/compose
```

Persistent service data lives outside the repository under:

```text
/srv/shreyws/services
```

The normal browser path is:

```text
Tailscale client
  -> Traefik HTTPS on shreyws.tail1591fa.ts.net
  -> Authentik forward-auth
  -> internal service container
```

Core operational systems:

- Traefik handles HTTPS routing.
- Authentik handles SSO and forward-auth.
- Prometheus, Node Exporter and cAdvisor collect metrics.
- Alertmanager sends alerts to JSONL audit logs and Telegram.
- Grafana visualizes metrics and logs.
- Loki and Alloy provide centralized logging.
- BorgBackup stores encrypted backups and tested restore data.
- Diun reports container image updates without applying them automatically.

The pilot workload follows the same architecture as future internal services while avoiding privileged access and unnecessary dependencies.

## Workload Trust Classes

ShreyWS uses three trust classes:

- Platform infrastructure: Traefik, Authentik, monitoring, logging and backup components.
- Owner-trusted applications: the pilot workload and future personal internal applications.
- Guest or untrusted workloads: friend/family services, arbitrary uploads, adversarial prompts, browser agents or code execution.

Docker containers are acceptable for platform and owner-trusted applications when privileges are minimized. Docker alone is not a strong security boundary for intentionally hostile code.

## Current Network Direction

The platform is moving from one broad shared Docker network toward small app-specific frontend networks. The first live split is:

```text
pilot_frontend:
  shreyws-pilot
  shreyws-traefik
  shreyws-prometheus
```

Existing infrastructure services remain on `traefik_default` until their dependencies can be migrated safely one group at a time.

See [Agent platform architecture](agent-platform.md) for the future workload model. The deployed owner-agent pilot lives in `/srv/shreyws/infra/compose/owner-agent`, routes at `/agent/`, and uses its own `owner_agent_frontend` network shared only with Traefik and Prometheus.
