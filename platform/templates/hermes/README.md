# Hermes Runtime Adapter Placeholder

This directory is intentionally a placeholder.

The ShreyWS platform now has a generic `AgentInstance` manifest and provisioner, but it does not yet implement a real Hermes adapter. Do not invent Hermes image names, ports, environment variables, health endpoints, tool controls or storage paths.

Before enabling a real Hermes runtime adapter, record the authoritative Hermes deployment contract here:

- upstream project URL,
- pinned image/version,
- exposed internal port,
- health endpoint,
- required environment variables,
- persistent storage paths,
- secret files,
- command/tool execution controls,
- metrics endpoint if any,
- logging behavior,
- upgrade and rollback procedure.

Until that exists, Hermes manifests should use:

```yaml
runtime:
  type: hermes
  version: placeholder
  enabled: false
```
