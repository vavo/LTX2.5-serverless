import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import boto3
import redis.asyncio as redis
import runpod
from botocore.config import Config

from workflow_support import build_output_path, collect_output_entries

logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s", "level":"%(levelname)s", "message":"%(message)s"}')
logger = logging.getLogger("ltx-worker")

REDIS_URL = "redis://127.0.0.1:6379"
if os.environ.get("REDIS_URL", REDIS_URL) not in {
    f"redis://{host}:6379{suffix}"
    for host in ("127.0.0.1", "localhost")
    for suffix in ("", "/", "/0")
}:
    raise RuntimeError("External Redis is disabled; unset REDIS_URL to use local Redis.")
redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5, retry_on_timeout=True)

COMFY_NODES = os.environ.get("COMFY_NODES", "127.0.0.1:8188").split(",")
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", "/comfyui/input")
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", "/comfyui/output")
RENDER_TIMEOUT_SECONDS = int(os.environ.get("RENDER_TIMEOUT_SECONDS", "900"))
ALLOWED_CALLBACK_ORIGIN = os.environ.get("ALLOWED_CALLBACK_ORIGIN", "").rstrip("/")
ALLOWED_IMAGE_ORIGIN = os.environ.get("ALLOWED_IMAGE_ORIGIN", "").rstrip("/")
# Keyframes after frame 0 inject guide nodes whose wiring must be verified on a GPU before enabling.
ENABLE_KEYFRAME_GUIDES = os.environ.get("ENABLE_KEYFRAME_GUIDES", "false").lower() == "true"
NSFW_BANNED_WORDS = {"child", "children", "kids", "teen", "lolita", "underage"}

MAX_SCENE_IMAGES = 4
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 30
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")

try:
    with open("video_ltx2_5_i2v_API.json", "r") as f:
        BASE_WORKFLOW = json.load(f)
except Exception:
    raise RuntimeError("Worker cannot start without workflow JSON.")

NODE_MAP = {
    "image": "395",
    "prompt": "398:376",
    "width": "398:372",
    "height": "398:360",
    "fps": "398:361",
    "text_to_video": "398:363",
    "enhancer": "398:380",
    "enhancer_toggle": "398:383",
    "seed1": "398:338",
    "seed2": "398:339",
    "latent_video": "398:356",
    "latent_audio": "398:366",
    "first_frame_stage1": "398:357",
    "first_frame_stage2": "398:349",
    "image_resize": "398:351",
    "image_preprocess": "398:350",
    "conditioning": "398:365",
    "video_vae": "398:385",
    "concat_stage1": "398:377",
    "concat_stage2": "398:340",
    "guider_stage1": "398:388",
    "guider_stage2": "398:391",
    "separate_stage1": "398:367",
    "separate_stage2": "398:369",
    "upsampler": "398:348",
    "decode": "398:374",
    "output": "75",
}
_missing_nodes = [node for node in NODE_MAP.values() if node not in BASE_WORKFLOW]
if _missing_nodes:
    raise RuntimeError(f"Workflow template is missing nodes: {_missing_nodes}")

PLACEHOLDER_IMAGE = "ltx_t2v_placeholder.png"


class JobInputError(ValueError):
    pass


# --- validation ---
def _uuid(inp: dict, name: str) -> str:
    value = inp.get(name)
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise JobInputError(f"Invalid {name}.")
    return value


def _int(inp: dict, name: str, low: int, high: int) -> int:
    value = inp.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise JobInputError(f"Invalid {name}.")
    return value


def _text(inp: dict, name: str, required: bool) -> str:
    value = inp.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > 5000 or (required and not value.strip()):
        raise JobInputError(f"Invalid {name}.")
    return value.strip()


def _bucket(inp: dict) -> str:
    value = inp.get("r2_bucket")
    if not isinstance(value, str) or not BUCKET_RE.match(value):
        raise JobInputError("Invalid r2_bucket.")
    return value


def validate_webhook_url(url) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise JobInputError("webhook_url must be HTTPS.")
    if ALLOWED_CALLBACK_ORIGIN and not url.startswith(ALLOWED_CALLBACK_ORIGIN + "/"):
        raise JobInputError("webhook_url origin is not allowed.")
    return url


def validate_image_url(url) -> str:
    if not ALLOWED_IMAGE_ORIGIN:
        raise JobInputError("Image input is not configured on this worker.")
    if not isinstance(url, str) or len(url) > 2048:
        raise JobInputError("Invalid image url.")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or f"{parts.scheme}://{parts.netloc}".lower() != ALLOWED_IMAGE_ORIGIN.lower()
    ):
        raise JobInputError("Image url is not allowed.")
    return url


def validate_images(inp: dict, num_frames: int) -> list[dict]:
    images = inp.get("images")
    if images is None:
        return []
    if not isinstance(images, list) or len(images) > MAX_SCENE_IMAGES:
        raise JobInputError(f"images must be a list of at most {MAX_SCENE_IMAGES} items.")
    result, frames = [], set()
    for item in images:
        if not isinstance(item, dict) or set(item) - {"url", "frame_index", "strength"}:
            raise JobInputError("Invalid image entry.")
        frame = _int(item, "frame_index", 0, num_frames - 1)
        if frame % 8 or frame in frames:
            raise JobInputError("Image frame_index must be a unique multiple of 8.")
        frames.add(frame)
        strength = item.get("strength")
        if strength is not None and (
            not isinstance(strength, (int, float)) or isinstance(strength, bool) or not 0 <= strength <= 1
        ):
            raise JobInputError("Invalid image strength.")
        result.append({
            "url": validate_image_url(item.get("url")),
            "frame_index": frame,
            "strength": None if strength is None else float(strength),
        })
    result.sort(key=lambda image: image["frame_index"])
    if any(image["frame_index"] > 0 for image in result) and not ENABLE_KEYFRAME_GUIDES:
        raise JobInputError("Keyframes beyond the first frame are not enabled on this worker.")
    return result


def callback_ids(inp: dict) -> dict | None:
    try:
        base = {"job_id": _uuid(inp, "job_id"), "user_id": _uuid(inp, "user_id")}
        if inp.get("task") == "concat":
            return {"task": "concat", **base}
        if inp.get("task") == "scene":
            return {**base, "scene_id": _uuid(inp, "scene_id")}
    except JobInputError:
        pass
    return None


def validate_scene(inp: dict) -> dict:
    job_id, user_id, scene_id = _uuid(inp, "job_id"), _uuid(inp, "user_id"), _uuid(inp, "scene_id")
    p = {
        "visual_prompt": _text(inp, "visual_prompt", True),
        "audio_prompt": _text(inp, "audio_prompt", False),
        "width": _int(inp, "width", 256, 1920),
        "height": _int(inp, "height", 256, 1920),
        "target_width": _int(inp, "target_width", 256, 3840),
        "target_height": _int(inp, "target_height", 256, 3840),
        "num_frames": _int(inp, "num_frames", 9, 257),
        "fps": _int(inp, "fps", 1, 60),
        "seed": _int(inp, "seed", 0, 2**32 - 1),
        "bucket": _bucket(inp),
    }
    # The template renders stage 1 at half size, so full size must stay a multiple of 64.
    if p["width"] % 64 or p["height"] % 64 or (p["num_frames"] - 1) % 8:
        raise JobInputError("Invalid LTX dimensions or frame count.")
    expected = f"users/{user_id}/jobs/{job_id}/scenes/partial/{scene_id}.mp4"
    if inp.get("output_key") != expected:
        raise JobInputError("Unexpected output_key.")
    p["output_key"] = expected
    p["images"] = validate_images(inp, p["num_frames"])
    return p


def validate_concat(inp: dict) -> dict:
    job_id, user_id = _uuid(inp, "job_id"), _uuid(inp, "user_id")
    prefix = f"users/{user_id}/jobs/{job_id}/scenes/partial/"
    keys = inp.get("partial_keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
        raise JobInputError("Invalid partial_keys.")
    for key in keys:
        if (
            not isinstance(key, str)
            or not key.startswith(prefix)
            or not key.endswith(".mp4")
            or not UUID_RE.match(key[len(prefix):-4])
        ):
            raise JobInputError("Invalid partial_keys.")
    expected = f"users/{user_id}/jobs/{job_id}/scenes/full/{job_id}.mp4"
    if inp.get("output_key") != expected:
        raise JobInputError("Unexpected output_key.")
    return {"bucket": _bucket(inp), "partial_keys": keys, "output_key": expected}


# --- input images ---
def detect_image_extension(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


async def download_image(session, url: str, dest_dir: Path, index: int) -> Path:
    async def _fetch() -> bytes:
        async with session.get(url, allow_redirects=False) as resp:
            if resp.status != 200:
                raise JobInputError("Image could not be downloaded.")
            if not resp.headers.get("Content-Type", "").lower().startswith("image/"):
                raise JobInputError("Image url did not return an image.")
            data = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise JobInputError("Image is too large.")
            return bytes(data)

    try:
        data = await asyncio.wait_for(_fetch(), IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise JobInputError("Image download timed out.")
    extension = detect_image_extension(data[:16])
    if not extension:
        raise JobInputError("Image must be PNG, JPEG, or WebP.")
    path = dest_dir / f"kf-{index}.{extension}"
    path.write_bytes(data)
    return path


def ensure_placeholder_image() -> None:
    # LoadImage must resolve a real file even when the first-frame path is bypassed.
    path = Path(COMFY_INPUT_DIR) / PLACEHOLDER_IMAGE
    if path.exists():
        return
    size = 64
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def cleanup_input_files(filepaths: list[str]) -> None:
    input_root = Path(COMFY_INPUT_DIR).resolve()
    for filepath in filepaths:
        try:
            path = Path(os.path.realpath(filepath))
            if not str(path).startswith(str(input_root) + os.sep):
                continue
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning(f"Failed to clean up input file: {filepath}")

    for filepath in filepaths:
        parent = Path(os.path.realpath(filepath)).parent
        while str(parent).startswith(str(input_root) + os.sep) and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


# --- workflow ---
def _node(node_id: str) -> str:
    return NODE_MAP[node_id]


def add_keyframe_guides(workflow: dict, keyframes: list[dict]) -> None:
    images = []
    for i, keyframe in enumerate(keyframes):
        load, resize, preprocess = f"kf{i}:load", f"kf{i}:resize", f"kf{i}:preprocess"
        workflow[load] = {
            "class_type": "LoadImage",
            "_meta": {"title": f"Keyframe {i}"},
            "inputs": {"image": keyframe["name"]},
        }
        workflow[resize] = copy.deepcopy(BASE_WORKFLOW[_node("image_resize")])
        workflow[resize]["inputs"]["input"] = [load, 0]
        workflow[preprocess] = copy.deepcopy(BASE_WORKFLOW[_node("image_preprocess")])
        workflow[preprocess]["inputs"]["image"] = [resize, 0]
        images.append(([preprocess, 0], keyframe))

    stages = (
        ("1", [_node("first_frame_stage1"), 0], "concat_stage1", "guider_stage1", "separate_stage1", "upsampler"),
        ("2", [_node("first_frame_stage2"), 0], "concat_stage2", "guider_stage2", "separate_stage2", "decode"),
    )
    for stage, latent, concat, guider, separate, consumer in stages:
        positive, negative = [_node("conditioning"), 0], [_node("conditioning"), 1]
        for i, (image, keyframe) in enumerate(images):
            guide = f"kf{i}:guide{stage}"
            workflow[guide] = {
                "class_type": "LTXVAddGuide",
                "_meta": {"title": f"Keyframe {i} guide (stage {stage})"},
                "inputs": {
                    "positive": positive,
                    "negative": negative,
                    "vae": [_node("video_vae"), 0],
                    "latent": latent,
                    "image": list(image),
                    "frame_idx": keyframe["frame_index"],
                    "strength": keyframe["strength"],
                },
            }
            positive, negative, latent = [guide, 0], [guide, 1], [guide, 2]
        workflow[_node(concat)]["inputs"]["video_latent"] = latent
        workflow[_node(guider)]["inputs"]["positive"] = positive
        workflow[_node(guider)]["inputs"]["negative"] = negative
        crop = f"kf:crop{stage}"
        workflow[crop] = {
            "class_type": "LTXVCropGuides",
            "_meta": {"title": f"Crop keyframe guides (stage {stage})"},
            "inputs": {"positive": positive, "negative": negative, "latent": [_node(separate), 0]},
        }
        workflow[_node(consumer)]["inputs"]["samples"] = [crop, 2]


def build_scene_workflow(p: dict, image_names: list[str]) -> dict:
    workflow = copy.deepcopy(BASE_WORKFLOW)

    def set_input(node: str, name: str, value) -> None:
        workflow[_node(node)]["inputs"][name] = value

    prompt = p["visual_prompt"] + (f"\n\nAudio: {p['audio_prompt']}" if p["audio_prompt"] else "")
    set_input("prompt", "value", prompt)
    set_input("width", "value", p["width"])
    set_input("height", "value", p["height"])
    set_input("fps", "value", p["fps"])
    set_input("enhancer_toggle", "value", False)
    set_input("enhancer", "sampling_mode", "off")
    set_input("seed1", "noise_seed", p["seed"])
    set_input("seed2", "noise_seed", p["seed"] + 1)
    # The template derives length from whole seconds; pin the exact 8n+1 frame count instead.
    set_input("latent_video", "length", p["num_frames"])
    set_input("latent_audio", "frames_number", p["num_frames"])

    images = [{**image, "name": name} for image, name in zip(p["images"], image_names)]
    first = images[0] if images and images[0]["frame_index"] == 0 else None
    if first:
        set_input("image", "image", first["name"])
        set_input("text_to_video", "value", False)
        if first["strength"] is not None:
            set_input("first_frame_stage1", "strength", first["strength"])
    else:
        set_input("image", "image", PLACEHOLDER_IMAGE)
        set_input("text_to_video", "value", True)

    keyframes = [
        {**image, "strength": 1.0 if image["strength"] is None else image["strength"]}
        for image in images
        if image["frame_index"] > 0
    ]
    if keyframes:
        add_keyframe_guides(workflow, keyframes)
    return workflow


# --- R2 storage ---
def make_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        # R2 rejects some default AWS checksum headers.
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


async def _with_retry(fn):
    for attempt in range(3):
        try:
            return await asyncio.to_thread(fn)
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


async def upload_to_s3_with_retry(filepath: str, bucket: str, key: str, content_type: str = "video/mp4") -> None:
    await _with_retry(
        lambda: make_r2_client().upload_file(filepath, bucket, key, ExtraArgs={"ContentType": content_type})
    )


async def download_from_s3_with_retry(bucket: str, key: str, filepath: str) -> None:
    await _with_retry(lambda: make_r2_client().download_file(bucket, key, filepath))


# --- ffmpeg ---
def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")


def scene_ffmpeg_args(src: str, dst: str, width: int, height: int) -> list[str]:
    # Uniform H.264/AAC output lets the concat step use stream copy.
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},setsar=1"
    return [
        "-i", src, "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst,
    ]


async def concat_videos(paths: list[str], dst: str, list_path: str) -> None:
    Path(list_path).write_text("".join(f"file '{p}'\n" for p in paths))
    base = ["-f", "concat", "-safe", "0", "-i", list_path]
    try:
        await asyncio.to_thread(run_ffmpeg, [*base, "-c", "copy", "-movflags", "+faststart", dst])
    except RuntimeError:
        await asyncio.to_thread(
            run_ffmpeg,
            [*base, "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", dst],
        )


# --- ComfyUI execution ---
class GPUFleetManager:
    @staticmethod
    async def get_best_node(session: aiohttp.ClientSession) -> str:
        best_node, min_queue = None, 999
        for node in COMFY_NODES:
            if await redis_client.get(f"circuit_breaker:{node}"):
                continue
            try:
                async with session.get(f"http://{node}/queue", timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                    data = await resp.json()
                    q_size = len(data.get("queue_running", [])) + len(data.get("queue_pending", []))
                    if q_size < min_queue:
                        best_node, min_queue = node, q_size
            except Exception:
                await redis_client.setex(f"circuit_breaker:{node}", 60, "DEAD")
                logger.warning(f"CIRCUIT BREAKER TRIPPED: {node} flagged as offline.")
        if not best_node:
            raise RuntimeError("No ComfyUI node available.")
        return best_node


async def wait_for_workflow_completion(session, target_node: str, prompt_id: str, start_time: float) -> dict:
    fail_count = 0
    while True:
        elapsed = time.time() - start_time
        if elapsed > RENDER_TIMEOUT_SECONDS:
            raise TimeoutError("Render timeout.")
        try:
            async with session.get(f"http://{target_node}/history/{prompt_id}") as resp:
                history_data = await resp.json()
        except Exception:
            fail_count += 1
            if fail_count > 5:
                raise RuntimeError(f"Node {target_node} disconnected.")
            await asyncio.sleep(2)
            continue
        if prompt_id in history_data:
            return history_data[prompt_id]
        await asyncio.sleep(min(2 + elapsed / 30, 5))


async def execute_workflow_with_failover(session, workflow: dict, job_id: str, start_time: float) -> dict:
    for attempt in range(2):
        target_node = None
        try:
            target_node = await GPUFleetManager.get_best_node(session)
            await redis_client.hset(f"job_status:{job_id}", mapping={"status": "rendering", "node": target_node})
            async with session.post(f"http://{target_node}/prompt", json={"prompt": workflow}) as resp:
                prompt_id = (await resp.json())["prompt_id"]
            return await wait_for_workflow_completion(session, target_node, prompt_id, start_time)
        except Exception as e:
            logger.warning(f"[{job_id}] GPU {target_node} failed. ({e})")
            if target_node:
                await redis_client.setex(f"circuit_breaker:{target_node}", 60, "DEAD")
            if attempt == 1:
                raise RuntimeError("All failover attempts exhausted.")
    raise RuntimeError("All failover attempts exhausted.")


# --- tasks ---
async def render_scene(job_id: str, p: dict, work_dir: str) -> None:
    if any(word in p["visual_prompt"].lower() for word in NSFW_BANNED_WORDS):
        raise JobInputError("Prompt violates safety protocols.")
    ensure_placeholder_image()
    input_dir = Path(COMFY_INPUT_DIR) / f"scene-{uuid.uuid4().hex}"
    written: list[str] = []
    start = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=RENDER_TIMEOUT_SECONDS + 120)) as session:
            image_names = []
            if p["images"]:
                input_dir.mkdir(parents=True, exist_ok=True)
                for index, image in enumerate(p["images"]):
                    path = await download_image(session, image["url"], input_dir, index)
                    written.append(str(path))
                    image_names.append(f"{input_dir.name}/{path.name}")
            workflow = build_scene_workflow(p, image_names)
            history = await execute_workflow_with_failover(session, workflow, job_id, start)
    finally:
        cleanup_input_files(written)

    video = next((e for e in collect_output_entries(history.get("outputs", {})) if e["media_kind"] == "video"), None)
    if not video:
        raise RuntimeError("Workflow completed without a video output.")
    src = build_output_path(COMFY_OUTPUT_DIR, video)
    dst = os.path.join(work_dir, "scene.mp4")
    await redis_client.hset(f"job_status:{job_id}", mapping={"status": "uploading"})
    await asyncio.to_thread(run_ffmpeg, scene_ffmpeg_args(str(src), dst, p["target_width"], p["target_height"]))
    await upload_to_s3_with_retry(dst, p["bucket"], p["output_key"])
    Path(src).unlink(missing_ok=True)


async def assemble_final(p: dict, work_dir: str) -> None:
    local = []
    for index, key in enumerate(p["partial_keys"]):
        path = os.path.join(work_dir, f"{index:02d}.mp4")
        await download_from_s3_with_retry(p["bucket"], key, path)
        local.append(path)
    dst = os.path.join(work_dir, "full.mp4")
    await concat_videos(local, dst, os.path.join(work_dir, "list.txt"))
    await upload_to_s3_with_retry(dst, p["bucket"], p["output_key"])


# --- signed callback ---
def sign_callback(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


async def send_callback(url: str, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    secret = os.environ["RUNPOD_WEBHOOK_SECRET"].encode()
    for attempt in range(6):
        timestamp = str(int(time.time()))
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(
                    url,
                    data=body,
                    allow_redirects=False,
                    headers={
                        "Content-Type": "application/json",
                        "x-render-timestamp": timestamp,
                        "x-render-signature": f"sha256={sign_callback(secret, timestamp, body)}",
                    },
                ) as resp:
                    if resp.status < 300:
                        return
                    # 409 = output not visible yet or not dispatched yet; 5xx = transient.
                    if resp.status != 409 and resp.status < 500:
                        raise RuntimeError(f"Callback rejected with HTTP {resp.status}.")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"Callback attempt {attempt + 1} failed: {e}")
        await asyncio.sleep(min(2 ** (attempt + 1), 60))
    raise RuntimeError("Callback failed after retries.")


# --- handler ---
async def handler(job: dict) -> dict:
    job_id = job.get("id", uuid.uuid4().hex)
    job_input = job.get("input", {})

    if job_input.get("health_check") is True:
        return {"status": "healthy", "service": "ltx-2.5-worker"}

    try:
        webhook_url = validate_webhook_url(job_input.get("webhook_url"))
    except JobInputError as e:
        return {"status": "error", "error": str(e)}
    ids = callback_ids(job_input)
    if ids is None:
        return {"status": "error", "error": "Invalid task or identifiers."}

    await redis_client.hset(f"job_status:{job_id}", mapping={"status": "initializing"})
    await redis_client.expire(f"job_status:{job_id}", 3600)

    work_dir = tempfile.mkdtemp(prefix="ltx-")
    output_key = None
    try:
        if job_input["task"] == "scene":
            params = validate_scene(job_input)
            await render_scene(job_id, params, work_dir)
        else:
            params = validate_concat(job_input)
            await assemble_final(params, work_dir)
        output_key = params["output_key"]
        result = {**ids, "status": "completed"}
    except Exception as e:
        logger.exception(f"[{job_id}] {job_input.get('task')} failed")
        if isinstance(e, JobInputError):
            message = str(e)
        elif job_input.get("task") == "concat":
            message = "Final assembly failed on the worker."
        else:
            message = "Rendering failed on the worker."
        result = {**ids, "status": "failed", "error": message}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    await redis_client.hset(f"job_status:{job_id}", mapping={"status": result["status"]})
    await send_callback(webhook_url, result)
    return {"status": result["status"], "output_key": output_key}


logger.info("Initializing LTX 2.5 serverless worker")
runpod.serverless.start({"handler": handler})
