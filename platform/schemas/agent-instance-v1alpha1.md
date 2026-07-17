# AgentInstance v1alpha1 Manifest

`AgentInstance` is the ShreyWS platform primitive for a provisioned AI agent instance.

The schema is intentionally small and runtime-independent. Runtime adapters may add validated runtime-specific fields later, but the platform owns lifecycle, routing, authorization, storage, secrets, monitoring metadata and backup registration.

Required top-level fields:

```yaml
apiVersion: shreyws.io/v1alpha1
kind: AgentInstance
metadata:
  name: lowercase-dns-name
spec: {}
```

Required `spec` sections:

- `owner`: Authentik username plus exact Authentik groups.
- `runtime`: runtime type/version and whether it is safe to start.
- `interfaces`: web route and future Telegram toggle.
- `storage`: data and secret paths under approved ShreyWS roots.
- `resources`: CPU, memory and PID limits.
- `security`: explicit deny-by-default controls.

Security defaults required by validation:

- `commandExecution: false`
- `dockerSocket: false`
- `hostNetwork: false`
- `privileged: false`
- `hostFilesystem: false`

Path rules:

- data paths must stay under `/srv/shreyws/services/agents`,
- secret paths must stay under `/srv/shreyws/secrets/agents`,
- web paths must use `/agents/<name>/` form.

Runtime rules:

- `runtime.type` currently accepts `hermes` or `placeholder`,
- real Hermes start is blocked until the image, ports, health endpoint and safe environment contract are documented,
- enabled runtimes must use pinned non-`latest` images.
