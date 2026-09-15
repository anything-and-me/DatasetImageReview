from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from tools.dataset_image_review.app import PairingEngine, ReviewApplication, ReviewStore, export_dataset
except ModuleNotFoundError:
    from app import PairingEngine, ReviewApplication, ReviewStore, export_dataset


def write_file(path: Path, data: bytes = b"image") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


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


if __name__ == "__main__":
    unittest.main()
