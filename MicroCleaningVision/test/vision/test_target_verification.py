"""单目标连通域与复检。合成 Mask，不代表真实清洗。"""

import importlib.util
import unittest
from dataclasses import replace

from microcleaning.contracts import ExecutionReceipt, NextRoute
from microcleaning.data_learning.image_quality import ImageQuality, build_observation
from microcleaning.vision.verification import verify_area_change


HAS_PERCEPTION_DEPS = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


def _zeros(height: int, width: int):
    import numpy as np

    return np.zeros((height, width), dtype=np.uint8)


def _fill(mask, x: int, y: int, width: int, height: int) -> None:
    mask[y:y + height, x:x + width] = 255


def _three_targets():
    """T1 在左上，T2、T3 离得够远，搜索框不会互相盖住。面积 100、500、500。"""

    mask = _zeros(80, 100)
    _fill(mask, 4, 4, 10, 10)
    _fill(mask, 60, 4, 20, 25)
    _fill(mask, 4, 50, 25, 20)
    return mask


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class TargetInstanceTests(unittest.TestCase):
    def test_extract_one_mask_keeps_original_coordinates_and_excludes_neighbors(self):
        import numpy as np
        from microcleaning.vision.target_instance import extract_target_instances, extract_target_mask
        mask = _three_targets()
        target = extract_target_instances(mask)[1]
        selected = extract_target_mask(mask, target)
        self.assertEqual(mask.shape, selected.shape)
        self.assertEqual(target.area_px, np.count_nonzero(selected))
        self.assertEqual(target.centroid_px, extract_target_instances(selected)[0].centroid_px)
        self.assertFalse(selected[4, 4])

    def test_stale_instance_cannot_select_a_relabelled_target(self):
        from microcleaning.vision.target_instance import extract_target_instances, extract_target_mask
        mask = _three_targets()
        old = extract_target_instances(mask)[0]
        mask[4:14, 4:14] = 0
        with self.assertRaises(ValueError):
            extract_target_mask(mask, old)

    def test_public_match_reports_existing_label_or_disappearance(self):
        from microcleaning.vision.target_instance import extract_target_instances, match_target_instance
        mask = _three_targets()
        target = extract_target_instances(mask)[1]
        post = mask.copy()
        post[4:14, 4:14] = 0
        match = match_target_instance(pre_target=target, pre_mask=mask, post_mask=post)
        self.assertEqual("matched", match.status)
        self.assertEqual(1, match.post_label)
        post[4:29, 60:80] = 0
        vanished = match_target_instance(pre_target=target, pre_mask=mask, post_mask=post)
        self.assertIsNone(vanished.post_label)
        self.assertEqual(0.0, vanished.post_area_px)
    def test_empty_mask_has_no_targets(self):
        from microcleaning.vision.target_instance import extract_target_instances

        found = extract_target_instances(_zeros(40, 40))
        self.assertEqual([], found)

    def test_empty_array_is_rejected(self):
        import numpy as np

        from microcleaning.vision.target_instance import extract_target_instances

        with self.assertRaises(ValueError):
            extract_target_instances(np.zeros((0, 10), dtype=np.uint8))

    def test_single_target_uses_its_own_component(self):
        import numpy as np

        from microcleaning.vision.target_instance import extract_target_instances

        mask = _zeros(40, 40)
        _fill(mask, 12, 8, 10, 10)
        found = extract_target_instances(mask, mask_ref="replay://one.png")

        self.assertEqual(1, len(found))
        target = found[0]
        self.assertEqual("T1", target.target_id)
        self.assertEqual(100.0, target.area_px)
        self.assertEqual(int(np.count_nonzero(mask)), target.area_px)
        self.assertEqual((12, 8, 10, 10), target.bbox)
        self.assertEqual((16.5, 12.5), target.centroid_px)
        self.assertEqual(1, target.component_label)
        self.assertEqual(1.0, target.confidence)
        self.assertEqual("replay://one.png", target.mask_ref)

    def test_three_targets_do_not_take_the_full_mask_area(self):
        import numpy as np

        from microcleaning.vision.target_instance import extract_target_instances

        mask = _three_targets()
        found = extract_target_instances(mask)
        again = extract_target_instances(mask)

        self.assertEqual(found, again)
        self.assertEqual(["T1", "T2", "T3"], [item.target_id for item in found])
        self.assertEqual([100.0, 500.0, 500.0], [item.area_px for item in found])
        self.assertEqual([(4, 4, 10, 10), (60, 4, 20, 25), (4, 50, 25, 20)], [item.bbox for item in found])
        full_area = int(np.count_nonzero(mask))
        self.assertEqual(1100, full_area)
        self.assertTrue(all(item.area_px != full_area for item in found))
        self.assertEqual(full_area, sum(item.area_px for item in found))

    def test_small_target_is_kept(self):
        from microcleaning.vision.target_instance import extract_target_instances

        mask = _zeros(12, 12)
        mask[3, 4] = 255
        found = extract_target_instances(mask)

        self.assertEqual(1, len(found))
        self.assertEqual(1.0, found[0].area_px)
        self.assertEqual((4.0, 3.0), found[0].centroid_px)
        self.assertEqual((4, 3, 1, 1), found[0].bbox)
        self.assertEqual("T1", found[0].target_id)

    def test_border_contact_is_kept(self):
        from microcleaning.vision.target_instance import extract_target_instances

        mask = _zeros(20, 30)
        _fill(mask, 0, 0, 4, 6)
        _fill(mask, 26, 15, 4, 5)
        found = extract_target_instances(mask)

        self.assertEqual(2, len(found))
        self.assertEqual((0, 0, 4, 6), found[0].bbox)
        self.assertEqual(24.0, found[0].area_px)
        self.assertEqual((1.5, 2.5), found[0].centroid_px)
        self.assertEqual((26, 15, 4, 5), found[1].bbox)
        self.assertEqual(20.0, found[1].area_px)
        self.assertEqual(mask.shape[1], found[1].bbox[0] + found[1].bbox[2])
        self.assertEqual(mask.shape[0], found[1].bbox[1] + found[1].bbox[3])

    def test_eight_connectivity_is_stable(self):
        from microcleaning.vision.target_instance import extract_target_instances

        diagonal = _zeros(8, 8)
        diagonal[2, 2] = 255
        diagonal[3, 3] = 255
        self.assertEqual(1, len(extract_target_instances(diagonal)))
        self.assertEqual(2.0, extract_target_instances(diagonal)[0].area_px)

        gap = _zeros(8, 8)
        gap[2, 2] = 255
        gap[2, 4] = 255
        separated = extract_target_instances(gap)
        self.assertEqual(["T1", "T2"], [item.target_id for item in separated])
        self.assertEqual([1.0, 1.0], [item.area_px for item in separated])


@unittest.skipUnless(HAS_PERCEPTION_DEPS, "需要 requirements/perception-opencv.txt")
class SingleTargetVerificationTests(unittest.TestCase):
    def setUp(self):
        quality = ImageQuality(0.95, 0.95, 0.95)
        self.pre = build_observation(
            task_id="measure",
            frame_id="pre",
            raw_image_ref="replay://pre.png",
            quality=quality,
        )
        self.post = build_observation(
            task_id="measure",
            frame_id="post",
            raw_image_ref="replay://post.png",
            quality=quality,
        )
        self.receipt = ExecutionReceipt(
            "a1", "fake_serial", "t0", "t1", (10.0, 20.0), 200, 0.3, "ACK_RECEIVED", "SIMULATED", True,
        )

    def test_empty_mask_does_not_invent_a_cleaned_target(self):
        from microcleaning.vision.target_instance import TargetInstance, verify_single_target

        blank = _zeros(40, 40)
        ghost = TargetInstance("T1", (5.0, 5.0), 100.0, (0, 0, 10, 10), 1, 1.0)
        outcome = verify_single_target(
            task_id="measure",
            pre=self.pre,
            post=self.post,
            pre_target=ghost,
            pre_mask=blank,
            post_mask=blank,
            receipt=self.receipt,
        )

        self.assertEqual("unmatched", outcome.match_status)
        self.assertIsNone(outcome.pre_area_px)
        self.assertIsNone(outcome.post_area_px)
        self.assertIsNone(outcome.removal_rate)
        self.assertEqual(NextRoute.HUMAN, outcome.result.next_route)
        self.assertEqual(("TARGET_NOT_IN_MASK",), outcome.result.reason_codes)

    def test_unchanged_single_target_is_not_cleaned(self):
        from microcleaning.vision.target_instance import extract_target_instances

        mask = _zeros(40, 40)
        _fill(mask, 12, 8, 10, 10)
        target = extract_target_instances(mask)[0]
        outcome = self._verify(mask, mask, target)

        self.assertEqual("matched", outcome.match_status)
        self.assertEqual("T1", outcome.target_id)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(100.0, outcome.post_area_px)
        self.assertAlmostEqual(0.0, outcome.removal_rate)
        self.assertEqual(1.0, outcome.match_quality)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)
        self.assertEqual(("RESIDUAL_REMAINS",), outcome.result.reason_codes)

    def test_complete_disappearance_is_not_the_global_area(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _three_targets()
        post_mask = pre_mask.copy()
        post_mask[4:14, 4:14] = 0
        target = extract_target_instances(pre_mask)[0]
        outcome = self._verify(pre_mask, post_mask, target)
        global_result = self._global(pre_mask, post_mask)

        self.assertEqual("T1", outcome.target_id)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(0.0, outcome.post_area_px)
        self.assertAlmostEqual(1.0, outcome.removal_rate)
        self.assertEqual(NextRoute.STOP, outcome.result.next_route)
        self.assertEqual(("REPLAY_THRESHOLD_MET",), outcome.result.reason_codes)
        self.assertEqual(NextRoute.RETRY, global_result.next_route)
        self.assertNotEqual(outcome.post_area_px, global_result.residual_area_px)
        self.assertNotEqual(outcome.result.next_route, global_result.next_route)

    def test_half_residual_stays_below_replay_threshold(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _zeros(80, 100)
        _fill(pre_mask, 8, 8, 10, 10)
        _fill(pre_mask, 60, 40, 20, 20)
        post_mask = _zeros(80, 100)
        _fill(post_mask, 8, 8, 5, 10)
        target = extract_target_instances(pre_mask)[0]
        outcome = self._verify(pre_mask, post_mask, target)
        global_result = self._global(pre_mask, post_mask)

        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(50.0, outcome.post_area_px)
        self.assertAlmostEqual(0.5, outcome.removal_rate)
        self.assertAlmostEqual(0.421875, outcome.match_quality)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)
        self.assertEqual(("RESIDUAL_REMAINS",), outcome.result.reason_codes)
        self.assertEqual(NextRoute.STOP, global_result.next_route)
        self.assertNotEqual(outcome.result.next_route, global_result.next_route)

    def test_slight_shift_matches_the_same_blob(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _zeros(40, 50)
        _fill(pre_mask, 12, 8, 10, 10)
        post_mask = _zeros(40, 50)
        _fill(post_mask, 14, 8, 10, 10)
        target = extract_target_instances(pre_mask)[0]
        outcome = self._verify(pre_mask, post_mask, target)

        self.assertEqual("matched", outcome.match_status)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(100.0, outcome.post_area_px)
        self.assertNotEqual(80.0, outcome.post_area_px)
        self.assertAlmostEqual(0.0, outcome.removal_rate)
        self.assertAlmostEqual(0.7, outcome.match_quality)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)

    def test_split_is_not_forced_into_one_pair(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _zeros(80, 80)
        _fill(pre_mask, 20, 20, 12, 10)
        post_mask = _zeros(80, 80)
        _fill(post_mask, 20, 20, 5, 6)
        _fill(post_mask, 26, 20, 5, 6)
        target = extract_target_instances(pre_mask)[0]
        outcome = self._verify(pre_mask, post_mask, target)
        summed = self._areas(pre_mask, post_mask)

        self.assertEqual("ambiguous", outcome.match_status)
        self.assertEqual(120.0, outcome.pre_area_px)
        self.assertIsNone(outcome.post_area_px)
        self.assertIsNone(outcome.removal_rate)
        self.assertEqual(0.0, outcome.match_quality)
        self.assertEqual(NextRoute.HUMAN, outcome.result.next_route)
        self.assertEqual(("TARGET_MATCH_AMBIGUOUS",), outcome.result.reason_codes)
        self.assertNotEqual(outcome.result.next_route, summed.next_route)

    def test_nearby_target_does_not_change_this_area(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _zeros(70, 80)
        _fill(pre_mask, 10, 40, 10, 10)
        _fill(pre_mask, 24, 40, 10, 10)
        post_mask = pre_mask.copy()
        _fill(post_mask, 24, 40, 16, 10)
        targets = extract_target_instances(pre_mask)
        post_targets = extract_target_instances(post_mask)
        outcome = self._verify(pre_mask, post_mask, targets[0])

        self.assertEqual(100.0, targets[0].area_px)
        self.assertEqual(100.0, targets[1].area_px)
        self.assertEqual(160.0, post_targets[1].area_px)
        self.assertEqual("matched", outcome.match_status)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertEqual(100.0, outcome.post_area_px)
        self.assertNotEqual(sum(item.area_px for item in post_targets), outcome.post_area_px)
        self.assertAlmostEqual(0.0, outcome.removal_rate)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)

    def test_centroid_jump_to_another_blob_is_unmatched(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _zeros(80, 90)
        _fill(pre_mask, 10, 40, 10, 10)
        _fill(pre_mask, 24, 40, 10, 10)
        _fill(pre_mask, 4, 4, 40, 10)
        post_mask = _zeros(80, 90)
        _fill(post_mask, 22, 40, 10, 10)
        subject = next(item for item in extract_target_instances(pre_mask) if item.bbox == (10, 40, 10, 10))
        outcome = self._verify(pre_mask, post_mask, subject)
        global_result = self._global(pre_mask, post_mask)

        self.assertEqual("unmatched", outcome.match_status)
        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertIsNone(outcome.post_area_px)
        self.assertIsNone(outcome.removal_rate)
        self.assertEqual(NextRoute.HUMAN, outcome.result.next_route)
        self.assertEqual(("TARGET_UNMATCHED",), outcome.result.reason_codes)
        self.assertNotEqual(100.0, outcome.post_area_px)
        self.assertEqual(NextRoute.STOP, global_result.next_route)
        self.assertNotEqual(outcome.result.next_route, global_result.next_route)

    def test_other_targets_shrinking_does_not_clear_t1(self):
        from microcleaning.vision.target_instance import extract_target_instances

        pre_mask = _three_targets()
        post_mask = pre_mask.copy()
        post_mask[4:29, 60:80] = 0
        post_mask[50:70, 4:29] = 0
        import numpy as np

        target = extract_target_instances(pre_mask)[0]
        spoofed = replace(target, area_px=float(np.count_nonzero(pre_mask)))
        outcome = self._verify(pre_mask, post_mask, spoofed)
        global_result = self._global(pre_mask, post_mask)

        self.assertEqual(100.0, outcome.pre_area_px)
        self.assertNotEqual(spoofed.area_px, outcome.pre_area_px)
        self.assertEqual(100.0, outcome.post_area_px)
        self.assertAlmostEqual(0.0, outcome.removal_rate)
        self.assertEqual(NextRoute.RETRY, outcome.result.next_route)
        self.assertEqual(("RESIDUAL_REMAINS",), outcome.result.reason_codes)
        self.assertEqual(NextRoute.STOP, global_result.next_route)
        self.assertAlmostEqual(1000.0 / 1100.0, global_result.removal_rate)
        self.assertNotEqual(outcome.result.next_route, global_result.next_route)

    def test_matched_pair_keeps_existing_verification_branches(self):
        from microcleaning.vision.target_instance import extract_target_instances

        mask = _zeros(30, 30)
        _fill(mask, 5, 5, 8, 8)
        target = extract_target_instances(mask)[0]

        damaged = self._verify(mask, mask, target, damage_flag=True)
        self.assertEqual(("DAMAGE_SUSPECTED",), damaged.result.reason_codes)
        self.assertIsNone(damaged.removal_rate)
        self.assertTrue(damaged.result.damage_flag)
        self.assertEqual(NextRoute.HUMAN, damaged.result.next_route)

        incomparable = self._verify(mask, mask, target, images_comparable=False)
        self.assertEqual(("PRE_POST_NOT_COMPARABLE",), incomparable.result.reason_codes)
        self.assertIsNone(incomparable.removal_rate)

        failed = replace(self.receipt, success=False)
        missing = self._verify(mask, mask, target, receipt=failed)
        self.assertEqual(("EXECUTION_OR_POST_EVIDENCE_MISSING",), missing.result.reason_codes)
        self.assertEqual(64.0, missing.pre_area_px)
        self.assertEqual(64.0, missing.post_area_px)
        self.assertIsNone(missing.removal_rate)

    def _verify(self, pre_mask, post_mask, target, **kwargs):
        from microcleaning.vision.target_instance import verify_single_target

        return verify_single_target(
            task_id="measure",
            pre=self.pre,
            post=self.post,
            pre_target=target,
            pre_mask=pre_mask,
            post_mask=post_mask,
            receipt=kwargs.pop("receipt", self.receipt),
            **kwargs,
        )

    def _global(self, pre_mask, post_mask):
        import numpy as np

        return verify_area_change(
            task_id="measure",
            pre=self.pre,
            post=self.post,
            pre_area_px=float(np.count_nonzero(pre_mask)),
            post_area_px=float(np.count_nonzero(post_mask)),
            receipt=self.receipt,
        )

    def _areas(self, pre_mask, post_mask):
        return self._global(pre_mask, post_mask)


if __name__ == "__main__":
    unittest.main()
