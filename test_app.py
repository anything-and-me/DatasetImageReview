from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

try:
    from tools.dataset_image_review.app import (
        PairingEngine,
        ReviewApplication,
        ReviewServer,
        ReviewStore,
        export_dataset,
        run_preflight,
    )
except ModuleNotFoundError:
    from app import PairingEngine, ReviewApplication, ReviewServer, ReviewStore, export_dataset, run_preflight


def write_file(path: Path, data: bytes = b"image") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_image(path: Path, color: tuple[int, int, int] = (128, 128, 128), size: tuple[int, int] = (160, 120)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


class PairingTests(unittest.TestCase):
    def test_relative_path_pairing_and_label_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            labels = root / "labels"
            write_file(visual / "scene" / "frame_001.jpg", b"visual")
            write_file(original / "scene" / "frame_001.jpg", b"original")
            write_file(labels / "scene" / "frame_001.txt", b"0 0.5 0.5 0.2 0.2\n")

            records = PairingEngine(visual, original, labels).scan()

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].anomalies, [])
            self.assertEqual(records[0].visual_rel, "scene/frame_001.jpg")
            self.assertEqual(records[0].original_rel, "scene/frame_001.jpg")
            self.assertEqual(records[0].label_rel, "scene/frame_001.txt")

    def test_original_images_are_sufficient_when_visualizations_are_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            write_image(original / "scene" / "frame_001.jpg")

            records = PairingEngine(None, original).scan()

            self.assertEqual(len(records), 1)
            self.assertIsNone(records[0].visual_path)
            self.assertIsNone(records[0].visual_rel)
            self.assertEqual(records[0].anomalies, [])

    def test_missing_pair_and_duplicate_stem_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            write_file(visual / "a" / "same.jpg")
            write_file(visual / "missing.jpg")
            write_file(original / "x" / "same.jpg")
            write_file(original / "y" / "same.png")

            records = PairingEngine(visual, original).scan()
            missing = next(record for record in records if record.visual_rel == "missing.jpg")
            duplicate = next(
                record
                for record in records
                if record.visual_rel == "a/same.jpg" and "duplicate_stem" in record.anomalies
            )

            self.assertIn("missing_original", missing.anomalies)
            self.assertIn("duplicate_stem", duplicate.anomalies)
            original_only = [record for record in records if "missing_visual" in record.anomalies]
            self.assertEqual(len(original_only), 2)
            self.assertEqual(len(records), 4)

            candidate_records = PairingEngine(
                visual,
                original,
                include_original_only=False,
            ).scan()
            self.assertEqual(len(candidate_records), 2)

    def test_relative_pairing_is_not_marked_duplicate_from_other_direct_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            write_file(visual / "a" / "same.jpg")
            write_file(visual / "b" / "same.jpg")
            write_file(original / "a" / "same.jpg")
            write_file(original / "b" / "same.jpg")

            records = PairingEngine(visual, original).scan()

            self.assertEqual(len(records), 2)
            self.assertTrue(all(record.anomalies == [] for record in records))

    def test_manifest_pairing_accepts_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            manifest = root / "pairs.csv"
            write_file(visual / "render.jpg")
            write_file(original / "source.jpg")
            manifest.write_text(
                "sample_id,visual_image,original_image\n"
                "custom-1,visual/render.jpg,original/source.jpg\n",
                encoding="utf-8",
            )

            records = PairingEngine(
                visual,
                original,
                manifest_path=manifest,
            ).scan()

            self.assertEqual(records[0].sample_id, "custom-1")
            self.assertEqual(records[0].anomalies, [])


class PersistenceAndExportTests(unittest.TestCase):
    def test_missing_original_cannot_be_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            write_file(visual / "missing.jpg")
            application = ReviewApplication(
                PairingEngine(visual, original),
                ReviewStore(root / "state.json"),
                root / "export",
            )
            record = application.records[0]

            with self.assertRaisesRegex(ValueError, "缺失原图"):
                application.set_decision(record.sample_id, "keep")
            self.assertNotIn(record.sample_id, application.store.decisions)

    def test_state_recovers_and_undoes_last_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            store = ReviewStore(state_path)
            store.set_decision("sample-1", "keep")
            store.set_decision("sample-2", "reject")

            recovered = ReviewStore(state_path)
            self.assertEqual(recovered.decisions["sample-1"], "keep")
            self.assertEqual(recovered.decisions["sample-2"], "reject")
            recovered.undo()
            self.assertNotIn("sample-2", recovered.decisions)

    def test_export_only_kept_samples_and_preserves_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            labels = root / "labels"
            output = root / "export"
            write_file(visual / "scene" / "keep.jpg", b"visual-keep")
            write_file(original / "scene" / "keep.jpg", b"original-keep")
            write_file(labels / "scene" / "keep.txt", b"0 0.5 0.5 0.2 0.2\n")
            write_file(visual / "scene" / "reject.jpg", b"visual-reject")
            write_file(original / "scene" / "reject.jpg", b"original-reject")
            records = PairingEngine(visual, original, labels).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")
            store.set_decision(records[1].sample_id, "reject")
            source_digest = hashlib.sha256((original / "scene" / "keep.jpg").read_bytes()).hexdigest()

            result = export_dataset(records, store, output)

            self.assertEqual(result["exported"], 1)
            self.assertEqual((output / "images/scene/keep.jpg").read_bytes(), b"original-keep")
            self.assertEqual((output / "labels/scene/keep.txt").read_bytes(), b"0 0.5 0.5 0.2 0.2\n")
            self.assertFalse((output / "images/scene/reject.jpg").exists())
            self.assertEqual(
                hashlib.sha256((original / "scene/keep.jpg").read_bytes()).hexdigest(),
                source_digest,
            )

            second = export_dataset(records, store, output)

            self.assertEqual(second["exported"], 1)
            self.assertEqual(second["conflicts"], [])
            manifest = [
                json.loads(line)
                for line in (output / "review_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(item["decision"] == "keep" and item["exported"] for item in manifest))

    def test_export_reports_conflict_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            output = root / "export"
            write_file(visual / "keep.jpg", b"visual")
            write_file(original / "keep.jpg", b"source")
            records = PairingEngine(visual, original).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")
            write_file(output / "images" / "keep.jpg", b"existing-different")

            result = export_dataset(records, store, output)

            self.assertEqual(result["exported"], 0)
            self.assertEqual(len(result["conflicts"]), 1)
            self.assertEqual((output / "images/keep.jpg").read_bytes(), b"existing-different")

    def test_export_preflights_label_conflict_before_copying_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            labels = root / "labels"
            output = root / "export"
            write_file(visual / "keep.jpg", b"visual")
            write_file(original / "keep.jpg", b"source")
            write_file(labels / "keep.txt", b"0 0.5 0.5 0.2 0.2\n")
            records = PairingEngine(visual, original, labels).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")
            write_file(output / "labels" / "keep.txt", b"0 0.2 0.2 0.1 0.1\n")

            result = export_dataset(records, store, output)

            self.assertEqual(result["exported"], 0)
            self.assertEqual(len(result["conflicts"]), 1)
            self.assertFalse((output / "images" / "keep.jpg").exists())
            self.assertFalse((output / "visualizations" / "keep.jpg").exists())
            self.assertEqual(
                (output / "labels" / "keep.txt").read_bytes(),
                b"0 0.2 0.2 0.1 0.1\n",
            )

    def test_export_generates_annotation_guide_and_selected_platform_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            labels = root / "labels"
            output = root / "export"
            write_image(visual / "scene" / "keep.jpg", (0, 255, 0))
            write_image(original / "scene" / "keep.jpg", (0, 0, 255))
            write_file(labels / "scene" / "keep.txt", b"0 0.5 0.5 0.2 0.4\n")
            records = PairingEngine(visual, original, labels).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")

            result = export_dataset(
                records,
                store,
                output,
                formats={"yolo", "coco", "cvat", "label_studio"},
                class_names=["bottle"],
            )

            self.assertEqual(result["exported"], 1)
            self.assertEqual(result["formats"], ["coco", "cvat", "label_studio", "yolo"])
            self.assertTrue((output / "images/scene/keep.jpg").is_file())
            self.assertTrue((output / "data.yaml").is_file())
            coco = json.loads((output / "coco/annotations.json").read_text(encoding="utf-8"))
            self.assertEqual(coco["categories"], [{"id": 0, "name": "bottle", "supercategory": "object"}])
            self.assertEqual(coco["annotations"][0]["bbox"], [64.0, 36.0, 32.0, 48.0])
            self.assertTrue((output / "cvat/data.yaml").is_file())
            label_studio = json.loads((output / "label_studio/tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(label_studio[0]["predictions"][0]["result"][0]["value"]["rectanglelabels"], ["bottle"])
            guide = (output / "annotation_guide/README.md").read_text(encoding="utf-8")
            self.assertIn("bottle", guide)
            self.assertTrue((output / "annotation_guide/classes.txt").is_file())

    def test_export_original_only_sample_without_preannotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            output = root / "export"
            write_image(original / "scene" / "keep.jpg", (20, 40, 60))
            records = PairingEngine(None, original).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")
            source_digest = hashlib.sha256((original / "scene" / "keep.jpg").read_bytes()).hexdigest()

            result = export_dataset(records, store, output)

            self.assertEqual(result["exported"], 1)
            self.assertTrue((output / "images/scene/keep.jpg").is_file())
            self.assertFalse((output / "labels/scene/keep.txt").exists())
            self.assertFalse((output / "visualizations/scene/keep.jpg").exists())
            self.assertEqual(
                hashlib.sha256((original / "scene" / "keep.jpg").read_bytes()).hexdigest(),
                source_digest,
            )

    def test_metadata_conflict_is_reported_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            output = root / "export"
            write_image(visual / "keep.jpg", (1, 2, 3))
            write_image(original / "keep.jpg", (4, 5, 6))
            records = PairingEngine(visual, original).scan()
            store = ReviewStore(root / "state.json")
            store.set_decision(records[0].sample_id, "keep")
            export_dataset(records, store, output, formats={"coco"})
            annotations_path = output / "coco" / "annotations.json"
            annotations_path.write_text('{"manually":"edited"}\n', encoding="utf-8")

            result = export_dataset(records, store, output, formats={"coco"})

            self.assertTrue(
                any(item["path"] == str(annotations_path) for item in result["conflicts"])
            )
            self.assertEqual(annotations_path.read_text(encoding="utf-8"), '{"manually":"edited"}\n')

    def test_out_of_bounds_label_is_rejected_before_platform_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            labels = root / "labels"
            write_image(visual / "keep.jpg")
            write_image(original / "keep.jpg")
            write_file(labels / "keep.txt", b"0 0.9 0.5 0.5 0.2\n")

            record = PairingEngine(visual, original, labels).scan()[0]

            self.assertIn("invalid_label:1", record.anomalies)


class QualityPreflightTests(unittest.TestCase):
    def test_preflight_reports_corrupt_small_low_quality_and_duplicate_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            write_image(visual / "good.jpg", (40, 80, 120))
            write_image(original / "good.jpg", (40, 80, 120))
            write_image(visual / "duplicate.jpg", (40, 80, 120))
            write_image(original / "duplicate.jpg", (40, 80, 120))
            write_image(visual / "small.jpg", (10, 10, 10), (24, 24))
            write_image(original / "small.jpg", (10, 10, 10), (24, 24))
            write_file(visual / "broken.jpg", b"not-an-image")
            write_file(original / "broken.jpg", b"not-an-image")
            records = PairingEngine(visual, original).scan()

            report = run_preflight(records)

            self.assertEqual(report["summary"]["total"], 4)
            self.assertEqual(report["summary"]["corrupt"], 1)
            self.assertGreaterEqual(report["summary"]["quality_issues"], 1)
            self.assertEqual(report["summary"]["exact_duplicates"], 1)
            broken = next(item for item in report["samples"] if item["original_rel"] == "broken.jpg")
            small = next(item for item in report["samples"] if item["original_rel"] == "small.jpg")
            self.assertIn("corrupt_image", broken["issues"])
            self.assertIn("small_image", small["issues"])
            self.assertEqual(report["summary"]["exact_duplicates"], 1)
            self.assertTrue(
                any(
                    item["duplicate"] and item["duplicate"]["kind"] == "exact"
                    for item in report["samples"]
                )
            )

    def test_duplicate_candidates_can_be_rejected_in_one_reversible_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            output = root / "export"
            write_image(visual / "representative.jpg", (40, 80, 120))
            write_image(original / "representative.jpg", (40, 80, 120))
            write_image(visual / "duplicate.jpg", (40, 80, 120))
            write_image(original / "duplicate.jpg", (40, 80, 120))
            application = ReviewApplication(
                PairingEngine(visual, original),
                ReviewStore(output / ".review_state.json"),
                output,
            )
            application.run_quality_preflight()

            affected = application.reject_duplicate_candidates()

            self.assertEqual(affected, 1)
            duplicate = next(
                item
                for item in application.preflight_report["samples"]
                if item["duplicate"] is not None
            )
            self.assertEqual(application.store.decisions[duplicate["sample_id"]], "reject")
            application.store.undo()
            self.assertNotIn(duplicate["sample_id"], application.store.decisions)

    def test_preflight_report_restores_for_the_same_input_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            output = root / "export"
            write_image(original / "frame.jpg", (80, 90, 100))
            first = ReviewApplication()
            first.configure_from_directories(None, original, output)
            report = first.run_quality_preflight()

            restarted = ReviewApplication()
            restarted.configure_from_directories(None, original, output)

            self.assertIsNotNone(restarted.preflight_report)
            self.assertEqual(restarted.preflight_report["fingerprint"], report["fingerprint"])
            self.assertTrue(restarted.state_payload()["preflight"]["completed"])


class WebSetupTests(unittest.TestCase):
    def test_unconfigured_application_can_configure_selected_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            output = root / "export"
            write_image(visual / "scene" / "frame.jpg")
            write_image(original / "scene" / "frame.jpg")

            application = ReviewApplication()

            self.assertFalse(application.state_payload()["ready"])
            application.configure_from_directories(visual, original, output)
            payload = application.state_payload()

            self.assertTrue(payload["ready"])
            self.assertEqual(payload["config"]["visual_root"], str(visual.resolve()))
            self.assertEqual(payload["config"]["original_root"], str(original.resolve()))
            self.assertFalse(payload["config"]["candidate_only"])
            self.assertTrue(payload["config"]["include_visualizations"])
            self.assertEqual(len(payload["records"]), 1)

    def test_web_configuration_accepts_original_only_and_rejects_overlapping_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            output = root / "export"
            write_image(original / "scene" / "frame.jpg")
            application = ReviewApplication()

            application.configure_from_directories(None, original, output)

            self.assertIsNone(application.state_payload()["config"]["visual_root"])
            self.assertEqual(len(application.records), 1)
            with self.assertRaisesRegex(ValueError, "导出目录不能与原图文件夹"):
                application.configure_from_directories(None, original, original)
            with self.assertRaisesRegex(ValueError, "必须提供模型可视化图"):
                application.configure_from_directories(None, original, output, candidate_only=True)

    def test_web_configure_endpoint_initializes_the_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            visual = root / "visual"
            original = root / "original"
            output = root / "export"
            write_image(visual / "frame.jpg")
            write_image(original / "frame.jpg")

            server = ReviewServer(("127.0.0.1", 0), ReviewApplication())
            server.timeout = 0.05
            stop = threading.Event()

            def serve_web_setup() -> None:
                while not stop.is_set():
                    server.handle_request()
                    server.process_directory_requests()

            server.directory_picker._show_dialog = lambda title: visual  # type: ignore[method-assign]
            worker = threading.Thread(target=serve_web_setup, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", "/api/state")
                initial = connection.getresponse()
                self.assertEqual(initial.status, 200)
                self.assertFalse(json.loads(initial.read())["ready"])

                connection.request(
                    "POST",
                    "/api/select-directory",
                    body=json.dumps({"field": "visual_root"}),
                    headers={"Content-Type": "application/json"},
                )
                selected_response = connection.getresponse()
                selected_payload = json.loads(selected_response.read())
                self.assertEqual(selected_response.status, 200)
                self.assertEqual(selected_payload["path"], str(visual.resolve()))

                request = {
                    "visual_root": str(visual),
                    "original_root": str(original),
                    "output_root": str(output),
                }
                connection.request(
                    "POST",
                    "/api/configure",
                    body=json.dumps(request),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())

                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ready"])
                self.assertEqual(len(payload["records"]), 1)
            finally:
                connection.close()
                stop.set()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
