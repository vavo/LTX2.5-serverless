import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import frontend_app


class FakeResponse:
    def __init__(self, *, status: int, json_data=None, text_data: str | None = None):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self.headers = {"Content-Type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        if self._text_data is not None:
            return self._text_data
        return json.dumps(self._json_data or {})

    async def json(self):
        if self._json_data is not None:
            return self._json_data
        if self._text_data is not None:
            return json.loads(self._text_data)
        return {}


class FakeClientSession:
    def __init__(self, *, post_response=None, get_response=None, **kwargs):
        self._post_response = post_response
        self._get_response = get_response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json, **kwargs):
        self.requests.append((url, kwargs))
        return self._post_response

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self._get_response


class TestFrontendApp(unittest.TestCase):
    def setUp(self) -> None:
        frontend_app.POD_SUBMIT_JOBS.clear()
        self.client = TestClient(frontend_app.app)

    def tearDown(self) -> None:
        frontend_app.POD_SUBMIT_JOBS.clear()

    def test_pod_submit_returns_queued_job(self) -> None:
        payload = {
            "payload": {
                "input": {
                    "workflow": {"1": {"inputs": {"image": "source.png"}}},
                    "images": [{"name": "source.png", "image": "dGVzdA=="}],
                }
            }
        }

        with (
            patch.object(frontend_app, "get_pod_submit_node", return_value="127.0.0.1:8188"),
            patch.object(frontend_app, "apply_input_filename_map", side_effect=lambda workflow, _: workflow),
            patch.object(frontend_app, "write_input_images", return_value=["/tmp/source.png"]),
            patch.object(
                frontend_app.aiohttp,
                "ClientSession",
                return_value=FakeClientSession(
                    post_response=FakeResponse(status=200, json_data={"prompt_id": "prompt-123"})
                ),
            ),
        ):
            response = self.client.post("/api/pod-submit", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["response_json"]["status"], "queued")
        self.assertEqual(body["response_json"]["prompt_id"], "prompt-123")
        self.assertEqual(
            frontend_app.POD_SUBMIT_JOBS["prompt-123"]["filepaths"],
            ["/tmp/source.png"],
        )

    def test_pod_submit_status_completion_cleans_tracked_files(self) -> None:
        frontend_app.remember_pod_submit_job("prompt-456", ["/tmp/source.png"])

        with (
            patch.object(
                frontend_app.aiohttp,
                "ClientSession",
                return_value=FakeClientSession(
                    get_response=FakeResponse(
                        status=200,
                        json_data={
                            "prompt-456": {
                                "outputs": {
                                    "1": {
                                        "images": [{"filename": "frame.png", "subfolder": ""}],
                                        "videos": [{"filename": "clip.mp4", "subfolder": ""}],
                                    }
                                }
                            }
                        },
                    )
                ),
            ),
            patch.object(frontend_app, "cleanup_input_files") as cleanup_mock,
            patch.object(frontend_app.time, "time", return_value=110.25),
        ):
            frontend_app.POD_SUBMIT_JOBS["prompt-456"]["submitted_at"] = 100.0
            response = self.client.get("/api/pod-submit/prompt-456?node=127.0.0.1:8188")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["response_json"]["status"], "completed")
        self.assertEqual(body["response_json"]["metadata"]["render_time_sec"], 10.25)
        cleanup_mock.assert_called_once_with(["/tmp/source.png"])
        self.assertNotIn("prompt-456", frontend_app.POD_SUBMIT_JOBS)
        self.assertEqual(
            body["response_json"]["output"]["videos"][0]["url"],
            "/api/comfy-output?filename=clip.mp4&subfolder=&media_kind=video",
        )

    def test_config_reports_text_to_video_enabled(self) -> None:
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIs(body["text_to_video_enabled"], True)
        self.assertIn("t2v", body["modes"])

    def test_payload_t2v_mode_omits_images(self) -> None:
        response = self.client.post(
            "/api/payload",
            json={"prompt": "A drone shot flying over mountains.", "mode": "t2v"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["mode"], "t2v")
        self.assertNotIn("images", body["payload"]["input"])

    def test_payload_rejects_unknown_mode(self) -> None:
        response = self.client.post(
            "/api/payload",
            json={"prompt": "A drone shot flying over mountains.", "mode": "v2v"},
        )

        self.assertEqual(response.status_code, 422)

    def test_comfy_output_returns_file_response(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp_file:
            with patch.object(
                frontend_app,
                "build_output_path",
                return_value=frontend_app.Path(tmp_file.name),
            ):
                response = self.client.get(
                    "/api/comfy-output?filename=result.mp4&subfolder=&media_kind=video"
                )

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
