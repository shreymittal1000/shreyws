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
