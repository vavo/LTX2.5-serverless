import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

WORKFLOW_PATHS = {
    "i2v": ROOT_DIR / "video_ltx2_5_i2v_API.json",
    "t2v": ROOT_DIR / "video_ltx2_5_t2v_API.json",
}

# Nodes that exist only in the i2v workflow's image-conditioning branch.
I2V_ONLY_NODES = {"395", "398:363", "398:357", "398:349", "398:350", "398:351"}


class Ltx25WorkflowTestMixin:
    variant = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = json.loads(WORKFLOW_PATHS[cls.variant].read_text(encoding="utf-8"))

    def test_every_workflow_link_targets_an_existing_node(self) -> None:
        missing_references = []
        for node_id, node in self.workflow.items():
            for input_name, value in node.get("inputs", {}).items():
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and isinstance(value[0], str)
                    and value[0] not in self.workflow
                ):
                    missing_references.append((node_id, input_name, value[0]))

        self.assertEqual(missing_references, [])

    def test_workflow_uses_the_ltx25_int8_stack(self) -> None:
        self.assertEqual(
            self.workflow["398:384"]["inputs"]["unet_name"],
            "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
        )
        self.assertEqual(
            self.workflow["398:387"]["inputs"]["clip_name"],
            "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
        )
        self.assertEqual(
            self.workflow["398:385"]["inputs"]["vae_name"],
            "ltx-2.5-video-vae-bf16.safetensors",
        )
        self.assertEqual(
            self.workflow["398:386"]["inputs"]["vae_name"],
            "ltx-2.5-audio-vae-bf16.safetensors",
        )

    def test_save_video_node_exists(self) -> None:
        self.assertIn("75", self.workflow)
        self.assertEqual(self.workflow["75"]["class_type"], "SaveVideo")


class TestLtx25I2vWorkflow(Ltx25WorkflowTestMixin, unittest.TestCase):
    variant = "i2v"

    def test_filename_prefix(self) -> None:
        self.assertEqual(
            self.workflow["75"]["inputs"]["filename_prefix"], "video/LTX-2.5_i2v"
        )


class TestLtx25T2vWorkflow(Ltx25WorkflowTestMixin, unittest.TestCase):
    variant = "t2v"

    def test_filename_prefix(self) -> None:
        self.assertEqual(
            self.workflow["75"]["inputs"]["filename_prefix"], "video/LTX-2.5_t2v"
        )

    def test_image_conditioning_nodes_are_absent(self) -> None:
        for node_id in I2V_ONLY_NODES:
            self.assertNotIn(node_id, self.workflow)

    def test_latent_video_branch_bypasses_image_conditioning(self) -> None:
        self.assertEqual(
            self.workflow["398:377"]["inputs"]["video_latent"], ["398:356", 0]
        )
        self.assertEqual(
            self.workflow["398:340"]["inputs"]["video_latent"], ["398:348", 0]
        )

    def test_prompt_generation_has_no_image_input(self) -> None:
        self.assertNotIn("image", self.workflow["398:380"]["inputs"])


class TestLtx25WorkflowParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.i2v_workflow = json.loads(
            WORKFLOW_PATHS["i2v"].read_text(encoding="utf-8")
        )
        cls.t2v_workflow = json.loads(
            WORKFLOW_PATHS["t2v"].read_text(encoding="utf-8")
        )

    def test_t2v_node_set_is_i2v_minus_image_branch(self) -> None:
        expected_t2v_nodes = set(self.i2v_workflow) - I2V_ONLY_NODES
        self.assertEqual(set(self.t2v_workflow), expected_t2v_nodes)

    def test_shared_nodes_have_identical_class_types(self) -> None:
        shared_node_ids = set(self.i2v_workflow) & set(self.t2v_workflow)
        mismatched = [
            node_id
            for node_id in shared_node_ids
            if self.i2v_workflow[node_id]["class_type"]
            != self.t2v_workflow[node_id]["class_type"]
        ]
        self.assertEqual(mismatched, [])


if __name__ == "__main__":
    unittest.main()
