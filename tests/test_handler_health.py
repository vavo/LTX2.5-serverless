import asyncio
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


class TestHandlerHealth(unittest.TestCase):
    def load_handler(self, redis_url=None):
        registered = {}

        runpod = types.ModuleType("runpod")
        runpod.serverless = types.SimpleNamespace(
            start=lambda config: registered.update(config)
        )

        aiohttp = types.ModuleType("aiohttp")
        aiohttp.ClientSession = type("ClientSession", (), {})

        boto3 = types.ModuleType("boto3")
        botocore = types.ModuleType("botocore")
        botocore_config = types.ModuleType("botocore.config")
        botocore_config.Config = type("Config", (), {})

        redis = types.ModuleType("redis")
        redis_asyncio = types.ModuleType("redis.asyncio")
        self.redis_factory = Mock(return_value=object())
        redis_asyncio.from_url = self.redis_factory
        redis.asyncio = redis_asyncio

        modules = {
            "aiohttp": aiohttp,
            "boto3": boto3,
            "botocore": botocore,
            "botocore.config": botocore_config,
            "redis": redis,
            "redis.asyncio": redis_asyncio,
            "runpod": runpod,
        }

        handler_path = Path(__file__).resolve().parents[1] / "handler.py"
        spec = importlib.util.spec_from_file_location("handler_health_test", handler_path)
        module = importlib.util.module_from_spec(spec)

        with patch.dict(sys.modules, modules), patch.dict(os.environ):
            os.environ.pop("REDIS_URL", None)
            if redis_url is not None:
                os.environ["REDIS_URL"] = redis_url
            spec.loader.exec_module(module)
        return module, registered

    def test_health_check_does_not_use_runtime_services(self) -> None:
        module, registered = self.load_handler()
        response = asyncio.run(
            module.handler({"input": {"health_check": True}})
        )

        self.assertEqual(
            response,
            {"status": "healthy", "service": "ltx-2.5-worker"},
        )
        self.assertIs(registered["handler"], module.handler)

    def test_redis_is_local_for_worker_and_hub_startup(self):
        for hub_validation in ("false", "true"):
            for url in (None, "redis://127.0.0.1:6379", "redis://localhost:6379",
                        "redis://localhost:6379/", "redis://127.0.0.1:6379/0"):
                with self.subTest(url=url, hub=hub_validation), patch.dict(
                    os.environ, {"RUNPOD_HUB_VALIDATION": hub_validation}
                ):
                    self.load_handler(url)
                    self.assertEqual(self.redis_factory.call_args.args,
                                     ("redis://127.0.0.1:6379",))

    def test_external_redis_rejected_before_client_creation(self):
        for url in self.unsafe_redis_urls:
            with self.subTest(url=url), self.assertRaisesRegex(
                RuntimeError, "External Redis is disabled"
            ) as error:
                self.load_handler(url)
            self.redis_factory.assert_not_called()
            self.assertNotIn(url, str(error.exception))

    unsafe_redis_urls = (
        "redis://external.example:6379", "rediss://external.example:6379",
        "redis://localhost.evil.example:6379", "redis://127.0.0.1.evil.example:6379",
        "redis://127.0.0.1:6379@external.example:6379",
        "redis://user:secret@external.example:6379",
        "redis://127.0.0.1:6379?host=external.example", "unix:///tmp/redis.sock",
        "redis://127.0.0.1:6380", "redis://127.0.0.1:6379/1",
    )

    def test_local_redis_startup_rejects_external_urls_without_commands(self):
        source = (Path(__file__).resolve().parents[1] / "src/start.sh").read_text()
        function = source.split("start_local_redis() {", 1)[1].split("\n}\n", 1)[0]
        for url in self.unsafe_redis_urls:
            result = subprocess.run(
                ["/bin/bash", "-c", "start_local_redis() {" + function
                 + "\n}\nstart_local_redis"],
                env={"REDIS_URL": url, "PATH": "/nonexistent"},
                capture_output=True, text=True,
            )
            with self.subTest(url=url):
                self.assertEqual(result.returncode, 1)
                self.assertIn("External Redis is disabled", result.stderr)
                self.assertNotIn(url, result.stdout + result.stderr)

    def test_local_redis_startup_keeps_bind_and_storage_protections(self):
        source = (Path(__file__).resolve().parents[1] / "src/start.sh").read_text()
        function = source.split("start_local_redis() {", 1)[1].split("\n}\n", 1)[0]
        stubs = '''
attempt=0
redis-cli() {
    [[ "$*" == '-u redis://127.0.0.1:6379 ping' ]] || exit 2
    if [ "$attempt" = 0 ]; then return 1; fi
    echo PONG
}
redis-server() {
    [[ "$*" == '--daemonize yes --bind 127.0.0.1 --protected-mode yes --save  --appendonly no' ]] || exit 2
}
sleep() { attempt=1; }
'''
        result = subprocess.run(
            ["/bin/bash", "-c", stubs + "start_local_redis() {" + function
             + "\n}\nstart_local_redis"],
            env={"REDIS_URL": "redis://localhost:6379/0", "PATH": "/nonexistent"},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Redis ready at redis://127.0.0.1:6379", result.stdout)


USER_ID = "00000000-0000-4000-8000-00000000000a"
JOB_ID = "00000000-0000-4000-8000-00000000000b"
SCENE_ID = "00000000-0000-4000-8000-00000000000c"
JOB_PREFIX = f"users/{USER_ID}/jobs/{JOB_ID}"
IMAGE_ORIGIN = "https://images.example.com"
SCENE = {
    "task": "scene",
    "job_id": JOB_ID,
    "user_id": USER_ID,
    "scene_id": SCENE_ID,
    "visual_prompt": "A calm river at sunrise.",
    "audio_prompt": "Water flows.",
    "width": 1280,
    "height": 704,
    "target_width": 1280,
    "target_height": 720,
    "upscale": False,
    "num_frames": 121,
    "fps": 24,
    "seed": 42,
    "r2_bucket": "ltx-video-outputs",
    "output_key": f"{JOB_PREFIX}/scenes/partial/{SCENE_ID}.mp4",
    "webhook_url": "https://app.example/api/render-webhook",
}
CONCAT = {
    "task": "concat",
    "job_id": JOB_ID,
    "user_id": USER_ID,
    "r2_bucket": "ltx-video-outputs",
    "partial_keys": [f"{JOB_PREFIX}/scenes/partial/{SCENE_ID}.mp4"],
    "output_key": f"{JOB_PREFIX}/scenes/full/{JOB_ID}.mp4",
    "webhook_url": "https://app.example/api/render-webhook",
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, content_type="image/png", chunks=(PNG,)):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.content = FakeContent(list(chunks))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def workflow_links(workflow):
    for node_id, node in workflow.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                yield node_id, name, value[0]


class TestRenderTasks(unittest.TestCase):
    def setUp(self):
        self.module, _ = TestHandlerHealth.load_handler(TestHandlerHealth())
        self.module.ALLOWED_IMAGE_ORIGIN = IMAGE_ORIGIN

    def scene(self, **changes):
        return {**SCENE, **changes}

    def test_text_to_video_uses_template_toggle_and_exact_frames(self):
        params = self.module.validate_scene(self.scene())
        workflow = self.module.build_scene_workflow(params, [])
        inputs = lambda node: workflow[node]["inputs"]
        self.assertIs(inputs("398:363")["value"], True)
        self.assertEqual(inputs("395")["image"], self.module.PLACEHOLDER_IMAGE)
        self.assertIs(inputs("398:383")["value"], False)
        self.assertEqual(inputs("398:380")["sampling_mode"], "off")
        self.assertEqual(inputs("398:356")["length"], 121)
        self.assertEqual(inputs("398:366")["frames_number"], 121)
        self.assertEqual(inputs("398:372")["value"], 1280)
        self.assertEqual(inputs("398:360")["value"], 704)
        self.assertEqual(inputs("398:361")["value"], 24)
        self.assertEqual(inputs("398:338")["noise_seed"], 42)
        self.assertEqual(inputs("398:339")["noise_seed"], 43)
        self.assertIn("Audio: Water flows.", inputs("398:376")["value"])
        self.assertFalse(any(node.startswith("kf") for node in workflow))
        self.assertEqual(self.module.BASE_WORKFLOW["398:363"]["inputs"]["value"], False)

    def test_first_frame_image_uses_native_image_to_video(self):
        params = self.module.validate_scene(self.scene(
            images=[{"url": f"{IMAGE_ORIGIN}/u/a.png", "frame_index": 0, "strength": 0.5}]
        ))
        workflow = self.module.build_scene_workflow(params, ["scene-x/kf-0.png"])
        self.assertIs(workflow["398:363"]["inputs"]["value"], False)
        self.assertEqual(workflow["395"]["inputs"]["image"], "scene-x/kf-0.png")
        self.assertEqual(workflow["398:357"]["inputs"]["strength"], 0.5)
        self.assertFalse(any(node.startswith("kf") for node in workflow))

    def test_later_keyframes_require_flag_and_inject_connected_guides(self):
        images = [
            {"url": f"{IMAGE_ORIGIN}/u/a.png", "frame_index": 0},
            {"url": f"{IMAGE_ORIGIN}/u/b.png", "frame_index": 64},
            {"url": f"{IMAGE_ORIGIN}/u/c.png", "frame_index": 120, "strength": 0.8},
        ]
        with self.assertRaisesRegex(self.module.JobInputError, "not enabled"):
            self.module.validate_scene(self.scene(images=images))

        self.module.ENABLE_KEYFRAME_GUIDES = True
        params = self.module.validate_scene(self.scene(images=list(reversed(images))))
        self.assertEqual([image["frame_index"] for image in params["images"]], [0, 64, 120])
        workflow = self.module.build_scene_workflow(params, ["s/kf-0.png", "s/kf-1.png", "s/kf-2.png"])
        guides = sorted(node for node, data in workflow.items() if data["class_type"] == "LTXVAddGuide")
        self.assertEqual(guides, ["kf0:guide1", "kf0:guide2", "kf1:guide1", "kf1:guide2"])
        self.assertEqual(workflow["kf0:guide1"]["inputs"]["frame_idx"], 64)
        self.assertEqual(workflow["kf1:guide1"]["inputs"]["strength"], 0.8)
        self.assertEqual(workflow["kf0:load"]["inputs"]["image"], "s/kf-1.png")
        self.assertEqual(workflow["398:377"]["inputs"]["video_latent"], ["kf1:guide1", 2])
        self.assertEqual(workflow["398:388"]["inputs"]["positive"], ["kf1:guide1", 0])
        self.assertEqual(workflow["398:340"]["inputs"]["video_latent"], ["kf1:guide2", 2])
        self.assertEqual(workflow["398:391"]["inputs"]["negative"], ["kf1:guide2", 1])
        self.assertEqual(workflow["398:348"]["inputs"]["samples"], ["kf:crop1", 2])
        self.assertEqual(workflow["398:374"]["inputs"]["samples"], ["kf:crop2", 2])
        self.assertEqual(workflow["kf:crop2"]["inputs"]["latent"], ["398:369", 0])
        for node_id, name, target in workflow_links(workflow):
            with self.subTest(node=node_id, input=name):
                self.assertIn(target, workflow)

    def test_keyframes_without_first_frame_keep_text_to_video_base(self):
        self.module.ENABLE_KEYFRAME_GUIDES = True
        params = self.module.validate_scene(self.scene(
            images=[{"url": f"{IMAGE_ORIGIN}/u/b.png", "frame_index": 64}]
        ))
        workflow = self.module.build_scene_workflow(params, ["s/kf-0.png"])
        self.assertIs(workflow["398:363"]["inputs"]["value"], True)
        self.assertEqual(workflow["395"]["inputs"]["image"], self.module.PLACEHOLDER_IMAGE)
        self.assertEqual(workflow["kf0:guide1"]["inputs"]["strength"], 1.0)

    def test_scene_input_rejects_unsafe_or_invalid_values(self):
        for change in (
            {"output_key": "renders/x.mp4"},
            {"output_key": f"users/{USER_ID}/jobs/{JOB_ID}/scenes/partial/other.mp4"},
            {"num_frames": 120},
            {"num_frames": 265},
            {"width": 1000},
            {"height": 736},
            {"job_id": "not-a-uuid"},
            {"r2_bucket": "Bad_Bucket"},
            {"visual_prompt": " "},
            {"seed": -1},
            {"fps": True},
        ):
            with self.subTest(change=change), self.assertRaises(self.module.JobInputError):
                self.module.validate_scene(self.scene(**change))

    def test_image_entries_are_restricted(self):
        self.module.ENABLE_KEYFRAME_GUIDES = True
        valid = {"url": f"{IMAGE_ORIGIN}/u/a.png", "frame_index": 0}
        for images in (
            "not-a-list",
            [valid] * 5,
            [{**valid, "url": "http://images.example.com/u/a.png"}],
            [{**valid, "url": "https://images.example.com.evil.com/a.png"}],
            [{**valid, "url": "https://user:pass@images.example.com/a.png"}],
            [{**valid, "url": "https://images.example.com:8443/a.png"}],
            [{**valid, "frame_index": 4}],
            [{**valid, "frame_index": 121}],
            [valid, {**valid, "url": f"{IMAGE_ORIGIN}/u/b.png"}],
            [{**valid, "strength": 1.5}],
            [{**valid, "strength": True}],
            [{**valid, "extra": 1}],
        ):
            with self.subTest(images=images), self.assertRaises(self.module.JobInputError):
                self.module.validate_scene(self.scene(images=images))
        self.module.ALLOWED_IMAGE_ORIGIN = ""
        with self.assertRaisesRegex(self.module.JobInputError, "not configured"):
            self.module.validate_scene(self.scene(images=[valid]))

    def test_image_download_enforces_type_size_and_no_redirects(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(FakeResponse())
            path = asyncio.run(self.module.download_image(session, f"{IMAGE_ORIGIN}/a", Path(tmp), 0))
            self.assertEqual(path.name, "kf-0.png")
            self.assertEqual(path.read_bytes(), PNG)
            self.assertIs(session.calls[0][1]["allow_redirects"], False)
            for response in (
                FakeResponse(status=302),
                FakeResponse(content_type="text/html"),
                FakeResponse(chunks=(b"<html>not an image</html>",)),
                FakeResponse(chunks=(PNG, b"\x00" * (self.module.MAX_IMAGE_BYTES + 1))),
            ):
                with self.subTest(status=response.status), self.assertRaises(self.module.JobInputError):
                    asyncio.run(self.module.download_image(FakeSession(response), f"{IMAGE_ORIGIN}/a", Path(tmp), 1))

    def test_concat_keys_must_stay_in_the_job_folder(self):
        self.assertEqual(self.module.validate_concat(dict(CONCAT))["output_key"], CONCAT["output_key"])
        for keys in (
            [],
            [f"users/{USER_ID}/jobs/{SCENE_ID}/scenes/partial/{SCENE_ID}.mp4"],
            [f"{JOB_PREFIX}/scenes/partial/../../{SCENE_ID}.mp4"],
            [f"{JOB_PREFIX}/scenes/partial/{SCENE_ID}.mov"],
        ):
            with self.subTest(keys=keys), self.assertRaises(self.module.JobInputError):
                self.module.validate_concat({**CONCAT, "partial_keys": keys})
        with self.assertRaises(self.module.JobInputError):
            self.module.validate_concat({**CONCAT, "output_key": f"{JOB_PREFIX}/scenes/full/other.mp4"})

    def test_webhook_url_must_be_https_and_allowed(self):
        with self.assertRaises(self.module.JobInputError):
            self.module.validate_webhook_url("http://app.example/api/render-webhook")
        self.module.ALLOWED_CALLBACK_ORIGIN = "https://app.example"
        self.module.validate_webhook_url("https://app.example/api/render-webhook")
        with self.assertRaises(self.module.JobInputError):
            self.module.validate_webhook_url("https://app.example.evil.com/api/render-webhook")

    def test_callback_signature_matches_server_format(self):
        import hashlib
        import hmac
        expected = hmac.new(b"secret", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
        self.assertEqual(self.module.sign_callback(b"secret", "1700000000", b'{"a":1}'), expected)

    def test_scene_ffmpeg_args_scale_and_crop_to_target(self):
        args = self.module.scene_ffmpeg_args("in.mp4", "out.mp4", 1280, 720)
        vf = args[args.index("-vf") + 1]
        self.assertIn("scale=1280:720:force_original_aspect_ratio=increase", vf)
        self.assertIn("crop=1280:720", vf)
        self.assertIn("+faststart", args)

    def run_handler(self, job_input, render=None):
        self.module.redis_client = AsyncMock()
        self.module.send_callback = AsyncMock()
        self.module.render_scene = render or AsyncMock()
        self.module.assemble_final = AsyncMock()
        return asyncio.run(self.module.handler({"id": "rp-1", "input": job_input}))

    def test_handler_completes_scene_and_sends_signed_callback_payload(self):
        result = self.run_handler(dict(SCENE))
        self.assertEqual(result, {"status": "completed", "output_key": SCENE["output_key"]})
        self.module.send_callback.assert_awaited_once_with(
            SCENE["webhook_url"],
            {"job_id": JOB_ID, "user_id": USER_ID, "scene_id": SCENE_ID, "status": "completed"},
        )

    def test_handler_completes_concat(self):
        result = self.run_handler(dict(CONCAT))
        self.assertEqual(result["status"], "completed")
        payload = self.module.send_callback.await_args.args[1]
        self.assertEqual(payload, {"task": "concat", "job_id": JOB_ID, "user_id": USER_ID, "status": "completed"})

    def test_handler_reports_failures_without_internal_details(self):
        result = self.run_handler(dict(SCENE), render=AsyncMock(side_effect=RuntimeError("/secret/path boom")))
        self.assertEqual(result["status"], "failed")
        payload = self.module.send_callback.await_args.args[1]
        self.assertEqual(payload["error"], "Rendering failed on the worker.")
        self.run_handler(self.scene(width=1000))
        payload = self.module.send_callback.await_args.args[1]
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "Invalid LTX dimensions or frame count.")

    def test_handler_rejects_bad_envelope_without_callback(self):
        for job_input in (
            {**SCENE, "webhook_url": "http://app.example/hook"},
            {**SCENE, "task": "other"},
            {**SCENE, "job_id": "bad"},
        ):
            with self.subTest(job_input=job_input):
                result = self.run_handler(job_input)
                self.assertEqual(result["status"], "error")
                self.module.send_callback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
