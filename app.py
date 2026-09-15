#!/usr/bin/env python3
"""Local side-by-side image review tool for model-assisted dataset curation.

The application serves a small browser UI, keeps review state in an
atomically-written JSON file, and copies (never moves) accepted source files
into a dataset export.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import http.server
import json
import mimetypes
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import tempfile
import threading
from typing import Any, Iterable
from urllib.parse import unquote, urlparse
import uuid

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DECISIONS = {"keep", "reject", "skip"}
DECISION_LABELS = {"keep": "保留", "reject": "不保留", "skip": "跳过", None: "未审核"}
APP_VERSION = "1.2.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve()


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def relative_posix(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def image_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def resolve_manifest_path(value: str | None, root: Path, manifest_path: Path) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    candidates = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        candidates.extend(
            [
                manifest_path.parent / candidate,
                root.parent / candidate,
                root / candidate,
            ]
        )
    for option in candidates:
        if option.exists():
            return option.resolve()
    return candidates[0].resolve() if candidates else None


def stable_sample_id(visual_rel: str | None, original_rel: str | None, fallback: str = "") -> str:
    payload = f"{visual_rel or ''}\0{original_rel or ''}\0{fallback}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def validate_label(path: Path) -> list[str]:
    anomalies: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ["invalid_label"]
    for line_number, line in enumerate(lines, start=1):
        fields = line.split()
        if len(fields) not in {5, 6}:
            anomalies.append(f"invalid_label:{line_number}")
            continue
        try:
            class_id = int(fields[0])
            values = [float(value) for value in fields[1:]]
        except ValueError:
            anomalies.append(f"invalid_label:{line_number}")
            continue
        if class_id < 0 or not all(0.0 <= value <= 1.0 for value in values[:4]):
            anomalies.append(f"invalid_label:{line_number}")
            continue
        if len(values) == 5 and not 0.0 <= values[4] <= 1.0:
            anomalies.append(f"invalid_label:{line_number}")
    return anomalies


@dataclass
class PairRecord:
    sample_id: str
    visual_rel: str | None
    original_rel: str | None
    label_rel: str | None
    visual_path: Path | None
    original_path: Path | None
    label_path: Path | None
    anomalies: list[str]

    @property
    def has_error(self) -> bool:
        return bool(self.anomalies)

    def to_dict(self, decision: str | None = None) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "visual_rel": self.visual_rel,
            "original_rel": self.original_rel,
            "label_rel": self.label_rel,
            "anomalies": self.anomalies,
            "has_error": self.has_error,
            "decision": decision,
        }


class PairingEngine:
    def __init__(
        self,
        visual_root: Path,
        original_root: Path,
        label_root: Path | None = None,
        manifest_path: Path | None = None,
        include_original_only: bool = True,
    ) -> None:
        self.visual_root = canonical_path(visual_root)
        self.original_root = canonical_path(original_root)
        self.label_root = canonical_path(label_root) if label_root else None
        self.manifest_path = canonical_path(manifest_path) if manifest_path else None
        self.include_original_only = include_original_only

    def _label_for(self, visual_rel: str | None, original_rel: str | None) -> tuple[Path | None, list[str]]:
        if self.label_root is None:
            return None, []
        rels = [rel for rel in (original_rel, visual_rel) if rel]
        candidates: list[Path] = []
        for rel in rels:
            candidate = self.label_root / Path(rel).with_suffix(".txt")
            if candidate.is_file():
                candidates.append(candidate.resolve())
        if not candidates:
            stem = Path(rels[0]).stem if rels else ""
            if stem:
                candidates = sorted(self.label_root.rglob(f"{stem}.txt"))
        unique = list(dict.fromkeys(candidates))
        if len(unique) > 1:
            return None, ["duplicate_label"]
        if not unique:
            return None, ["missing_label"]
        anomalies = validate_label(unique[0])
        return unique[0], anomalies

    def _record(
        self,
        sample_id: str | None,
        visual_path: Path | None,
        original_path: Path | None,
        visual_rel: str | None,
        original_rel: str | None,
        label_value: str | None = None,
    ) -> PairRecord:
        anomalies: list[str] = []
        if visual_path is None:
            anomalies.append("missing_visual")
        if original_path is None:
            anomalies.append("missing_original")
        label_path: Path | None
        if label_value is not None:
            label_path = resolve_manifest_path(label_value, self.label_root or Path.cwd(), self.manifest_path or Path.cwd())
            if label_path is None or not label_path.is_file():
                label_path = None
                anomalies.append("missing_label")
            elif self.label_root is not None and not is_inside(label_path, self.label_root):
                anomalies.append("label_outside_root")
            else:
                anomalies.extend(validate_label(label_path))
        else:
            label_path, label_anomalies = self._label_for(visual_rel, original_rel)
            anomalies.extend(label_anomalies)
        return PairRecord(
            sample_id=sample_id or stable_sample_id(visual_rel, original_rel),
            visual_rel=visual_rel,
            original_rel=original_rel,
            label_rel=relative_posix(label_path, self.label_root) if label_path and self.label_root else (
                str(label_path) if label_path else None
            ),
            visual_path=visual_path,
            original_path=original_path,
            label_path=label_path,
            anomalies=sorted(set(anomalies)),
        )

    def _manifest_rows(self) -> list[PairRecord]:
        if self.manifest_path is None or not self.manifest_path.is_file():
            return []
        suffix = self.manifest_path.suffix.lower()
        rows: list[dict[str, Any]] = []
        if suffix in {".jsonl", ".ndjson"}:
            with self.manifest_path.open(encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream if line.strip()]
        elif suffix == ".json":
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("records", [])
        else:
            with self.manifest_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
        records: list[PairRecord] = []
        for row_index, row in enumerate(rows, start=1):
            visual_value = row.get("visual_image") or row.get("visual") or row.get("visual_rel")
            original_value = row.get("original_image") or row.get("original") or row.get("original_rel")
            visual_path = resolve_manifest_path(visual_value, self.visual_root, self.manifest_path)
            original_path = resolve_manifest_path(original_value, self.original_root, self.manifest_path)
            visual_rel = relative_posix(visual_path, self.visual_root) if visual_path else visual_value
            original_rel = relative_posix(original_path, self.original_root) if original_path else original_value
            record = self._record(
                str(row.get("sample_id") or row.get("id") or "") or None,
                visual_path if visual_path and visual_path.is_file() else None,
                original_path if original_path and original_path.is_file() else None,
                visual_rel,
                original_rel,
                row.get("label_file") or row.get("label") or row.get("label_rel"),
            )
            if not row.get("sample_id") and not row.get("id"):
                record.sample_id = stable_sample_id(visual_rel, original_rel, str(row_index))
            records.append(record)
        return records

    def scan(self) -> list[PairRecord]:
        if self.manifest_path:
            return self._manifest_rows()
        visuals = image_files(self.visual_root)
        originals = image_files(self.original_root)
        visual_by_rel = {path.relative_to(self.visual_root).as_posix(): path for path in visuals}
        original_by_rel = {path.relative_to(self.original_root).as_posix(): path for path in originals}
        visual_by_stem: dict[str, list[Path]] = {}
        original_by_stem: dict[str, list[Path]] = {}
        for path in visuals:
            visual_by_stem.setdefault(path.stem.casefold(), []).append(path)
        for path in originals:
            original_by_stem.setdefault(path.stem.casefold(), []).append(path)

        records: list[PairRecord] = []
        matched_original_paths: set[Path] = set()
        for visual_rel, visual_path in visual_by_rel.items():
            anomalies: list[str] = []
            original_path = original_by_rel.get(visual_rel)
            original_rel = visual_rel if original_path else None
            matched_by_stem = False
            if original_path is None:
                candidates = original_by_stem.get(visual_path.stem.casefold(), [])
                if len(candidates) == 1:
                    original_path = candidates[0]
                    original_rel = original_path.relative_to(self.original_root).as_posix()
                    matched_by_stem = True
                elif len(candidates) > 1:
                    anomalies.append("duplicate_stem")
            if matched_by_stem and (
                len(visual_by_stem.get(visual_path.stem.casefold(), [])) > 1
                or len(original_by_stem.get(original_path.stem.casefold(), [])) > 1
            ):
                anomalies.append("duplicate_stem")
            record = self._record(
                None,
                visual_path,
                original_path,
                visual_rel,
                original_rel,
            )
            record.anomalies = sorted(set(record.anomalies + anomalies))
            records.append(record)
            if original_path is not None:
                matched_original_paths.add(original_path.resolve())

        if self.include_original_only:
            for original_rel, original_path in original_by_rel.items():
                if original_path.resolve() in matched_original_paths:
                    continue
                records.append(
                    self._record(
                        None,
                        None,
                        original_path,
                        None,
                        original_rel,
                    )
                )

        records.sort(key=lambda record: (record.visual_rel or record.original_rel or record.sample_id))
        return records


class ReviewStore:
    def __init__(self, path: Path) -> None:
        self.path = canonical_path(path)
        self.lock = threading.RLock()
        self.decisions: dict[str, str] = {}
        self.history: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        with self.lock:
            if not self.path.is_file():
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise ValueError(f"无法读取审核状态文件: {self.path}")
            self.decisions = {
                key: value for key, value in payload.get("decisions", {}).items() if value in DECISIONS
            }
            self.history = payload.get("history", []) if isinstance(payload.get("history", []), list) else []

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": utc_now(),
            "decisions": self.decisions,
            "history": self.history[-1000:],
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def set_decision(self, sample_id: str, decision: str) -> None:
        if decision not in DECISIONS:
            raise ValueError(f"unsupported decision: {decision}")
        with self.lock:
            previous = self.decisions.get(sample_id)
            self.history.append(
                {"sample_id": sample_id, "previous": previous, "decision": decision, "timestamp": utc_now()}
            )
            self.decisions[sample_id] = decision
            self._write()

    def undo(self) -> str | None:
        with self.lock:
            if not self.history:
                return None
            action = self.history.pop()
            sample_id = action["sample_id"]
            previous = action.get("previous")
            if previous in DECISIONS:
                self.decisions[sample_id] = previous
            else:
                self.decisions.pop(sample_id, None)
            self._write()
            return sample_id

    def stats(self, records: Iterable[PairRecord]) -> dict[str, int]:
        items = list(records)
        return {
            "total": len(items),
            "reviewed": sum(self.decisions.get(item.sample_id) in DECISIONS for item in items),
            "keep": sum(self.decisions.get(item.sample_id) == "keep" for item in items),
            "reject": sum(self.decisions.get(item.sample_id) == "reject" for item in items),
            "skip": sum(self.decisions.get(item.sample_id) == "skip" for item in items),
            "unreviewed": sum(item.sample_id not in self.decisions for item in items),
            "anomalies": sum(item.has_error for item in items),
        }


def empty_stats() -> dict[str, int]:
    return {
        "total": 0,
        "reviewed": 0,
        "keep": 0,
        "reject": 0,
        "skip": 0,
        "unreviewed": 0,
        "anomalies": 0,
    }


@dataclass
class DirectoryRequest:
    title: str
    completed: threading.Event
    selected: Path | None = None
    error: str | None = None
    cancelled: bool = False


class DirectoryPicker:
    """Open native directory dialogs on the application's main thread."""

    def __init__(self) -> None:
        self.requests: Queue[DirectoryRequest] = Queue()

    def choose(self, title: str, timeout_seconds: float = 300.0) -> Path | None:
        request = DirectoryRequest(title=title, completed=threading.Event())
        self.requests.put(request)
        if not request.completed.wait(timeout_seconds):
            request.cancelled = True
            raise ValueError("目录选择超时，请重新点击选择按钮")
        if request.error:
            raise ValueError(request.error)
        return request.selected

    def process_pending(self) -> None:
        while True:
            try:
                request = self.requests.get_nowait()
            except Empty:
                return
            if request.cancelled:
                continue
            try:
                request.selected = self._show_dialog(request.title)
            except (OSError, RuntimeError) as exc:
                request.error = str(exc)
            finally:
                request.completed.set()

    @staticmethod
    def _show_dialog(title: str) -> Path | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise RuntimeError(
                "当前 Python 未提供 tkinter，无法打开系统目录选择器。"
                "请安装 tkinter 后重试，或使用 JSON/命令行配置启动。"
            ) from exc
        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.update()
            selected = filedialog.askdirectory(parent=root, title=title, mustexist=True)
        except tk.TclError as exc:
            raise RuntimeError(f"无法打开系统目录选择器: {exc}") from exc
        finally:
            if root is not None:
                root.destroy()
        return canonical_path(Path(selected)) if selected else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_if_same(source: Path, destination: Path, source_hash: str | None = None) -> str:
    source_hash = source_hash or sha256_file(source)
    if destination.exists():
        if not destination.is_file():
            return "conflict"
        return "same" if sha256_file(destination) == source_hash else "conflict"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temp_name)
        os.replace(temp_name, destination)
    finally:
        if temp_name.exists():
            temp_name.unlink()
    return "created"


def preflight_artifacts(
    artifacts: list[tuple[Path, Path]],
) -> tuple[list[tuple[Path, Path, str, str]], list[Path]]:
    """Inspect every target in a sample before copying any of its files."""
    planned: list[tuple[Path, Path, str, str]] = []
    conflicts: list[Path] = []
    for source, destination in artifacts:
        source_hash = sha256_file(source)
        if destination.exists():
            if not destination.is_file() or sha256_file(destination) != source_hash:
                conflicts.append(destination)
                status = "conflict"
            else:
                status = "same"
        else:
            status = "created"
        planned.append((source, destination, source_hash, status))
    return planned, conflicts


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def export_dataset(
    records: list[PairRecord],
    store: ReviewStore,
    output_root: Path,
    include_visualizations: bool = True,
    require_labels: bool = False,
    allow_images_without_labels: bool = False,
) -> dict[str, Any]:
    output_root = canonical_path(output_root)
    exported_ids: set[str] = set()
    conflicts: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    artifacts_created = 0
    artifacts_same = 0
    for record in records:
        if store.decisions.get(record.sample_id) != "keep":
            continue
        if record.original_path is None or not record.original_path.is_file():
            issues.append({"sample_id": record.sample_id, "reason": "missing_original"})
            continue
        if require_labels and record.label_path is None and not allow_images_without_labels:
            issues.append({"sample_id": record.sample_id, "reason": "missing_label"})
            continue
        if any(anomaly.startswith("invalid_label") for anomaly in record.anomalies):
            issues.append({"sample_id": record.sample_id, "reason": "invalid_label"})
            continue
        original_rel = Path(record.original_rel or record.original_path.name)
        image_destination = output_root / "images" / original_rel
        artifacts: list[tuple[Path, Path]] = [(record.original_path, image_destination)]
        if record.label_path is not None:
            label_destination = output_root / "labels" / original_rel.with_suffix(".txt")
            artifacts.append((record.label_path, label_destination))
        if include_visualizations and record.visual_path is not None and record.visual_path.is_file():
            visual_rel = Path(record.visual_rel or record.visual_path.name)
            destination = output_root / "visualizations" / visual_rel
            artifacts.append((record.visual_path, destination))

        try:
            planned, sample_conflicts = preflight_artifacts(artifacts)
        except OSError as exc:
            issues.append({"sample_id": record.sample_id, "reason": f"read_error:{exc}"})
            continue
        if sample_conflicts:
            conflicts.extend(
                {"sample_id": record.sample_id, "path": str(destination)}
                for destination in sample_conflicts
            )
            continue
        try:
            for source, destination, source_hash, expected_status in planned:
                result = copy_if_same(source, destination, source_hash)
                if result == "conflict":
                    raise OSError(f"export target changed during copy: {destination}")
                if result != expected_status:
                    raise OSError(f"unexpected export status for {destination}: {result}")
                if result == "created":
                    artifacts_created += 1
                else:
                    artifacts_same += 1
        except OSError as exc:
            issues.append({"sample_id": record.sample_id, "reason": f"copy_error:{exc}"})
            continue
        exported_ids.add(record.sample_id)

    manifest_lines: list[str] = []
    for record in records:
        decision = store.decisions.get(record.sample_id)
        manifest_lines.append(
            json.dumps(
                {
                    "sample_id": record.sample_id,
                    "visual_image": str(record.visual_path) if record.visual_path else None,
                    "original_image": str(record.original_path) if record.original_path else None,
                    "label_file": str(record.label_path) if record.label_path else None,
                    "decision": decision,
                    "timestamp": utc_now(),
                    "exported": record.sample_id in exported_ids,
                    "anomalies": record.anomalies,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    atomic_write_text(output_root / "review_manifest.jsonl", "\n".join(manifest_lines) + ("\n" if manifest_lines else ""))
    summary = {
        "app_version": APP_VERSION,
        "exported": len(exported_ids),
        "kept": sum(store.decisions.get(record.sample_id) == "keep" for record in records),
        "rejected": sum(store.decisions.get(record.sample_id) == "reject" for record in records),
        "skipped": sum(store.decisions.get(record.sample_id) == "skip" for record in records),
        "unreviewed": sum(record.sample_id not in store.decisions for record in records),
        "conflicts": conflicts,
        "issues": issues,
        "artifacts_created": artifacts_created,
        "artifacts_unchanged": artifacts_same,
        "output_root": str(output_root),
        "timestamp": utc_now(),
    }
    atomic_write_text(
        output_root / "export_summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    return summary


def read_dimensions(path: Path | None) -> list[int] | None:
    if path is None or not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            return [int(image.width), int(image.height)]
    except (OSError, ValueError):
        return None


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据集图像审核</title>
<style>
:root { color-scheme: dark; --bg:#111827; --panel:#1f2937; --panel2:#273449; --line:#3a4a62; --text:#e5e7eb; --muted:#9ca3af; --accent:#22c55e; --danger:#ef4444; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; font:14px/1.45 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; background:linear-gradient(135deg,#0b1220,#172033 55%,#111827); color:var(--text); }
header { padding:18px 24px 10px; display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
h1 { margin:0; font-size:22px; letter-spacing:.02em; }
button, select, input { border:1px solid var(--line); background:var(--panel2); color:var(--text); border-radius:8px; padding:9px 13px; }
button { cursor:pointer; }
button:hover { border-color:#7dd3fc; transform:translateY(-1px); }
button.primary { background:#166534; border-color:#22c55e; }
button.reject { background:#7f1d1d; border-color:#ef4444; }
button:disabled { opacity:.45; cursor:not-allowed; transform:none; }
[hidden] { display:none !important; }
.layout { max-width:1500px; margin:auto; padding:0 24px 24px; }
.toolbar, .stats, .info, .statusbar { background:rgba(31,41,55,.9); border:1px solid var(--line); border-radius:12px; padding:12px; }
.toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
.toolbar .spacer { flex:1; }
.setup { max-width:880px; margin:35px auto; padding:24px; background:rgba(31,41,55,.94); border:1px solid var(--line); border-radius:12px; }
.setup h2 { margin:0 0 6px; font-size:20px; }
.setup p { color:var(--muted); margin:6px 0 18px; }
.setup-grid { display:grid; grid-template-columns:1fr auto; gap:10px; align-items:center; }
.setup-grid label { grid-column:1 / -1; color:var(--muted); font-size:12px; margin-top:4px; }
.setup-grid input { min-width:0; width:100%; }
.setup-options { display:flex; gap:12px; flex-wrap:wrap; margin:18px 0; color:var(--muted); }
.setup-options label { display:flex; align-items:center; gap:6px; }
.setup-options input { accent-color:var(--accent); }
.stats { display:grid; grid-template-columns:repeat(6,minmax(100px,1fr)); gap:8px; margin-bottom:12px; }
.stat { background:rgba(39,52,73,.75); padding:9px 12px; border-radius:8px; }
.stat b { display:block; font-size:20px; }
.stat span { color:var(--muted); font-size:12px; }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.card { min-width:0; background:rgba(31,41,55,.94); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.card h2 { font-size:14px; margin:0; padding:10px 13px; background:#253247; }
.stage { height:min(68vh,700px); min-height:300px; display:flex; justify-content:center; align-items:center; overflow:auto; background:#0b1020; }
.stage img { max-width:100%; max-height:100%; object-fit:contain; transform-origin:center; transition:transform .12s ease; }
.placeholder { color:var(--muted); text-align:center; padding:30px; }
.info { margin-top:12px; display:grid; grid-template-columns:repeat(4,1fr); gap:8px 18px; }
.info div { min-width:0; }
.label { color:var(--muted); font-size:12px; }
.value { overflow-wrap:anywhere; }
.warning { color:#fbbf24; }
.statusbar { margin-top:12px; min-height:44px; color:#bfdbfe; white-space:pre-wrap; }
.help { color:var(--muted); font-size:12px; }
@media (max-width:900px) { .pair { grid-template-columns:1fr; } .stats { grid-template-columns:repeat(3,1fr); } .info { grid-template-columns:repeat(2,1fr); } .stage { height:45vh; } }
</style>
</head>
<body>
<header><h1>数据集图像审核</h1><div class="help">Y/Enter 保留 · N/Delete 不保留 · ←/→ 翻页 · Z 撤销</div></header>
<main class="layout">
  <section class="setup" id="setupPanel" hidden>
    <h2>选择本机文件夹</h2>
    <p>点击按钮会打开当前电脑的系统目录选择器。路径只在本机审核服务中使用，不会上传到云端。</p>
    <div class="setup-grid">
      <label for="visualRoot">模型可视化图文件夹（必选）</label>
      <input id="visualRoot" readonly placeholder="请选择包含检测框或掩码可视化图的文件夹">
      <button data-directory="visual_root">选择文件夹</button>
      <label for="originalRoot">原图文件夹（必选）</label>
      <input id="originalRoot" readonly placeholder="请选择对应原始图片文件夹">
      <button data-directory="original_root">选择文件夹</button>
      <label for="labelRoot">YOLO 标签文件夹（可选）</label>
      <input id="labelRoot" readonly placeholder="无标签可留空">
      <button data-directory="label_root">选择文件夹</button>
      <label for="outputRoot">导出数据集文件夹（必选）</label>
      <input id="outputRoot" readonly placeholder="请选择一个已有的导出父目录">
      <button data-directory="output_root">选择文件夹</button>
    </div>
    <div class="setup-options">
      <label><input id="candidateOnly" type="checkbox"> 仅审核有模型可视化图的候选样本</label>
      <label><input id="includeVisualizations" type="checkbox" checked> 导出可视化图</label>
      <label><input id="allowImagesWithoutLabels" type="checkbox"> 允许导出无标签图片</label>
    </div>
    <button class="primary" id="configure">确认文件夹并开始审核</button>
    <div class="statusbar" id="setupStatus">请依次选择模型图、原图和导出文件夹。</div>
  </section>
  <div id="reviewUi" hidden>
  <div class="toolbar">
    <label>筛选 <select id="filter"><option value="all">全部</option><option value="unreviewed">未审核</option><option value="keep">已保留</option><option value="reject">已排除</option><option value="skip">已跳过</option><option value="error">配对异常</option></select></label>
    <button id="prev">上一张</button><button id="next">下一张</button>
    <button class="primary" data-decision="keep">保留</button><button class="reject" data-decision="reject">不保留</button><button data-decision="skip">跳过</button><button id="undo">撤销</button>
    <span class="spacer"></span><button id="zoomOut">−</button><button id="zoomReset">100%</button><button id="zoomIn">＋</button><button id="chooseFolders">重新选择文件夹</button><button id="rescan">重新扫描</button><button id="export">导出数据集</button>
  </div>
  <div class="stats" id="stats"></div>
  <div class="pair">
    <section class="card"><h2>模型可视化图</h2><div class="stage" id="visualStage"><div class="placeholder">暂无图片</div></div></section>
    <section class="card"><h2>对应原图</h2><div class="stage" id="originalStage"><div class="placeholder">暂无图片</div></div></section>
  </div>
  <div class="info" id="info"></div>
  <div class="statusbar" id="status">正在加载…</div>
  </div>
</main>
<script>
const state = { ready: false, records: [], decisions: {}, stats: {}, filter: "all", cursor: 0, scale: 1, detail: null };
const $ = id => document.getElementById(id);
const inputForDirectory = { visual_root: "visualRoot", original_root: "originalRoot", label_root: "labelRoot", output_root: "outputRoot" };
function setSetupStatus(message, error=false) { $("setupStatus").textContent = message; $("setupStatus").style.color = error ? "#fca5a5" : "#bfdbfe"; }
function showSetup(message="请选择文件夹后开始审核。") {
  $("setupPanel").hidden = false; $("reviewUi").hidden = true; setSetupStatus(message);
}
function showReview() { $("setupPanel").hidden = true; $("reviewUi").hidden = false; }
function fillSetup(config) {
  for (const [field, id] of Object.entries(inputForDirectory)) {
    if (config && config[field]) $(id).value = config[field];
  }
  if (config) {
    $("candidateOnly").checked = !!config.candidate_only;
    $("includeVisualizations").checked = config.include_visualizations !== false;
    $("allowImagesWithoutLabels").checked = !!config.allow_images_without_labels;
  }
}
async function chooseDirectory(field) {
  setSetupStatus("正在打开系统目录选择器，请在弹出的窗口中选择文件夹…");
  const response = await fetch("/api/select-directory", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({field})});
  const payload = await response.json();
  if (!response.ok) { setSetupStatus(payload.error || "无法打开目录选择器", true); return; }
  if (!payload.path) { setSetupStatus("未选择文件夹。"); return; }
  $(inputForDirectory[field]).value = payload.path;
  setSetupStatus(`已选择：${payload.path}`);
}
async function configureReview() {
  const visualRoot = $("visualRoot").value, originalRoot = $("originalRoot").value, outputRoot = $("outputRoot").value;
  if (!visualRoot || !originalRoot || !outputRoot) { setSetupStatus("请先选择模型图、原图和导出文件夹。", true); return; }
  setSetupStatus("正在扫描并建立审核列表…");
  const response = await fetch("/api/configure", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({
    visual_root: visualRoot, original_root: originalRoot, label_root: $("labelRoot").value || null, output_root: outputRoot,
    candidate_only: $("candidateOnly").checked, include_visualizations: $("includeVisualizations").checked,
    allow_images_without_labels: $("allowImagesWithoutLabels").checked
  })});
  const payload = await response.json();
  if (!response.ok) { setSetupStatus(payload.error || "配置失败", true); return; }
  state.cursor = 0; await reload(payload);
}
function currentList() {
  return state.records.filter(r => {
    const d = state.decisions[r.sample_id];
    if (state.filter === "all") return true;
    if (state.filter === "error") return r.has_error;
    if (state.filter === "unreviewed") return !d;
    return d === state.filter;
  });
}
function current() { const list = currentList(); return list[state.cursor] || list[0] || null; }
function setStatus(message, error=false) { $("status").textContent = message; $("status").style.color = error ? "#fca5a5" : "#bfdbfe"; }
function esc(text) { const div = document.createElement("div"); div.textContent = text ?? ""; return div.innerHTML; }
function renderStats() {
  const s = state.stats;
  $("stats").innerHTML = [["total","总图片"],["reviewed","已审核"],["keep","保留"],["reject","排除"],["skip","跳过"],["anomalies","异常"]].map(([k,n]) => `<div class="stat"><b>${s[k] ?? 0}</b><span>${n}</span></div>`).join("");
}
function fileUrl(kind, rel) { return rel ? `/files/${kind}/${encodeURIComponent(rel)}` : ""; }
function renderStage(id, kind, rel, alt) {
  const box = $(id);
  if (!rel) { box.innerHTML = `<div class="placeholder">${alt}<br><span class="warning">配对文件不存在</span></div>`; return; }
  box.innerHTML = `<img src="${fileUrl(kind, rel)}" alt="${esc(alt)}" style="transform:scale(${state.scale})" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'placeholder',textContent:'图片读取失败'}))">`;
}
async function loadDetail(record) {
  if (!record) return;
  const response = await fetch(`/api/record/${encodeURIComponent(record.sample_id)}`);
  state.detail = response.ok ? await response.json() : null;
}
async function render() {
  const list = currentList();
  if (state.cursor >= list.length) state.cursor = Math.max(0, list.length - 1);
  const record = current();
  renderStats();
  if (!record) {
    $("visualStage").innerHTML = '<div class="placeholder">当前筛选没有图片</div>';
    $("originalStage").innerHTML = '<div class="placeholder">当前筛选没有图片</div>';
    document.querySelector('[data-decision="keep"]').disabled = true;
    $("info").innerHTML = ""; setStatus("当前筛选没有可显示的图片"); return;
  }
  await loadDetail(record);
  const d = state.decisions[record.sample_id] || "未审核";
  document.querySelector('[data-decision="keep"]').disabled = !record.original_rel;
  renderStage("visualStage", "visual", record.visual_rel, "模型可视化图");
  renderStage("originalStage", "original", record.original_rel, "原图");
  const detail = state.detail || {};
  $("info").innerHTML = [
    ["进度", `${state.cursor + 1} / ${list.length}（总计 ${state.records.length}）`],
    ["文件名", record.visual_rel || record.original_rel || "未知"],
    ["模型图尺寸", detail.visual_dimensions ? detail.visual_dimensions.join(" × ") : "未知"],
    ["原图尺寸", detail.original_dimensions ? detail.original_dimensions.join(" × ") : "未知"],
    ["标签", record.label_rel ? "存在" : "不存在"],
    ["审核状态", d],
    ["样本 ID", record.sample_id],
    ["配对状态", record.anomalies.length ? `<span class="warning">${esc(record.anomalies.join(", "))}</span>` : "正常"],
  ].map(([k,v]) => `<div><div class="label">${k}</div><div class="value">${v}</div></div>`).join("");
  setStatus(`当前：${record.visual_rel || record.original_rel} · ${d} · 缩放 ${Math.round(state.scale*100)}%`);
}
async function reload(existingPayload=null) {
  const response = existingPayload ? null : await fetch("/api/state");
  if (!existingPayload && !response.ok) { setStatus("加载状态失败", true); return; }
  const payload = existingPayload || await response.json();
  state.ready = !!payload.ready;
  fillSetup(payload.config);
  if (!state.ready) { showSetup(); return; }
  showReview();
  state.records = payload.records; state.decisions = payload.decisions; state.stats = payload.stats;
  const list = currentList(); if (state.cursor >= list.length) state.cursor = Math.max(0, list.length - 1);
  await render();
}
async function decide(decision) {
  const record = current(); if (!record) return;
  const response = await fetch("/api/decision", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({sample_id:record.sample_id, decision})});
  const payload = await response.json();
  if (!response.ok) { setStatus(payload.error || "保存失败", true); return; }
  state.decisions = payload.decisions; state.stats = payload.stats;
  const list = currentList(); state.cursor = Math.min(state.cursor + 1, Math.max(0, list.length - 1)); await render();
}
async function undo() {
  const response = await fetch("/api/undo", {method:"POST"});
  const payload = await response.json();
  if (!response.ok) { setStatus(payload.error || "撤销失败", true); return; }
  state.decisions = payload.decisions; state.stats = payload.stats; await render(); setStatus(`已撤销：${payload.sample_id || "无可撤销操作"}`);
}
async function exportData() {
  setStatus("正在导出保留样本…");
  const response = await fetch("/api/export", {method:"POST"});
  const payload = await response.json();
  if (!response.ok) { setStatus(payload.error || "导出失败", true); return; }
  setStatus(`导出完成：${payload.exported} 个样本；冲突 ${payload.conflicts.length}；问题 ${payload.issues.length}`);
}
document.querySelectorAll("[data-decision]").forEach(button => button.onclick = () => decide(button.dataset.decision));
document.querySelectorAll("[data-directory]").forEach(button => button.onclick = () => chooseDirectory(button.dataset.directory));
$("configure").onclick = configureReview;
$("prev").onclick = () => { state.cursor = Math.max(0, state.cursor - 1); render(); };
$("next").onclick = () => { state.cursor = Math.min(currentList().length - 1, state.cursor + 1); render(); };
$("undo").onclick = undo; $("export").onclick = exportData;
$("chooseFolders").onclick = () => showSetup("重新选择并确认文件夹会切换到新的审核会话；源文件不会被修改。");
$("rescan").onclick = async () => {
  const response = await fetch("/api/rescan", {method:"POST"});
  if (!response.ok) { setStatus("重新扫描失败", true); return; }
  const payload = await response.json();
  state.records = payload.records; state.decisions = payload.decisions; state.stats = payload.stats;
  state.cursor = 0; await render(); setStatus(`已重新扫描：${state.records.length} 个模型可视化样本`);
};
$("filter").onchange = event => { state.filter = event.target.value; state.cursor = 0; render(); };
$("zoomIn").onclick = () => { state.scale = Math.min(2, +(state.scale + .1).toFixed(2)); render(); };
$("zoomOut").onclick = () => { state.scale = Math.max(.4, +(state.scale - .1).toFixed(2)); render(); };
$("zoomReset").onclick = () => { state.scale = 1; render(); };
document.onkeydown = event => {
  if (["INPUT","SELECT","TEXTAREA"].includes(document.activeElement.tagName)) return;
  if (event.key === "y" || event.key === "Y" || event.key === "Enter") { event.preventDefault(); decide("keep"); }
  else if (event.key === "n" || event.key === "N" || event.key === "Delete") { event.preventDefault(); decide("reject"); }
  else if (event.key === "ArrowLeft") { event.preventDefault(); $("prev").click(); }
  else if (event.key === "ArrowRight") { event.preventDefault(); $("next").click(); }
  else if (event.key === "z" || event.key === "Z") { event.preventDefault(); undo(); }
};
reload();
</script>
</body>
</html>
"""


class ReviewApplication:
    def __init__(
        self,
        engine: PairingEngine | None = None,
        store: ReviewStore | None = None,
        output_root: Path | None = None,
        include_visualizations: bool = True,
        allow_images_without_labels: bool = False,
        candidate_only: bool = False,
        instance_id: str | None = None,
    ) -> None:
        if (engine is None) != (store is None) or (engine is None) != (output_root is None):
            raise ValueError("engine、store 和 output_root 必须同时提供，或全部留空以使用网页选目录模式")
        self.engine = engine
        self.store = store
        self.output_root = canonical_path(output_root) if output_root is not None else None
        self.include_visualizations = include_visualizations
        self.allow_images_without_labels = allow_images_without_labels
        self.candidate_only = candidate_only
        self.instance_id = instance_id or uuid.uuid4().hex
        self.lock = threading.RLock()
        self.records = self.engine.scan() if self.engine is not None else []

    @property
    def ready(self) -> bool:
        return self.engine is not None and self.store is not None and self.output_root is not None

    def require_ready(self) -> tuple[PairingEngine, ReviewStore, Path]:
        if not self.ready:
            raise ValueError("请先在网页中选择并确认文件夹")
        assert self.engine is not None
        assert self.store is not None
        assert self.output_root is not None
        return self.engine, self.store, self.output_root

    def configure_from_directories(
        self,
        visual_root: Path,
        original_root: Path,
        output_root: Path,
        label_root: Path | None = None,
        manifest_path: Path | None = None,
        candidate_only: bool = False,
        include_visualizations: bool = True,
        allow_images_without_labels: bool = False,
    ) -> None:
        visual_root = canonical_path(visual_root)
        original_root = canonical_path(original_root)
        output_root = canonical_path(output_root)
        label_root = canonical_path(label_root) if label_root is not None else None
        manifest_path = canonical_path(manifest_path) if manifest_path is not None else None
        for name, path in (("可视化图文件夹", visual_root), ("原图文件夹", original_root)):
            if not path.is_dir():
                raise ValueError(f"{name}不存在或不是目录: {path}")
        if label_root is not None and not label_root.is_dir():
            raise ValueError(f"标签文件夹不存在或不是目录: {label_root}")
        if manifest_path is not None and not manifest_path.is_file():
            raise ValueError(f"配对清单不存在或不是文件: {manifest_path}")

        engine = PairingEngine(
            visual_root,
            original_root,
            label_root,
            manifest_path,
            include_original_only=not candidate_only,
        )
        records = engine.scan()
        store = ReviewStore(output_root / ".review_state.json")
        with self.lock:
            self.engine = engine
            self.store = store
            self.output_root = output_root
            self.include_visualizations = include_visualizations
            self.allow_images_without_labels = allow_images_without_labels
            self.candidate_only = candidate_only
            self.records = records

    def rescan(self) -> None:
        engine, _, _ = self.require_ready()
        with self.lock:
            self.records = engine.scan()

    def set_decision(self, sample_id: str, decision: str) -> None:
        _, store, _ = self.require_ready()
        with self.lock:
            record = next((item for item in self.records if item.sample_id == sample_id), None)
            if record is None:
                raise ValueError("sample not found")
            if decision == "keep" and (record.original_path is None or not record.original_path.is_file()):
                raise ValueError("缺失原图的样本不能保留")
            store.set_decision(sample_id, decision)

    def state_payload(self) -> dict[str, Any]:
        with self.lock:
            if not self.ready:
                return {
                    "app_version": APP_VERSION,
                    "instance_id": self.instance_id,
                    "ready": False,
                    "records": [],
                    "decisions": {},
                    "stats": empty_stats(),
                    "config": {
                        "visual_root": None,
                        "original_root": None,
                        "label_root": None,
                        "output_root": None,
                        "candidate_only": self.candidate_only,
                        "include_visualizations": self.include_visualizations,
                        "allow_images_without_labels": self.allow_images_without_labels,
                    },
                }
            engine, store, output_root = self.require_ready()
            return {
                "app_version": APP_VERSION,
                "instance_id": self.instance_id,
                "ready": True,
                "records": [record.to_dict(store.decisions.get(record.sample_id)) for record in self.records],
                "decisions": dict(store.decisions),
                "stats": store.stats(self.records),
                "config": {
                    "visual_root": str(engine.visual_root),
                    "original_root": str(engine.original_root),
                    "label_root": str(engine.label_root) if engine.label_root else None,
                    "output_root": str(output_root),
                    "candidate_only": self.candidate_only,
                    "include_visualizations": self.include_visualizations,
                    "allow_images_without_labels": self.allow_images_without_labels,
                },
            }

    def detail(self, sample_id: str) -> dict[str, Any] | None:
        _, store, _ = self.require_ready()
        with self.lock:
            for record in self.records:
                if record.sample_id == sample_id:
                    return {
                        **record.to_dict(store.decisions.get(sample_id)),
                        "visual_dimensions": read_dimensions(record.visual_path),
                        "original_dimensions": read_dimensions(record.original_path),
                    }
        return None


class RequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "DatasetReview/1.0"

    @property
    def application(self) -> ReviewApplication:
        return self.server.application  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{utc_now()}] {self.address_string()} {format % args}", flush=True)

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, data: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        encoded = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("request too large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.send_text(HTML_PAGE)
            return
        if path == "/api/state":
            self.send_json(self.application.state_payload())
            return
        if path.startswith("/api/record/"):
            sample_id = path.rsplit("/", 1)[-1]
            detail = self.application.detail(sample_id)
            if detail is None:
                self.send_json({"error": "sample not found"}, 404)
            else:
                self.send_json(detail)
            return
        if path.startswith("/files/"):
            self.serve_file(path)
            return
        self.send_json({"error": "not found"}, 404)

    def serve_file(self, path: str) -> None:
        parts = path.split("/", 3)
        if len(parts) != 4 or parts[2] not in {"visual", "original"}:
            self.send_json({"error": "not found"}, 404)
            return
        if not self.application.ready:
            self.send_json({"error": "请先在网页中选择并确认文件夹"}, 409)
            return
        engine, _, _ = self.application.require_ready()
        root = engine.visual_root if parts[2] == "visual" else engine.original_root
        relative = Path(parts[3])
        file_path = (root / relative).resolve()
        if not is_inside(file_path, root) or not file_path.is_file() or file_path.suffix.lower() not in IMAGE_SUFFIXES:
            self.send_json({"error": "file not found"}, 404)
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            self.send_json({"error": "file read failed"}, 500)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/select-directory":
                payload = self.read_json()
                selected = self.server.select_directory(str(payload["field"]))  # type: ignore[attr-defined]
                self.send_json({"path": str(selected) if selected is not None else None})
                return
            if parsed.path == "/api/configure":
                payload = self.read_json()

                def required_path(key: str) -> Path:
                    value = payload.get(key)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"{key} 必须是非空目录路径")
                    return Path(value)

                def optional_path(key: str) -> Path | None:
                    value = payload.get(key)
                    if value in (None, ""):
                        return None
                    if not isinstance(value, str):
                        raise ValueError(f"{key} 必须是目录路径或 null")
                    return Path(value)

                def boolean(key: str, default: bool) -> bool:
                    value = payload.get(key, default)
                    if not isinstance(value, bool):
                        raise ValueError(f"{key} 必须是 true 或 false")
                    return value

                self.application.configure_from_directories(
                    required_path("visual_root"),
                    required_path("original_root"),
                    required_path("output_root"),
                    label_root=optional_path("label_root"),
                    manifest_path=optional_path("manifest"),
                    candidate_only=boolean("candidate_only", False),
                    include_visualizations=boolean("include_visualizations", True),
                    allow_images_without_labels=boolean("allow_images_without_labels", False),
                )
                self.send_json(self.application.state_payload())
                return
            if parsed.path == "/api/decision":
                payload = self.read_json()
                self.application.set_decision(str(payload["sample_id"]), str(payload["decision"]))
                _, store, _ = self.application.require_ready()
                self.send_json(
                    {
                        "decisions": dict(store.decisions),
                        "stats": store.stats(self.application.records),
                    }
                )
                return
            if parsed.path == "/api/undo":
                _, store, _ = self.application.require_ready()
                sample_id = store.undo()
                self.send_json(
                    {
                        "sample_id": sample_id,
                        "decisions": dict(store.decisions),
                        "stats": store.stats(self.application.records),
                    }
                )
                return
            if parsed.path == "/api/export":
                engine, store, output_root = self.application.require_ready()
                summary = export_dataset(
                    self.application.records,
                    store,
                    output_root,
                    include_visualizations=self.application.include_visualizations,
                    require_labels=engine.label_root is not None,
                    allow_images_without_labels=self.application.allow_images_without_labels,
                )
                self.send_json(summary)
                return
            if parsed.path == "/api/rescan":
                self.application.rescan()
                self.send_json(self.application.state_payload())
                return
            self.send_json({"error": "not found"}, 404)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)


class ReviewServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], application: ReviewApplication):
        self.application = application
        self.directory_picker = DirectoryPicker()
        super().__init__(address, RequestHandler)

    def select_directory(self, field: str) -> Path | None:
        titles = {
            "visual_root": "选择模型可视化图文件夹",
            "original_root": "选择原图文件夹",
            "label_root": "选择 YOLO 标签文件夹（可取消）",
            "output_root": "选择导出数据集文件夹",
        }
        if field not in titles:
            raise ValueError("未知目录选择项")
        return self.directory_picker.choose(titles[field])

    def process_directory_requests(self) -> None:
        self.directory_picker.process_pending()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual-root", type=Path, default=None, help="模型可视化图片目录")
    parser.add_argument("--original-root", type=Path, default=None, help="原始图片目录")
    parser.add_argument("--label-root", type=Path, default=None, help="可选 YOLO 标签目录")
    parser.add_argument("--manifest", type=Path, default=None, help="可选 CSV/JSON/JSONL 显式配对清单")
    parser.add_argument("--output-root", type=Path, default=None, help="导出数据集目录")
    parser.add_argument("--state-file", type=Path, default=None, help="审核状态 JSON；默认写入 output-root/.review_state.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-visualizations", action="store_true", help="导出时不复制模型可视化图")
    parser.add_argument("--allow-images-without-labels", action="store_true", help="允许无标签样本仅复制图片")
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="仅以模型可视化图为审核候选；不列出缺少可视化图的原图",
    )
    parser.add_argument(
        "--web-setup",
        action="store_true",
        help="启动后通过网页中的系统文件夹选择器设置输入和导出目录",
    )
    parser.add_argument("--instance-id", default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        configured_roots = (args.visual_root, args.original_root, args.output_root)
        if args.web_setup:
            if args.host != "127.0.0.1":
                raise ValueError("网页文件夹选择模式仅允许监听 127.0.0.1")
            application = ReviewApplication(instance_id=args.instance_id)
        else:
            if any(path is None for path in configured_roots):
                raise ValueError(
                    "visual-root、original-root 和 output-root 必须同时提供；"
                    "或使用 --web-setup 在网页中选择文件夹"
                )
            assert args.visual_root is not None
            assert args.original_root is not None
            assert args.output_root is not None
            if not args.visual_root.is_dir():
                raise ValueError(f"visual-root 不存在或不是目录: {args.visual_root}")
            if not args.original_root.is_dir():
                raise ValueError(f"original-root 不存在或不是目录: {args.original_root}")
            if args.label_root and not args.label_root.is_dir():
                raise ValueError(f"label-root 不存在或不是目录: {args.label_root}")
            state_file = args.state_file or (args.output_root / ".review_state.json")
            engine = PairingEngine(
                args.visual_root,
                args.original_root,
                args.label_root,
                args.manifest,
                include_original_only=not args.candidate_only,
            )
            application = ReviewApplication(
                engine,
                ReviewStore(state_file),
                args.output_root,
                include_visualizations=not args.no_visualizations,
                allow_images_without_labels=args.allow_images_without_labels,
                candidate_only=args.candidate_only,
                instance_id=args.instance_id,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    server = ReviewServer((args.host, args.port), application)
    print(f"数据集图像审核工具已启动: http://{args.host}:{args.port}", flush=True)
    if application.ready:
        _, store, _ = application.require_ready()
        print(f"配对样本: {len(application.records)}; 状态文件: {store.path}", flush=True)
    else:
        print("请在浏览器中点击“选择文件夹”，完成配置后开始审核。", flush=True)
    print("按 Ctrl+C 停止。源文件只读，导出操作只复制保留样本。", flush=True)
    try:
        server.timeout = 0.2
        while True:
            server.handle_request()
            server.process_directory_requests()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    main()
