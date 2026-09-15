#!/usr/bin/env python3
"""Launch the portable image-review package from a local JSON configuration."""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any
import webbrowser
import uuid


REQUIRED_PATH_KEYS = ("visual_root", "original_root", "output_root")
OPTIONAL_PATH_KEYS = ("label_root", "manifest", "state_file")
BOOLEAN_KEYS = ("candidate_only", "include_visualizations", "allow_images_without_labels")
DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8765,
    "candidate_only": False,
    "include_visualizations": True,
    "allow_images_without_labels": False,
}
LOOPBACK_HOSTS = {"127.0.0.1"}


def resolve_config_path(value: str, config_dir: Path) -> str:
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else config_dir / path).resolve())


def load_config(config_path: Path) -> dict[str, Any]:
    """Load, validate, and normalize a portable-package configuration."""
    config_path = config_path.expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"找不到配置文件: {config_path}。请将 config.example.json 复制为 config.json 后填写路径。"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置文件 {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象")

    allowed_keys = {
        *REQUIRED_PATH_KEYS,
        *OPTIONAL_PATH_KEYS,
        *BOOLEAN_KEYS,
        "host",
        "port",
    }
    unexpected = sorted(set(raw) - allowed_keys)
    if unexpected:
        raise ValueError(f"配置中存在未识别字段: {', '.join(unexpected)}")

    config: dict[str, Any] = dict(DEFAULTS)
    config.update(raw)
    for key in REQUIRED_PATH_KEYS:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"配置项 {key} 必须是非空路径字符串")
        config[key] = resolve_config_path(value, config_path.parent)
    for key in OPTIONAL_PATH_KEYS:
        value = config.get(key)
        if value in (None, ""):
            config[key] = None
        elif isinstance(value, str):
            config[key] = resolve_config_path(value, config_path.parent)
        else:
            raise ValueError(f"配置项 {key} 必须是路径字符串或 null")
    for key in BOOLEAN_KEYS:
        if not isinstance(config[key], bool):
            raise ValueError(f"配置项 {key} 必须是 true 或 false")

    host = config["host"]
    if not isinstance(host, str) or host not in LOOPBACK_HOSTS:
        raise ValueError(f"host 仅允许本机回环地址: {', '.join(sorted(LOOPBACK_HOSTS))}")
    port = config["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port 必须是 1 到 65535 的整数")
    return config


def build_app_command(app_path: Path, config: dict[str, Any]) -> list[str]:
    """Translate portable-package JSON settings to the normal application CLI."""
    command = [
        sys.executable,
        str(app_path.resolve()),
        "--visual-root",
        config["visual_root"],
        "--original-root",
        config["original_root"],
        "--output-root",
        config["output_root"],
        "--host",
        config["host"],
        "--port",
        str(config["port"]),
    ]
    for setting, argument in (
        ("label_root", "--label-root"),
        ("manifest", "--manifest"),
        ("state_file", "--state-file"),
    ):
        if config[setting]:
            command.extend((argument, config[setting]))
    if config["candidate_only"]:
        command.append("--candidate-only")
    if not config["include_visualizations"]:
        command.append("--no-visualizations")
    if config["allow_images_without_labels"]:
        command.append("--allow-images-without-labels")
    return command


def build_web_setup_command(app_path: Path, config: dict[str, Any]) -> list[str]:
    """Start the application without paths so the browser can select folders."""
    return [
        sys.executable,
        str(app_path.resolve()),
        "--web-setup",
        "--host",
        config["host"],
        "--port",
        str(config["port"]),
    ]


def is_reviewer_healthy(host: str, port: int, expected_instance_id: str | None = None) -> bool:
    try:
        connection = http.client.HTTPConnection(host, port, timeout=0.5)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        payload = json.loads(response.read())
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
        return False
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    return (
        response.status == 200
        and isinstance(payload, dict)
        and isinstance(payload.get("app_version"), str)
        and isinstance(payload.get("records"), list)
        and isinstance(payload.get("stats"), dict)
        and (
            expected_instance_id is None
            or payload.get("instance_id") == expected_instance_id
        )
    )


def wait_for_application(
    process: subprocess.Popen[Any],
    host: str,
    port: int,
    expected_instance_id: str | None = None,
    timeout_seconds: float = 60.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_reviewer_healthy(host, port, expected_instance_id):
            return True
        time.sleep(0.15)
    return False


def browser_url(host: str, port: int) -> str:
    address = f"[{host}]" if ":" in host else host
    return f"http://{address}:{port}"


def parse_args() -> argparse.Namespace:
    package_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=package_dir / "config.json")
    parser.add_argument("--web-setup", action="store_true", help="在网页中选择本机文件夹，不读取 config.json")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开本地浏览器")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将要执行的应用命令")
    return parser.parse_args()


def main() -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本。", file=sys.stderr)
        return 2
    args = parse_args()
    default_config = Path(__file__).resolve().with_name("config.json")
    if args.web_setup:
        config: dict[str, Any] = dict(DEFAULTS)
        command = build_web_setup_command(Path(__file__).resolve().with_name("app.py"), config)
    else:
        try:
            config = load_config(args.config)
            command = build_app_command(Path(__file__).resolve().with_name("app.py"), config)
        except ValueError as exc:
            if args.config.expanduser().resolve() != default_config or default_config.is_file():
                print(str(exc), file=sys.stderr)
                return 2
            config = dict(DEFAULTS)
            command = build_web_setup_command(Path(__file__).resolve().with_name("app.py"), config)
            print("未找到 config.json，已进入网页文件夹选择模式。", flush=True)
    if args.dry_run:
        print(shlex.join(command))
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("缺少 Pillow。请先运行启动脚本，或执行 python -m pip install -r requirements.txt。", file=sys.stderr)
        return 2

    instance_id = uuid.uuid4().hex
    command.extend(("--instance-id", instance_id))
    process = subprocess.Popen(command)
    ready = wait_for_application(process, config["host"], config["port"], instance_id)
    if not ready:
        if process.poll() is not None:
            print(f"审核服务启动失败，退出码: {process.returncode}", file=sys.stderr)
            return process.returncode or 1
        print("审核服务尚未在 60 秒内通过健康检查；服务仍在运行，暂不自动打开浏览器。", file=sys.stderr)
    elif not args.no_browser:
        webbrowser.open(browser_url(config["host"], config["port"]))
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
