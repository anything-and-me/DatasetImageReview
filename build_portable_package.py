#!/usr/bin/env python3
"""Build a data-free, cross-platform ZIP delivery package for the reviewer."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
import zipfile


PACKAGE_ROOT = "dataset-image-review"
PACKAGE_VERSION = "1.3.0"
SOURCE_DIR = Path(__file__).resolve().parent
PACKAGE_FILES = (
    ("app.py", "app.py"),
    ("portable_launch.py", "portable_launch.py"),
    ("requirements.txt", "requirements.txt"),
    ("config.example.json", "config.example.json"),
    ("run_unix.sh", "run_unix.sh"),
    ("run_windows.bat", "run_windows.bat"),
    ("PORTABLE_README.md", "README.md"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_name() -> str:
    return f"{PACKAGE_ROOT}-portable-{PACKAGE_VERSION}.zip"


def build_package(output_dir: Path, force: bool = False) -> tuple[Path, Path]:
    """Create the ZIP and companion SHA-256 file without bundling user data."""
    output_dir = output_dir.expanduser().resolve()
    source_files = [(SOURCE_DIR / source_name, package_name) for source_name, package_name in PACKAGE_FILES]
    missing = [str(path) for path, _ in source_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少打包源文件: {', '.join(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / archive_name()
    checksum = output_dir / f"{archive.name}.sha256"
    with tempfile.NamedTemporaryFile(
        prefix=f".{archive.name}.", suffix=".tmp", dir=output_dir, delete=False
    ) as stream:
        temporary_archive = Path(stream.name)
    try:
        with zipfile.ZipFile(
            temporary_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=False,
        ) as bundle:
            for source, package_name in source_files:
                archive_member = f"{PACKAGE_ROOT}/{package_name}"
                info = zipfile.ZipInfo(archive_member)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if source.suffix == ".sh" else 0o100644) << 16
                bundle.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        generated_digest = sha256_file(temporary_archive)
        checksum_content = f"{generated_digest} *{archive.name}\n"
        if archive.exists():
            if not force and (not archive.is_file() or sha256_file(archive) != generated_digest):
                raise FileExistsError(
                    f"交付 ZIP 已存在且内容不同: {archive}；如确认覆盖，请传入 --force"
                )
        if checksum.exists():
            if not checksum.is_file() or checksum.read_text(encoding="utf-8") != checksum_content:
                if not force:
                    raise FileExistsError(
                        f"校验文件已存在且内容不同: {checksum}；如确认覆盖，请传入 --force"
                    )
        if archive.is_file() and sha256_file(archive) == generated_digest:
            temporary_archive.unlink()
        else:
            temporary_archive.replace(archive)
        archive.chmod(0o644)
    finally:
        if temporary_archive.exists():
            temporary_archive.unlink()
    checksum.write_text(checksum_content, encoding="utf-8")
    checksum.chmod(0o644)
    return archive, checksum


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=SOURCE_DIR / "dist")
    parser.add_argument("--force", action="store_true", help="允许覆盖已有的不同内容 ZIP/校验文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive, checksum = build_package(args.output_dir, force=args.force)
    print(f"ZIP: {archive}")
    print(f"SHA-256: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
