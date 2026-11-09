"""Comparison rendering must report this batch, including future unblocked runs."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from probes.comparison import write_comparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="probe_comparison_")
        self.root = Path(self.temp.name)
        self.output = self.root / "results" / "batch"
        self.output.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def row(self, probe="patchcore", image="realpart_img1", status="ok", reason="completed",
            artifact=None, record=True, duration=1234.0, metrics=None):
        directory = self.output / probe / image
        directory.mkdir(parents=True, exist_ok=True)
        if artifact:
            (directory / artifact).write_bytes(b"rendering-test-placeholder")
        value = {"probe_name": probe, "frame_id": image, "status": status,
                 "status_reason": reason, "reason_codes": [], "metrics": metrics or {},
                 "duration_ms": duration if status == "ok" else None,
                 "peak_allocated_mib": 12.0 if status == "ok" else None,
                 "peak_reserved_mib": 16.0 if status == "ok" else None}
        if record:
            (directory / "metrics.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def render(self, rows):
        write_comparison(self.root, self.output, {"results": rows})
        return (self.output / "comparison.md").read_text(encoding="utf-8")

    def method_line(self, text, name):
        return next(line for line in text.splitlines() if line.startswith(f"| {name} |"))

    def test_real_patchcore_success_is_not_labelled_blocked(self):
        text = self.render([self.row(artifact="overlay.png")])
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("realpart_img1=ok", line)
        self.assertIn("参考库的异常距离", line)
        self.assertNotIn("blocked", line)
        self.assertNotIn("缺同设计", line)
        self.assertIn("patchcore/realpart_img1/overlay.png", line)

    def test_blocked_uses_actual_reason_without_assuming_missing_reference(self):
        text = self.render([self.row(status="blocked", reason="GPU free memory below declared budget")])
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("realpart_img1=blocked", line)
        self.assertIn("GPU free memory below declared budget", line)
        self.assertNotIn("缺同设计", line)
        self.assertNotIn("完成的记录", line)
        self.assertIn("patchcore/realpart_img1/metrics.json", line)

    def test_failed_does_not_present_partial_overlay_as_success_evidence(self):
        text = self.render([self.row(status="failed", reason="decode | error\nretained",
                                    artifact="overlay.png")])
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("realpart_img1=failed", line)
        self.assertIn("decode \\| error retained", line)
        self.assertNotIn("patchcore/realpart_img1/overlay.png", line)
        self.assertIn("patchcore/realpart_img1/metrics.json", line)

    def test_unconfigured_probe_is_explicit_without_invented_state(self):
        text = self.render([])
        for name in ("Micro-SAM LM 点选", "DINOv2 特征", "PatchCore（DINO变体）"):
            line = self.method_line(text, name)
            self.assertIn("本批未配置此探针的真实图", line)
            self.assertNotIn("blocked", line)
            self.assertNotIn("=ok", line)

    def test_synthetic_images_have_direct_explicitly_synthetic_links(self):
        rows = [self.row(image="fixture_normal", artifact="overlay.png", metrics={"synthetic_only": True}),
                self.row(image="fixture_spots", artifact="overlay.png", metrics={"synthetic_only": True})]
        text = self.render(rows)
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("本批未配置此探针的真实图", line)
        self.assertIn("合成图证：fixture_normal（synthetic）", line)
        self.assertIn("合成图证：fixture_spots（synthetic）", line)
        self.assertIn("patchcore/fixture_normal/overlay.png", line)
        self.assertIn("patchcore/fixture_spots/overlay.png", line)
        self.assertIn("只证明合成流程", line)

    def test_absent_artifacts_and_historical_files_never_produce_broken_links(self):
        text = self.render([self.row(record=False)])
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("文件缺失", line)
        self.assertNotIn("[图1]", text)
        self.assertNotIn("[metrics]", text)
        for target in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue((self.output / target).resolve().is_file(), target)

    def test_micro_sam_and_dino_states_are_dynamic_and_findings_not_hardcoded(self):
        rows = [self.row(probe="micro_sam", status="failed", reason="checkpoint incompatible",
                         artifact="sam2_vs_micro_sam.png"),
                self.row(probe="dino", status="blocked", reason="drafts unavailable",
                         artifact="original_pca_clusters.png")]
        text = self.render(rows)
        micro = self.method_line(text, "Micro-SAM LM 点选")
        dino = self.method_line(text, "DINOv2 特征")
        self.assertIn("=failed", micro)
        self.assertIn("checkpoint incompatible", micro)
        self.assertNotIn("sam2_vs_micro_sam.png", micro)
        self.assertIn("=blocked", dino)
        self.assertIn("drafts unavailable", dino)
        self.assertNotIn("original_pca_clusters.png", dino)
        self.assertNotIn("无substrate草稿", text)

    def test_custom_image_ids_keep_identity_and_sort_values_with_evidence(self):
        rows = [self.row(probe="dino", image="query_z", duration=2000, artifact="original_pca_clusters.png"),
                self.row(probe="dino", image="query_a", duration=9000, artifact="original_pca_clusters.png")]
        text = self.render(rows)
        line = self.method_line(text, "DINOv2 特征")
        self.assertIn("9.000 / 2.000", line)
        self.assertLess(line.index("query_a=ok"), line.index("query_z=ok"))
        self.assertIn("图证：query_a", line)
        self.assertIn("图证：query_z", line)

    def test_cpu_success_does_not_turn_missing_cuda_measurement_into_no_inference(self):
        row = self.row(artifact="overlay.png")
        row["peak_allocated_mib"] = row["peak_reserved_mib"] = None
        text = self.render([row])
        line = self.method_line(text, "PatchCore（DINO变体）")
        self.assertIn("realpart_img1=ok", line)
        self.assertIn("未测（CPU或不适用）", line)
        self.assertNotIn("未推理", line)


if __name__ == "__main__":
    unittest.main()
