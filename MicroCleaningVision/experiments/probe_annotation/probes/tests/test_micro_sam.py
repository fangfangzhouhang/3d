import unittest
import numpy as np

from probes.micro_sam import mask_agreement


class AgreementTests(unittest.TestCase):
    def test_symmetric_model_agreement_and_areas(self):
        a = np.array([[1, 1], [0, 0]], dtype=bool)
        b = np.array([[1, 0], [1, 0]], dtype=bool)
        r = mask_agreement(a, b)
        self.assertEqual((r['micro_sam_area_px'], r['sam2_area_px']), (2, 2))
        self.assertEqual(r['model_output_agreement_iou_not_accuracy'], 1 / 3)
        self.assertEqual(mask_agreement(b, a), r)

    def test_empty_pair_is_unknown_not_perfect(self):
        z = np.zeros((3, 3), dtype=bool)
        self.assertIsNone(mask_agreement(z, z)['model_output_agreement_iou_not_accuracy'])

    def test_size_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            mask_agreement(np.zeros((3, 3)), np.zeros((2, 2)))


if __name__ == '__main__':
    unittest.main()
