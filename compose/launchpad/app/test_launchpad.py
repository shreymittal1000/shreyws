import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

tmp = tempfile.TemporaryDirectory()
os.environ["LAUNCHPAD_DB_PATH"] = str(Path(tmp.name) / "launchpad.db")
os.environ["LAUNCHPAD_APPS_ROOT"] = str(Path(tmp.name) / "apps")
spec = importlib.util.spec_from_file_location("launchpad", Path(__file__).with_name("launchpad.py"))
launchpad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launchpad)


class ValidationTests(unittest.TestCase):
    def valid(self):
        return {"name":"demo-app","source_type":"image","source":"nginx:1.29.1-alpine","memory_mb":128,"cpus":0.25,"storage_gb":1,"visibility":"internal","domain":"","container_port":80,"ipv4":"","environment":{},"secrets":{}}

    def test_valid_image(self):
        self.assertEqual(launchpad.validate_payload(self.valid())["name"], "demo-app")

    def test_ui_has_valid_api_base_literal(self):
        self.assertIn('const B="/launchpad/api";', launchpad.INDEX)

    def test_rejects_unapproved_image(self):
        value=self.valid(); value["source"]="evil/image:latest"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_public_until_enabled(self):
        value=self.valid(); value["visibility"]="public"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_public_domain_must_be_allowlisted(self):
        value=self.valid(); value.update(visibility="public",domain="site.apps.example.com")
        with patch.object(launchpad, "PUBLIC_ENABLED", True), patch.object(launchpad, "PUBLIC_DOMAIN_SUFFIXES", ("other.example",)):
            with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_public_route_uses_tunnel_entrypoint_without_origin_tls(self):
        value=self.valid(); value.update(visibility="public",domain="site.apps.example.com")
        with patch.object(launchpad, "PUBLIC_ENABLED", True), patch.object(launchpad, "PUBLIC_DOMAIN_SUFFIXES", ("apps.example.com",)):
            config=launchpad.validate_payload(value)
        labels=launchpad.route_labels(config,"app-network")
        self.assertIn("traefik.http.routers.launchpad-demo-app.entrypoints=cloudflare",labels)
        self.assertNotIn("traefik.http.routers.launchpad-demo-app.tls=true",labels)

    def test_rejects_host_ip(self):
        value=self.valid(); value["ipv4"]="192.168.1.9"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_accepts_https_git(self):
        value=self.valid(); value.update(source_type="git",source="https://github.com/example/app.git")
        self.assertEqual(launchpad.validate_payload(value)["source_type"],"git")

    def test_accepts_git_branch(self):
        value=self.valid(); value.update(source_type="git",source="https://github.com/example/app.git",git_ref="feature/private-repos")
        self.assertEqual(launchpad.validate_payload(value)["git_ref"],"feature/private-repos")

    def test_rejects_unsafe_git_branch(self):
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_git_ref("../main")

    def test_git_remote_uses_strict_per_app_key(self):
        private, public = launchpad.deploy_key_paths("demo-app")
        private.parent.mkdir(parents=True, exist_ok=True); private.write_text("private"); public.write_text("public")
        launchpad.KNOWN_HOSTS_PATH.write_text("github.com ssh-ed25519 AAAA\n")
        remote, env = launchpad.git_remote("https://github.com/example/private.git", "demo-app")
        self.assertEqual(remote, "git@github.com:example/private.git")
        self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
        self.assertIn(str(private), env["GIT_SSH_COMMAND"])

    def test_rejects_git_ssrf(self):
        value=self.valid(); value.update(source_type="git",source="https://127.0.0.1/repo.git")
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_git_url_credentials(self):
        value=self.valid(); value.update(source_type="git",source="https://token@github.com/example/app.git")
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_environment_key_validation(self):
        value=self.valid(); value["environment"]={"BAD-KEY":"x"}
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_external_discovery_excludes_owned_workloads(self):
        inspected = [
            {"Id":"a"*64,"Name":"/friend-site","Config":{"Image":"friend/site:latest","Labels":{"com.docker.compose.project":"friend"}},"State":{"Running":False,"Status":"exited"},"HostConfig":{"Memory":268435456,"NanoCpus":500000000},"NetworkSettings":{"Networks":{"friend-net":{"IPAddress":"192.168.1.130","GlobalIPv6Address":""}}}},
            {"Id":"b"*64,"Name":"/grafana","Config":{"Image":"grafana/grafana","Labels":{"com.docker.compose.project":"grafana"}},"State":{"Running":False,"Status":"exited"},"HostConfig":{},"NetworkSettings":{"Networks":{}}},
            {"Id":"c"*64,"Name":"/managed","Config":{"Image":"demo","Labels":{"shreyws.launchpad.app":"demo"}},"State":{"Running":False,"Status":"exited"},"HostConfig":{},"NetworkSettings":{"Networks":{}}},
        ]
        def fake_docker(args, **kwargs):
            if args == ["ps", "-aq"]: return "a\nb\nc\n"
            if args[0] == "inspect": return __import__("json").dumps(inspected)
            raise AssertionError(args)
        with patch.object(launchpad, "docker", side_effect=fake_docker):
            rows = launchpad.external_rows()
        self.assertEqual([row["name"] for row in rows], ["friend-site"])
        self.assertEqual(rows[0]["networks"][0]["ipv4"], "192.168.1.130")
        self.assertEqual(rows[0]["memory_mb"], 256)


if __name__ == "__main__": unittest.main()
