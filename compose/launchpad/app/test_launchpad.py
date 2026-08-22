import importlib.util
import os
import tempfile
import unittest
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

    def test_rejects_unapproved_image(self):
        value=self.valid(); value["source"]="evil/image:latest"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_public_until_enabled(self):
        value=self.valid(); value["visibility"]="public"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_host_ip(self):
        value=self.valid(); value["ipv4"]="192.168.1.9"
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_accepts_https_git(self):
        value=self.valid(); value.update(source_type="git",source="https://github.com/example/app.git")
        self.assertEqual(launchpad.validate_payload(value)["source_type"],"git")

    def test_rejects_git_ssrf(self):
        value=self.valid(); value.update(source_type="git",source="https://127.0.0.1/repo.git")
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_rejects_git_url_credentials(self):
        value=self.valid(); value.update(source_type="git",source="https://token@github.com/example/app.git")
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)

    def test_environment_key_validation(self):
        value=self.valid(); value["environment"]={"BAD-KEY":"x"}
        with self.assertRaises(launchpad.LaunchpadError): launchpad.validate_payload(value)


if __name__ == "__main__": unittest.main()
