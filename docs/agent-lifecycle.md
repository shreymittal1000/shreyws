# Agent Lifecycle

The initial owner-facing lifecycle tool is:

```text
/srv/shreyws/infra/platform/agentctl/agentctl.py
```

It is a safe command-line provisioner for `AgentInstance` manifests. It does not create users, Telegram bots, real Hermes instances or command-execution surfaces.

## Commands

Validate a manifest:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py validate /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

Show the deterministic plan:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py plan /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

Create generated files and directories:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py create /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

Check status:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py status /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

Start:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py start /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

The example Hermes manifest is `enabled: false`, so `start` intentionally refuses. This prevents accidentally running a fake Hermes runtime.

Stop:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py stop /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

Destroy generated files while preserving state:

```bash
python3 /srv/shreyws/infra/platform/agentctl/agentctl.py destroy --archive-state /srv/shreyws/infra/platform/examples/hermes-demo.yaml
```

`destroy` is safe by default:

- it refuses without `--archive-state` or `--yes`,
- it does not delete persistent state unless `--delete-state` is explicitly supplied,
- it shows dry-run output with `--dry-run`.

## Generated Structure

For `hermes-demo`, `create` writes:

```text
/srv/shreyws/infra/platform/instances/hermes-demo/
  manifest.yaml
  compose.yaml
  metadata.json
  backup-registration.json
  README.md
```

It also creates:

```text
/srv/shreyws/services/agents/hermes-demo
/srv/shreyws/secrets/agents/hermes-demo
```

The generated Compose file is reference-only until the runtime is enabled by a future adapter.

## Idempotency

Running `create` repeatedly for the same manifest rewrites deterministic files and does not create duplicate routes, networks or services.

## Auditability

Each instance has a generated `metadata.json` and `backup-registration.json`. Future platform work can aggregate these into monitoring, backup registration or a web control plane.
