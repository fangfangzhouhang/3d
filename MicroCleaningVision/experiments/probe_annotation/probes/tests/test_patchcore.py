from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from probes.base import ImageInput, ProbeBlocked
from probes.patchcore import PatchCoreProbe, greedy_coreset, nearest_euclidean


class PatchCoreMathTests(unittest.TestCase):
    def test_exact_chunked_nearest_distances(self):
        query = np.array([[0, 0], [2, 1], [7, 4]], dtype=np.float32)
        bank = np.array([[0, 0], [3, 1]], dtype=np.float32)
        actual = nearest_euclidean(query, bank, chunk_size=1)
        expected = np.array([0, 1, np.sqrt(25)], dtype=np.float32)
        np.testing.assert_allclose(actual, expected, rtol=1e-6)

    def test_coreset_is_deterministic_and_uses_source_vectors(self):
        vectors = np.array([[0, 0], [1, 0], [2, 0], [10, 0], [4, 0]], dtype=np.float32)
        bank_a, indices_a = greedy_coreset(vectors, 3)
        bank_b, indices_b = greedy_coreset(vectors, 3)
        np.testing.assert_array_equal(indices_a, indices_b)
        np.testing.assert_array_equal(bank_a, bank_b)
        np.testing.assert_array_equal(bank_a, vectors[indices_a])
        self.assertEqual(len(set(indices_a.tolist())), 3)

    def test_real_missing_manifest_blocks_before_model_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = PatchCoreProbe({"device": "cpu", "design_id": "design-a"}, root)
            with patch("probes.dino.DinoProbe.load", side_effect=AssertionError("must not load")):
                probe.load()
                with self.assertRaises(ProbeBlocked) as caught:
                    probe.infer(ImageInput("query", root / "missing.png", {"design_id": "design-a"}), root / "out")
            self.assertEqual(caught.exception.code, "missing-clean-reference")


if __name__ == "__main__":
    unittest.main()
