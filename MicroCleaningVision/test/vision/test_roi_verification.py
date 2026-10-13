"""固定 ROI 的合成像素对抗检查；不使用真实相机或串口。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from microcleaning.vision.roi_verification import (
    RoiVerificationPolicy, build_roi_reference, save_roi_evidence, validate_roi_location,
    verify_roi_pair, requires_neighbor_confirmation,
)


HAS_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_DEPS, "需要 requirements/perception-opencv.txt")
class RoiVerificationTests(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        self.np = np
        self.cv2 = cv2
        rng = np.random.default_rng(37)
        self.background = rng.integers(65, 195, size=(200, 240), dtype=np.uint8)
        self.before = np.zeros(self.background.shape, np.uint8)
        self.before[80:100, 90:110] = 255
        self.after = np.zeros_like(self.before)
        self.after[80:100, 90:100] = 255
        self.pre = self.render(self.before)
        self.post = self.render(self.after)
        self.reference = build_roi_reference(self.pre, self.before, target_id="S0001", frame_id="initial")

    def render(self, mask):
        image = self.background.copy()
        image[mask > 0] = 35
        return image

    def verify(self, **kwargs):
        return verify_roi_pair(
            reference=kwargs.pop("reference", self.reference),
            pre_image=kwargs.pop("pre_image", self.pre),
            post_image=kwargs.pop("post_image", self.post),
            pre_mask=kwargs.pop("pre_mask", self.before),
            post_mask=kwargs.pop("post_mask", self.after),
            pre_frame_id=kwargs.pop("pre_frame_id", "pre"),
            post_frame_id=kwargs.pop("post_frame_id", "post"),
            **kwargs,
        )

    def test_same_coordinates_half_residual_has_exact_metrics(self):
        result = self.verify()
        self.assertTrue(result.valid, result.to_dict())
        self.assertEqual((400, 200), (result.pre_area_px, result.post_area_px))
        self.assertEqual(0.5, result.removal_rate)
        self.assertEqual(0.5, result.cumulative_removal_rate)
        self.assertEqual(0.5, result.iou)
        self.assertAlmostEqual(2 / 3, result.dice)
        self.assertEqual((82, 72, 36, 36), result.roi)
        self.assertLess(self.np.linalg.norm(result.alignment_shift_px), 0.01)

    def test_location_validation_accepts_initial_snapshot_but_not_shift(self):
        initial = validate_roi_location(self.reference, self.pre, self.before)
        self.assertTrue(initial.valid, initial)
        transform = self.np.float32([[1, 0, 2], [0, 1, 0]])
        shifted = self.cv2.warpAffine(self.pre, transform, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        changed = validate_roi_location(self.reference, shifted, self.before)
        self.assertFalse(changed.valid)
        self.assertIn("ROI_POSITION_SHIFTED", changed.reason_codes)

    def test_no_post_foreground_is_zero_display_and_invalid(self):
        blank = self.np.zeros_like(self.before)
        result = self.verify(post_mask=blank, post_image=self.render(blank))
        self.assertFalse(result.valid)
        self.assertEqual(0.0, result.removal_rate)
        self.assertEqual(0, result.post_area_px)
        self.assertIsNone(result.cumulative_removal_rate)
        self.assertIsNone(result.iou)
        self.assertIn("ROI_POST_EMPTY", result.reason_codes)

    def test_no_pre_or_initial_foreground_never_claims_cleaned(self):
        blank = self.np.zeros_like(self.before)
        ref = build_roi_reference(self.background, blank)
        result = self.verify(reference=ref, pre_mask=blank, pre_image=self.background)
        self.assertFalse(result.valid)
        self.assertEqual(0.0, result.removal_rate)
        self.assertIn("ROI_REFERENCE_EMPTY", result.reason_codes)
        self.assertIn("ROI_PRE_EMPTY", result.reason_codes)

    def test_area_growth_is_negative_not_clamped(self):
        larger = self.before.copy()
        larger[80:100, 110:115] = 255
        result = self.verify(post_mask=larger, post_image=self.render(larger))
        self.assertTrue(result.valid, result.to_dict())
        self.assertEqual(-0.25, result.removal_rate)
        self.assertEqual(0.8, result.iou)

    def test_foreground_growth_past_fixed_roi_is_not_cropped_into_fake_success(self):
        larger = self.before.copy()
        larger[80:100, 90:130] = 255
        result = self.verify(post_mask=larger, post_image=self.render(larger))
        self.assertFalse(result.valid)
        self.assertIn("ROI_FOREGROUND_AT_BOUNDARY", result.reason_codes)
        self.assertIsNone(result.removal_rate)

    def test_target_cut_by_camera_border_requires_review(self):
        target = self.np.zeros_like(self.before)
        target[0:10, 0:10] = 255
        after = target.copy()
        after[0:10, 5:10] = 0
        pre, post = self.render(target), self.render(after)
        reference = build_roi_reference(pre, target)
        result = self.verify(reference=reference, pre_image=pre, post_image=post, pre_mask=target, post_mask=after)
        self.assertFalse(result.valid)
        self.assertIn("ROI_FOREGROUND_AT_BOUNDARY", result.reason_codes)

    def test_pixel_translation_rejected_without_warping_post(self):
        transform = self.np.float32([[1, 0, 2], [0, 1, -1]])
        shifted = self.cv2.warpAffine(self.post, transform, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        mask = self.cv2.warpAffine(self.after, transform, (240, 200))
        result = self.verify(post_image=shifted, post_mask=mask)
        self.assertFalse(result.valid)
        self.assertIsNone(result.removal_rate)
        self.assertIn("ROI_POSITION_SHIFTED", result.reason_codes)
        self.assertAlmostEqual(2, result.alignment.shift_px[0], delta=0.05)
        self.assertAlmostEqual(-1, result.alignment.shift_px[1], delta=0.05)

    def test_subpixel_translation_rejected(self):
        transform = self.np.float32([[1, 0, 0.5], [0, 1, 0]])
        shifted = self.cv2.warpAffine(self.post, transform, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        result = self.verify(post_image=shifted)
        self.assertFalse(result.valid)
        self.assertIn("ROI_POSITION_SHIFTED", result.reason_codes)

    def test_both_current_frames_shifted_from_first_reference_rejected(self):
        transform = self.np.float32([[1, 0, 2], [0, 1, 0]])
        pre = self.cv2.warpAffine(self.pre, transform, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        post = self.cv2.warpAffine(self.post, transform, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        result = self.verify(pre_image=pre, post_image=post)
        self.assertFalse(result.valid)
        self.assertIn("ROI_REFERENCE_POSITION_UNCERTAIN", result.reason_codes)

    def test_rotation_does_not_cancel_to_zero_median(self):
        rotation = self.cv2.getRotationMatrix2D((120, 100), 0.5, 1)
        post = self.cv2.warpAffine(self.post, rotation, (240, 200), borderMode=self.cv2.BORDER_REFLECT)
        result = self.verify(post_image=post)
        self.assertFalse(result.valid)
        self.assertIn("ROI_POSITION_SHIFTED", result.reason_codes)

    def test_large_illumination_change_cannot_fake_cleaning(self):
        changed = self.np.clip(self.post.astype(int) + 30, 0, 255).astype(self.np.uint8)
        result = self.verify(post_image=changed)
        self.assertFalse(result.valid)
        self.assertIsNone(result.removal_rate)
        self.assertIn("ROI_ILLUMINATION_CHANGED", result.reason_codes)

    def test_local_illumination_change_is_not_hidden_by_stable_whole_background(self):
        changed = self.post.copy()
        changed[72:108, 82:118] = self.np.clip(changed[72:108, 82:118].astype(int) + 30, 0, 255).astype(self.np.uint8)
        result = self.verify(post_image=changed)
        self.assertFalse(result.valid)
        self.assertIn("ROI_LOCAL_ILLUMINATION_CHANGED", result.reason_codes)
        self.assertIsNone(result.removal_rate)

    def test_blur_cannot_fake_area_decrease(self):
        result = self.verify(post_image=self.cv2.GaussianBlur(self.post, (9, 9), 3))
        self.assertFalse(result.valid)
        self.assertIsNone(result.removal_rate)
        self.assertTrue(set(result.reason_codes) & {"ROI_BACKGROUND_UNOBSERVABLE", "ROI_FOCUS_CHANGED", "ROI_PIXEL_QUALITY_LOW"})

    def test_black_white_flat_fields_unobservable(self):
        for intensity in (0, 128, 255):
            with self.subTest(intensity=intensity):
                image = self.np.full(self.pre.shape, intensity, self.np.uint8)
                ref = build_roi_reference(image, self.before)
                result = self.verify(reference=ref, pre_image=image, post_image=image)
                self.assertFalse(result.valid)
                self.assertIn("ROI_BACKGROUND_UNOBSERVABLE", result.reason_codes)
                self.assertIsNone(result.removal_rate)

    def test_quality_flags_are_not_overridden_by_good_pixels(self):
        result = self.verify(post_quality_flags=("FOCUS_LOW",))
        self.assertFalse(result.valid)
        self.assertIn("ROI_INPUT_QUALITY_FLAGGED", result.reason_codes)

    def test_same_frame_id_or_pixels_rejected(self):
        self.assertIn("ROI_DUPLICATE_FRAME_ID", self.verify(post_frame_id="pre").reason_codes)
        repeated = self.verify(post_image=self.pre, post_mask=self.before)
        self.assertFalse(repeated.valid)
        self.assertIn("ROI_DUPLICATE_PIXELS", repeated.reason_codes)
        self.assertIsNone(repeated.removal_rate)

    def test_other_target_changes_do_not_change_this_area(self):
        neighbor = self.before.copy()
        neighbor[80:100, 115:125] = 255
        pre_image = self.render(neighbor)
        reference = build_roi_reference(pre_image, self.before, all_target_mask=neighbor)
        after = self.after.copy()  # 本目标残留一半，另一个目标消失。
        result = self.verify(reference=reference, pre_image=pre_image, pre_mask=neighbor, post_mask=after, post_image=self.render(after), secondary_post_mask=after)
        self.assertTrue(result.valid, result.to_dict())
        self.assertEqual((400, 200), (result.pre_area_px, result.post_area_px))
        self.assertEqual(0.5, result.removal_rate)

    def test_changed_nearby_neighbor_requires_secondary_check(self):
        neighbor = self.before.copy()
        neighbor[80:100, 115:125] = 255
        pre = self.render(neighbor)
        reference = build_roi_reference(pre, self.before, all_target_mask=neighbor)
        self.assertTrue(requires_neighbor_confirmation(reference, pre, self.post))
        result = self.verify(reference=reference, pre_image=pre, pre_mask=neighbor)
        self.assertFalse(result.valid)
        self.assertIn("ROI_NEIGHBOR_SECONDARY_CONFIRMATION_REQUIRED", result.reason_codes)
        self.assertIsNone(result.removal_rate)

    def test_neighbor_growing_into_domain_requires_review(self):
        neighbor = self.before.copy()
        neighbor[80:100, 115:125] = 255
        pre = self.render(neighbor)
        reference = build_roi_reference(pre, self.before, all_target_mask=neighbor)
        after = neighbor.copy()
        after[80:100, 108:125] = 255
        result = self.verify(reference=reference, pre_image=pre, pre_mask=neighbor, post_image=self.render(after), post_mask=after)
        self.assertFalse(result.valid)
        self.assertIn("ROI_NEIGHBOR_INTRUSION", result.reason_codes)

    def test_split_residual_measured_in_fixed_region_without_new_ids(self):
        split = self.np.zeros_like(self.before)
        split[80:100, 90:94] = 255
        split[80:100, 105:110] = 255
        result = self.verify(post_mask=split, post_image=self.render(split))
        self.assertTrue(result.valid, result.to_dict())
        self.assertEqual(180, result.post_area_px)
        self.assertEqual(0.55, result.removal_rate)

    def test_pre_post_masks_wrong_sizes_are_invalid_not_silently_resized(self):
        result = self.verify(post_mask=self.after[:100])
        self.assertFalse(result.valid)
        self.assertIn("ROI_MASK_SIZE_MISMATCH", result.reason_codes)
        self.assertIsNone(result.iou)
        self.assertIsNone(result.removal_rate)

    def test_image_wrong_sizes_invalid_and_still_has_diagnostic_visuals(self):
        result = self.verify(post_image=self.post[:100])
        self.assertFalse(result.valid)
        self.assertIn("ROI_IMAGES_SIZE_MISMATCH", result.reason_codes)
        self.assertEqual(result.pre_crop.shape, result.post_crop.shape)

    def test_reference_is_snapshot_and_readonly(self):
        old_hash = self.reference.image_sha256
        self.pre[0, 0] = 0
        self.before[:] = 0
        self.assertEqual(400, self.reference.initial_area_px)
        self.assertEqual(old_hash, self.reference.image_sha256)
        self.assertFalse(self.reference.image.flags.writeable)
        self.assertFalse(self.reference.target_mask.flags.writeable)

    def test_second_round_keeps_initial_roi_and_distinguishes_cumulative_rate(self):
        final = self.np.zeros_like(self.before)
        final[80:100, 90:95] = 255
        result = self.verify(pre_image=self.post, pre_mask=self.after, post_image=self.render(final), post_mask=final)
        self.assertTrue(result.valid, result.to_dict())
        self.assertEqual((200, 100), (result.pre_area_px, result.post_area_px))
        self.assertEqual(0.5, result.removal_rate)
        self.assertEqual(0.75, result.cumulative_removal_rate)
        self.assertEqual(self.reference.roi, result.roi)

    def test_json_and_visual_evidence_saved_to_unicode_path(self):
        result = self.verify()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "复检第一轮"
            paths = save_roi_evidence(result, directory)
            data = json.loads((directory / paths["metadata"]).read_text(encoding="utf-8"))
            self.assertTrue(data["valid"])
            self.assertEqual(0.5, data["removal_rate"])
            self.assertIn("不能单独代表清洗率", data["overlap_semantics"])
            self.assertEqual({"pre_crop", "post_crop", "overlay", "contact_sheet", "metadata"}, set(paths))
            import hashlib
            for name in ("pre_crop", "post_crop", "overlay", "contact_sheet"):
                payload = (directory / paths[name]).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), data["artifact_sha256"][name])
                array = self.np.frombuffer(payload, self.np.uint8)
                decoded = self.cv2.imdecode(array, self.cv2.IMREAD_COLOR)
                self.assertIsNotNone(decoded)
            with self.assertRaises(FileExistsError):
                save_roi_evidence(result, directory)
            self.assertEqual((144, 448, 3), result.contact_sheet.shape)

    def test_policy_and_bad_array_inputs_rejected(self):
        for value in (float("nan"), float("inf"), 0, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RoiVerificationPolicy(alignment_tolerance_px=value)
        with self.assertRaises(ValueError):
            self.verify(post_image=self.post.astype(self.np.float32))
        with self.assertRaises(ValueError):
            build_roi_reference(self.pre, self.before, all_target_mask=self.np.zeros_like(self.before))


if __name__ == "__main__":
    unittest.main()
