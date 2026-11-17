import random
import unittest

from ltx_payload_builder import build_payload, seconds_to_frames


class TestLtxPayloadBuilder(unittest.TestCase):
    def test_seconds_to_frames_uses_ltx_formula(self) -> None:
        self.assertEqual(seconds_to_frames(5), 121)
        self.assertEqual(seconds_to_frames(1), 25)
        self.assertEqual(seconds_to_frames(20), 481)

        with self.assertRaisesRegex(ValueError, "whole number"):
            seconds_to_frames(5.5)

    def test_build_payload_updates_workflow_template(self) -> None:
        payload = build_payload(
            prompt="A camera push-in through a neon alley.",
            seconds=5,
            aspect_ratio="9:16",
            image_name="My Scene Final.png",
            image_data_url="data:image/png;base64,AAAA",
            optimize_prompt=False,
            rng=random.Random(7),
        )

        workflow = payload["input"]["workflow"]
        image = payload["input"]["images"][0]

        self.assertEqual(workflow["398:376"]["inputs"]["value"], "A camera push-in through a neon alley.")
        self.assertEqual(workflow["398:362"]["inputs"]["value"], 5)
        self.assertEqual(
            workflow["398:362"]["inputs"]["value"]
            * workflow["398:361"]["inputs"]["value"]
            + 1,
            121,
        )
        self.assertEqual(workflow["398:372"]["inputs"]["value"], 720)
        self.assertEqual(workflow["398:360"]["inputs"]["value"], 1280)
        self.assertEqual(workflow["398:361"]["inputs"]["value"], 24)
        self.assertEqual(workflow["398:380"]["inputs"]["sampling_mode"], "off")
        self.assertFalse(workflow["398:383"]["inputs"]["value"])
        self.assertEqual(workflow["395"]["inputs"]["image"], "My_Scene_Final.png")
        self.assertEqual(image["name"], "My_Scene_Final.png")
        self.assertEqual(image["image"], "data:image/png;base64,AAAA")

    def test_build_payload_t2v_mode_omits_image_fields(self) -> None:
        payload = build_payload(
            prompt="A drone shot flying over mountains.",
            seconds=5,
            aspect_ratio="9:16",
            optimize_prompt=False,
            mode="t2v",
            rng=random.Random(7),
        )

        self.assertNotIn("images", payload["input"])

        workflow = payload["input"]["workflow"]
        for removed_node in ("395", "398:363", "398:357", "398:349", "398:350", "398:351"):
            self.assertNotIn(removed_node, workflow)

        self.assertEqual(workflow["398:377"]["inputs"]["video_latent"], ["398:356", 0])
        self.assertEqual(workflow["398:340"]["inputs"]["video_latent"], ["398:348", 0])
        self.assertNotIn("image", workflow["398:380"]["inputs"])

        self.assertEqual(
            workflow["398:376"]["inputs"]["value"], "A drone shot flying over mountains."
        )
        self.assertEqual(workflow["398:362"]["inputs"]["value"], 5)
        self.assertEqual(workflow["398:372"]["inputs"]["value"], 720)
        self.assertEqual(workflow["398:360"]["inputs"]["value"], 1280)
        self.assertEqual(workflow["398:361"]["inputs"]["value"], 24)

    def test_build_payload_i2v_validates_image_before_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "data URL"):
            build_payload(
                prompt="A camera push-in through a neon alley.",
                seconds=1.5,
                aspect_ratio="9:16",
                image_name="scene.png",
                image_data_url="bad",
                optimize_prompt=False,
                rng=random.Random(7),
            )

    def test_build_payload_i2v_requires_image_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Source image name is required"):
            build_payload(
                prompt="A camera push-in through a neon alley.",
                seconds=5,
                aspect_ratio="9:16",
                image_name="",
                image_data_url="data:image/png;base64,AAAA",
                optimize_prompt=False,
                rng=random.Random(7),
            )

    def test_build_payload_rejects_unsupported_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported mode"):
            build_payload(
                prompt="A camera push-in through a neon alley.",
                seconds=5,
                aspect_ratio="9:16",
                optimize_prompt=False,
                mode="v2v",
                rng=random.Random(7),
            )


if __name__ == "__main__":
    unittest.main()
