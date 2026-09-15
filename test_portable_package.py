from __future__ import annotations

import hashlib
import http.server
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path

try:
    from tools.dataset_image_review.build_portable_package import (
        PACKAGE_ROOT,
        build_package,
    )
    from tools.dataset_image_review.portable_launch import (
        build_app_command,
        load_config,
        wait_for_application,
    )
except ModuleNotFoundError:
    from build_portable_package import PACKAGE_ROOT, build_package
    from portable_launch import build_app_command, load_config, wait_for_application


class RunningProcess:
    def poll(self) -> None:
        return None


class UnrelatedHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"app_version":"1.1.0","instance_id":"old-instance","records":[],"stats":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class PortableConfigTests(unittest.TestCase):
    def test_relative_paths_and_options_become_app_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "visual_root": "input/visual",
                        "original_root": "input/original",
                        "label_root": "input/labels",
                        "output_root": "output",
                        "candidate_only": True,
                        "include_visualizations": False,
                        "allow_images_without_labels": True,
                        "port": 9876,
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)
            command = build_app_command(root / "app.py", config)

            self.assertIn(str((root / "input/visual").resolve()), command)
            self.assertIn(str((root / "input/original").resolve()), command)
            self.assertIn(str((root / "input/labels").resolve()), command)
            self.assertIn(str((root / "output").resolve()), command)
            self.assertIn("--candidate-only", command)
            self.assertIn("--no-visualizations", command)
            self.assertIn("--allow-images-without-labels", command)
            self.assertEqual(command[command.index("--port") + 1], "9876")

    def test_rejects_hosts_the_local_http_server_cannot_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "visual_root": "visual",
                        "original_root": "original",
                        "output_root": "output",
                        "host": "::1",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "仅允许本机回环地址"):
                load_config(config_path)

    def test_unrelated_listener_does_not_pass_reviewer_health_check(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UnrelatedHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.assertFalse(
                wait_for_application(
                    RunningProcess(),  # type: ignore[arg-type]
                    "127.0.0.1",
                    server.server_port,
                    "new-instance",
                    timeout_seconds=0.05,
                )
            )
        finally:
            server.shutdown()
            server.server_close()


class PortablePackageTests(unittest.TestCase):
    def test_build_contains_only_cross_platform_program_files_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive, checksum = build_package(Path(temp))

            self.assertTrue(archive.is_file())
            self.assertTrue(checksum.is_file())
            self.assertEqual(archive.stat().st_mode & 0o777, 0o644)
            self.assertEqual(checksum.stat().st_mode & 0o777, 0o644)
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                expected = {
                    f"{PACKAGE_ROOT}/app.py",
                    f"{PACKAGE_ROOT}/portable_launch.py",
                    f"{PACKAGE_ROOT}/requirements.txt",
                    f"{PACKAGE_ROOT}/config.example.json",
                    f"{PACKAGE_ROOT}/run_unix.sh",
                    f"{PACKAGE_ROOT}/run_windows.bat",
                    f"{PACKAGE_ROOT}/README.md",
                }
                self.assertEqual(names, expected)
                self.assertTrue(
                    (bundle.getinfo(f"{PACKAGE_ROOT}/run_unix.sh").external_attr >> 16) & 0o111
                )
                self.assertFalse(any(name.endswith((".jpg", ".jpeg", ".png")) for name in names))
                self.assertIn(".venv", bundle.read(f"{PACKAGE_ROOT}/run_unix.sh").decode("utf-8"))
                self.assertIn(".venv", bundle.read(f"{PACKAGE_ROOT}/run_windows.bat").decode("utf-8"))

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(checksum.read_text(encoding="utf-8"), f"{digest} *{archive.name}\n")

    def test_extracted_package_accepts_a_config_and_dry_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, _ = build_package(root)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
            package_dir = root / PACKAGE_ROOT
            (package_dir / "config.json").write_text(
                json.dumps(
                    {
                        "visual_root": "input/visual",
                        "original_root": "input/original",
                        "output_root": "output",
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(package_dir / "portable_launch.py"), "--dry-run"],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(str(package_dir / "app.py"), completed.stdout)

    def test_extracted_package_app_starts_and_answers_health_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, _ = build_package(root)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
            package_dir = root / PACKAGE_ROOT
            visual = root / "visual"
            original = root / "original"
            visual.mkdir()
            original.mkdir()
            (visual / "sample.jpg").write_bytes(b"visual")
            (original / "sample.jpg").write_bytes(b"original")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            command = [
                sys.executable,
                str(package_dir / "app.py"),
                "--visual-root",
                str(visual),
                "--original-root",
                str(original),
                "--output-root",
                str(root / "output"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(wait_for_application(process, "127.0.0.1", port, timeout_seconds=5))
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["stats"]["total"], 1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)

    def test_build_refuses_to_overwrite_different_existing_archive_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            archive, _ = build_package(output_dir)
            archive.write_bytes(b"user-created-file")

            with self.assertRaisesRegex(FileExistsError, "内容不同"):
                build_package(output_dir)

            rebuilt, _ = build_package(output_dir, force=True)
            self.assertEqual(rebuilt, archive)
            self.assertNotEqual(archive.read_bytes(), b"user-created-file")

    def test_build_refuses_to_overwrite_different_checksum_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            archive, checksum = build_package(output_dir)
            checksum.write_text("user-created-checksum\n", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "校验文件"):
                build_package(output_dir)

            build_package(output_dir, force=True)
            self.assertEqual(checksum.read_text(encoding="utf-8"), f"{hashlib.sha256(archive.read_bytes()).hexdigest()} *{archive.name}\n")


if __name__ == "__main__":
    unittest.main()
