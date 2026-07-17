# Hermes Runtime Adapter

Hermes is the first target runtime for ShreyWS agent instances, but it is not implemented as a real runtime adapter yet.

The current repository contains:

```text
/srv/shreyws/infra/platform/templates/hermes/README.md
/srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

The example manifest uses:

```yaml
runtime:
  type: hermes
  version: placeholder
  enabled: false
```

This is deliberate. The platform foundation must not invent Hermes deployment details.

## Required Before Real Deployment

Before enabling Hermes, document:

- official upstream repository or release source,
- pinned image and version,
- internal HTTP port,
- health endpoint,
- persistent data paths,
- secret files and environment variables,
- model/API credential handling,
- command/tool execution controls,
- web interface behavior under path prefixes,
- Telegram interface behavior,
- metrics endpoint, if any,
- logging behavior,
- backup and restore expectations,
- update and rollback procedure.

## Adapter Boundary

The platform owns:

- lifecycle,
- routing,
- Authentik group mapping,
- storage and secret directories,
- resource limits,
- backup registration metadata,
- generated Compose location,
- monitoring/logging labels.

The Hermes adapter will own:

- Hermes image and version,
- Hermes-specific environment variables,
- Hermes-specific state paths,
- Hermes health check,
- Hermes web and Telegram interfaces,
- Hermes tool configuration.

## Current Safety Decision

`agentctl start` refuses to start `runtime.type: hermes` while the adapter is placeholder-only. This avoids deploying a container that only looks like Hermes.
