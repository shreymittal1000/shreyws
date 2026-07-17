#!/usr/bin/env python3

from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

AGENTCTL_PATH = Path(__file__).resolve().parents[1] / "agentctl" / "agentctl.py"
SPEC = importlib.util.spec_from_file_location("shreyws_agentctl", AGENTCTL_PATH)
agentctl = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(agentctl)

AgentctlError = agentctl.AgentctlError
deterministic_plan = agentctl.deterministic_plan
load_manifest = agentctl.load_manifest
validate_manifest = agentctl.validate_manifest
create_instance = agentctl.create_instance
destroy_instance = agentctl.destroy_instance


VALID = """apiVersion: shreyws.io/v1alpha1
kind: AgentInstance
metadata:
  name: hermes-demo
spec:
  owner:
    authentikUsername: shrey
    authentikGroups:
      - agent-hermes-demo
  runtime:
    type: hermes
    version: placeholder
    enabled: false
  interfaces:
    web:
      enabled: true
      path: /agents/hermes-demo/
    telegram:
      enabled: false
  storage:
    dataPath: {services}/hermes-demo
    secretPath: {secrets}/hermes-demo
    backup: true
  resources:
    cpus: 1.0
    memory: 2G
    pids: 256
  security:
    commandExecution: false
    dockerSocket: false
    hostNetwork: false
    privileged: false
    hostFilesystem: false
"""


class AgentctlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.infra = self.root / "infra"
        self.services = self.root / "services" / "agents"
        self.secrets = self.root / "secrets" / "agents"
        self.infra.mkdir(parents=True)
        self.services.mkdir(parents=True)
        self.secrets.mkdir(parents=True)
        self.manifest = self.root / "manifest.yaml"
        self.manifest.write_text(VALID.format(services=self.services, secrets=self.secrets), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        manifest = load_manifest(self.manifest)
        config = validate_manifest(manifest, self.services, self.secrets)
        return deterministic_plan(config, self.infra)

    def test_valid_manifest_and_deterministic_plan(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)
        self.assertEqual(first["instance"], "hermes-demo")
        self.assertEqual(first["auth"]["requiredGroups"], ["agent-hermes-demo"])

    def test_rejects_unsafe_name(self):
        self.manifest.write_text(VALID.format(services=self.services, secrets=self.secrets).replace("hermes-demo", "../bad", 1), encoding="utf-8")
        with self.assertRaises(AgentctlError):
            self.plan()

    def test_rejects_unsafe_path(self):
        self.manifest.write_text(VALID.format(services="/srv", secrets=self.secrets), encoding="utf-8")
        with self.assertRaises(AgentctlError):
            self.plan()

    def test_rejects_command_execution(self):
        self.manifest.write_text(VALID.format(services=self.services, secrets=self.secrets).replace("commandExecution: false", "commandExecution: true"), encoding="utf-8")
        with self.assertRaises(AgentctlError):
            self.plan()

    def test_create_is_idempotent_and_writes_deterministic_files(self):
        plan = self.plan()
        create_instance(plan, self.manifest, dry_run=False)
        first = (Path(plan["paths"]["composeFile"])).read_text(encoding="utf-8")
        create_instance(plan, self.manifest, dry_run=False)
        second = (Path(plan["paths"]["composeFile"])).read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertTrue(Path(plan["paths"]["dataPath"]).is_dir())
        self.assertTrue(Path(plan["paths"]["secretPath"]).is_dir())
        metadata = json.loads(Path(plan["paths"]["metadataFile"]).read_text(encoding="utf-8"))
        self.assertEqual(metadata["instance"], "hermes-demo")

    def test_destroy_requires_archive_or_override(self):
        plan = self.plan()
        create_instance(plan, self.manifest, dry_run=False)
        with self.assertRaises(AgentctlError):
            destroy_instance(plan, self.infra, dry_run=False, archive=False, delete_state=False, yes=False)
        self.assertTrue(Path(plan["paths"]["dataPath"]).exists())

    def test_destroy_preserves_state_by_default_with_archive(self):
        plan = self.plan()
        create_instance(plan, self.manifest, dry_run=False)
        state_file = Path(plan["paths"]["dataPath"]) / "state.txt"
        state_file.write_text("keep", encoding="utf-8")
        destroy_instance(plan, self.infra, dry_run=False, archive=True, delete_state=False, yes=False)
        self.assertFalse(Path(plan["paths"]["instanceDir"]).exists())
        self.assertTrue(state_file.exists())
        archives = list((self.infra.parent / "agents" / "archive").glob("hermes-demo-state-*.tar.gz"))
        self.assertEqual(len(archives), 1)


if __name__ == "__main__":
    unittest.main()
