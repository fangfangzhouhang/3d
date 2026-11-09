import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from annotate.export import decode_shape, digest, export, mask_shape, validate_config
from annotate.session import finish, record, start


class AnnotationTests(unittest.TestCase):
    def config(self):
        return {"schema":"sam2-points-v1", "image_sha256":"hash",
                "annotations":[{"id":1,"label":"structure","positive":[[0,0]],"negative":[[6,5]]}]}

    def test_config_rejects_wrong_coordinates_classes_and_ids(self):
        validate_config(self.config(),7,6,"hash")
        for field,value in (("id",True),("id",0),("label","contamination"),
                            ("positive",[[7,0]]),("positive",[[float('nan'),1]]),
                            ("positive",[]),("negative",[[0,0]]),("candidate_index",1)):
            c = self.config()
            c["annotations"][0][field] = value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                validate_config(c,7,6,"hash")
        c = self.config()
        c["annotations"] *= 2
        with self.assertRaises(ValueError):
            validate_config(c,7,6,"hash")
        with self.assertRaises(ValueError):
            validate_config(self.config(),7,6,"different image")

    def test_lossless_holes_disconnected_and_border_pixels(self):
        mask = np.zeros((15,19),bool)
        mask[2:10,3:11] = True
        mask[4:7,6:9] = False
        mask[0,18] = True
        mask[14,0] = True
        a = {"id":12,"label":"ignore"}
        shape = mask_shape(mask,a)
        np.testing.assert_array_equal(decode_shape(shape,15,19),mask)
        self.assertEqual(shape["group_id"],12)
        self.assertEqual(shape["shape_type"],"mask")
        with self.assertRaises(ValueError):
            mask_shape(np.zeros_like(mask),a)

    def test_export_overlaps_masks_and_source_image_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root/"original.png"
            rgb = np.zeros((10,11,3),np.uint8)
            Image.fromarray(rgb).save(image)
            out = root/"out"
            out.mkdir()
            masks = [np.ones((10,11),bool),np.eye(10,11,dtype=bool)]
            annotations = [{"id":3,"label":"ignore","positive":[[3,3]]},
                           {"id":9,"label":"structure","positive":[[1,1]]}]
            export(image,rgb,annotations,masks,out)
            data = json.loads((out/"original.json").read_text(encoding="utf-8"))
            self.assertEqual([s["label"] for s in data["shapes"]],["structure","ignore"])
            restored = {s["group_id"]:decode_shape(s,10,11) for s in data["shapes"]}
            for a,mask in zip(annotations,masks):
                np.testing.assert_array_equal(restored[a["id"]],mask)
            # Independent sequential Labelme-style rendering must leave all ignore pixels.
            rendered = np.zeros((10,11),np.uint8)
            for s in data["shapes"]:
                rendered[restored[s["group_id"]]] = 255 if s["label"]=="ignore" else 1
            np.testing.assert_array_equal(rendered==255,masks[0])
            self.assertFalse(data["flags"]["test_ground_truth_allowed"])
            with Image.open(out/"overlay_ids.png") as overlay:
                self.assertEqual(overlay.size,(11,10))

    def test_timer_has_recorded_revision_and_finishes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            image, config, session = r/"img.png",r/"points.json",r/"session.json"
            Image.new("RGB",(7,6)).save(image)
            config.write_text(json.dumps(self.config()),encoding="utf-8")
            start(session,image,"synthetic_unit_test")
            with self.assertRaises(ValueError):
                finish(session,config,image,"out")
            record(session,config)
            value = finish(session,config,image,"out")
            self.assertGreaterEqual(value["elapsed_seconds"],0)
            with self.assertRaises(ValueError):
                finish(session,config,image,"out")
            with self.assertRaises(FileExistsError):
                start(session,image,"test")


if __name__ == "__main__":
    unittest.main()
