# Agent Manifest

ShreyWS uses a declarative `AgentInstance` manifest to describe one isolated agent instance.

The manifest is runtime-independent. The platform reads it to generate lifecycle metadata, storage paths, secret paths, routing intent, authorization requirements, resource limits and safety defaults. Runtime adapters then translate the runtime section into a concrete Compose service when the runtime contract is known.

## Version

```yaml
apiVersion: shreyws.io/v1alpha1
kind: AgentInstance
```

`v1alpha1` is intentionally strict and small. Fields may change before family/friend use is allowed.

## Required Sections

```yaml
metadata:
  name: hermes-demo
```

`metadata.name` must be lowercase DNS-style: letters, numbers and hyphens. It becomes part of generated container, route, network and directory names.

```yaml
spec:
  owner:
    authentikUsername: shrey
    authentikGroups:
      - agent-hermes-demo
```

Authorization is group-based. Generated instances must use exact Authentik group matching. Substring authorization is not allowed.

```yaml
  runtime:
    type: hermes
    version: placeholder
    enabled: false
```

`runtime.type` is generic from the platform perspective. `hermes` is the first target adapter, but the core provisioner does not assume Hermes ports, images or environment variables.

```yaml
  interfaces:
    web:
      enabled: true
      path: /agents/hermes-demo/
    telegram:
      enabled: false
```

The initial platform supports web intent and reserves a Telegram toggle for a future adapter phase.

```yaml
  storage:
    dataPath: /srv/shreyws/services/agents/hermes-demo
    secretPath: /srv/shreyws/secrets/agents/hermes-demo
    backup: true
```

Data and secret paths must remain under the approved ShreyWS agent roots.

```yaml
  resources:
    cpus: 1.0
    memory: 2G
    pids: 256
```

Every instance has explicit resource limits.

```yaml
  security:
    commandExecution: false
    dockerSocket: false
    hostNetwork: false
    privileged: false
    hostFilesystem: false
```

These fields must be present and false in the first platform version.

## Example

See:

```text
/srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

## Validation Rules

The provisioner rejects:

- unsafe names,
- paths outside approved roots,
- malformed web paths,
- command execution,
- Docker socket access,
- host networking,
- privileged containers,
- broad host filesystem access,
- enabled runtimes without pinned non-`latest` images,
- real Hermes start attempts before the Hermes adapter contract is documented.
