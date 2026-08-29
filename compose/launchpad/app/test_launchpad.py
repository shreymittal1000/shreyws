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

    def test_hermes_preset_has_safe_runtime_defaults(self):
        value=self.valid(); value.update(source="nousresearch/hermes-agent:v2026.8.27",container_port="",memory_mb=4096,cpus=2,storage_gb=25)
        config=launchpad.validate_payload(value)
        self.assertEqual(config["container_port"],8642)
        self.assertEqual(config["environment"]["API_SERVER_HOST"],"0.0.0.0")
        self.assertGreaterEqual(len(config["secrets"]["API_SERVER_KEY"]),32)

    def test_hermes_runtime_mount_command_and_browser_shm(self):
        value=self.valid(); value.update(source="nousresearch/hermes-agent:v2026.8.27",container_port=8642,memory_mb=4096,cpus=2,storage_gb=25)
        config=launchpad.validate_payload(value)
        calls=[]
        def fake_docker(args, **kwargs):
            calls.append(args)
            if args[0]=="network" and args[1]=="ls": return "launchpad_app_demo-app\n"
            if args[:2]==["inspect","--format"]: return '{}'
            return ""
        with patch.object(launchpad,"docker",side_effect=fake_docker):
            launchpad.create_container(config,str(config["source"]),routed=False)
        create=next(args for args in calls if args[0]=="create")
        self.assertIn("type=bind,src="+str(launchpad.app_dir("demo-app")/"data")+",dst=/opt/data",create)
        self.assertEqual(create[-3:],["nousresearch/hermes-agent:v2026.8.27","sleep","infinity"])
        self.assertEqual(create[create.index("--shm-size")+1],"1g")

    def test_ui_offers_hermes_setup_without_ssh(self):
        self.assertIn("Set up Hermes",launchpad.INDEX)
        self.assertIn("hermes setup && hermes gateway start",launchpad.INDEX)

    def test_image_defaults_do_not_overwrite_user_resource_edits(self):
        self.assertIn("if(selected===configuredImage)return",launchpad.INDEX)
        self.assertIn("const selectedImage=image.value",launchpad.INDEX)
        self.assertIn("image.value=selectedImage",launchpad.INDEX)
        self.assertIn("form.container_port.value=selectedPort",launchpad.INDEX)

    def test_ui_has_valid_api_base_literal(self):
        self.assertIn('const B="/launchpad/api";', launchpad.INDEX)

    def test_ui_does_not_overwrite_git_auto_port_during_refresh(self):
        self.assertIn("if(sourceType.value==='image')form.container_port.value", launchpad.INDEX)

    def test_ui_has_immediate_and_pending_button_feedback(self):
        self.assertIn("button.classList.add('ack')", launchpad.INDEX)
        self.assertIn("button.classList.add('busy')", launchpad.INDEX)
        self.assertIn("button.setAttribute('aria-busy','true')", launchpad.INDEX)
        self.assertIn("finally{endButtonRequest(button)}", launchpad.INDEX)

    def test_ui_has_managed_container_terminal(self):
        self.assertIn("openTerminal('${a.name}')", launchpad.INDEX)
        self.assertIn("new WebSocket", launchpad.INDEX)
        self.assertIn("terminalSocket.send(command+'\\r')", launchpad.INDEX)

    def test_terminal_avoids_nested_tty_and_duplicate_echo(self):
        source=__import__("inspect").getsource(launchpad.Handler.handle_terminal)
        self.assertIn('["docker", "exec", "-i",',source)
        self.assertNotIn('"-it"',source)
        self.assertIn("~termios.ECHO",source)
        self.assertIn('payload.replace(b"\\r", b"\\n")',source)

    def test_websocket_handler_uses_http_11(self):
        self.assertEqual(launchpad.Handler.protocol_version,"HTTP/1.1")

    def test_websocket_accept_matches_rfc_example(self):
        self.assertEqual(
            launchpad.websocket_accept("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_server_websocket_frame_is_unmasked(self):
        self.assertEqual(launchpad.websocket_frame(b"hello"),b"\x81\x05hello")

    def test_terminal_origin_is_exact_internal_host(self):
        self.assertTrue(launchpad.terminal_origin_allowed("https://shreyws.tail1591fa.ts.net"))
        self.assertFalse(launchpad.terminal_origin_allowed("https://evil.example"))
        self.assertFalse(launchpad.terminal_origin_allowed("http://shreyws.tail1591fa.ts.net"))

    def test_rejects_unapproved_image(self):
        value=self.valid(); value["source"]="evil/image:latest"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_public_without_configured_domain_suffix(self):
        value=self.valid(); value.update(visibility="public",domain="site.apps.example.com")
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_public_domain_must_be_allowlisted(self):
        value=self.valid(); value.update(visibility="public",domain="site.apps.example.com")
        with patch.object(launchpad, "PUBLIC_DOMAIN_SUFFIXES", ("other.example",)):
            with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_public_route_uses_tunnel_entrypoint_without_origin_tls(self):
        value=self.valid(); value.update(visibility="public",domain="site.apps.example.com")
        with patch.object(launchpad, "PUBLIC_DOMAIN_SUFFIXES", ("apps.example.com",)):
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

    def test_git_port_can_be_auto_detected(self):
        value=self.valid(); value.update(source_type="git",source="https://github.com/example/app.git",container_port="")
        self.assertEqual(launchpad.validate_payload(value)["container_port"],0)

    def test_resolves_single_exposed_tcp_port(self):
        config={"container_port":0}
        with patch.object(launchpad,"docker",return_value='[{"Config":{"ExposedPorts":{"8080/tcp":{}}}}]'):
            self.assertEqual(launchpad.resolve_container_port(config,"demo"),8080)

    def test_requires_choice_for_multiple_exposed_ports(self):
        config={"container_port":0}
        value='[{"Config":{"ExposedPorts":{"8080/tcp":{},"9090/tcp":{}}}}]'
        with patch.object(launchpad,"docker",return_value=value):
            with self.assertRaisesRegex(launchpad.LaunchpadError,"multiple TCP ports"):
                launchpad.resolve_container_port(config,"demo")

    def test_explicit_port_wins_without_inspecting_image(self):
        with patch.object(launchpad,"docker") as mocked:
            self.assertEqual(launchpad.resolve_container_port({"container_port":3000},"demo"),3000)
        mocked.assert_not_called()

    def test_permission_failure_has_rootless_guidance(self):
        inspected='[{"State":{"Running":false,"ExitCode":1,"Error":""}}]'
        def fake_docker(args, **kwargs):
            if args[0] == "inspect": return inspected
            if args[0] == "logs": return 'chown("/var/cache/nginx/client_temp", 101) failed (1: Operation not permitted)'
            raise AssertionError(args)
        with patch.object(launchpad,"docker",side_effect=fake_docker):
            message=launchpad.startup_failure("candidate")
        self.assertIn("rootless/unprivileged runtime image",message)

    def test_normalizes_private_checkout_file_permissions(self):
        source=Path(tmp.name)/"permission-test"
        nested=source/"public"; nested.mkdir(parents=True,exist_ok=True)
        asset=nested/"portrait.jpg"; asset.write_bytes(b"image"); asset.chmod(0o600)
        completed=__import__("subprocess").CompletedProcess([],0,stdout=b"public/portrait.jpg\0")
        with patch.object(launchpad.subprocess,"run",return_value=completed):
            launchpad.normalize_build_context_permissions(source)
        self.assertEqual(asset.stat().st_mode & 0o777,0o644)
        self.assertEqual(nested.stat().st_mode & 0o777,0o755)

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
