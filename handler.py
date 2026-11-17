# ==============================================================================
# 🚀 INDRO STUDIO CLOUD - V5 GOD-TIER ENGINE
# Architect: Indro Core Engineering Team
# Features: 
#   - Distributed Circuit Breakers & Seamless Node Failover
#   - UUID-Safe Mutex Locks (Zero Deadlock)
#   - VIP Rate Limiting & Priority Queue Routing
#   - Real-Time Telemetry State Injection (for Frontend Progress Bars)
#   - Auto-Prompt Cinematic Enhancement & NSFW Filtering
# ==============================================================================

import runpod
import base64
import json
import os
import random
import time
import uuid
import logging
import hashlib
import asyncio
import aiohttp
import boto3
from pathlib import Path
from botocore.config import Config
import redis.asyncio as redis

from workflow_support import (
    apply_input_filename_map,
    build_output_path,
    build_workflow_cache_key,
    collect_output_entries,
    guess_media_type,
    is_workflow_job,
    write_input_images,
)

# --- 1. ENTERPRISE OBSERVABILITY & SECURITY ---
logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s", "level":"%(levelname)s", "message":"%(message)s"}')
logger = logging.getLogger("Indro-V5")

API_KEY_SECRET = os.environ.get("INDRO_API_KEY", "dev_token_123")
NSFW_BANNED_WORDS = {"child", "children", "kids", "teen", "lolita", "underage"} # Basic proxy for safety

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
MAX_INLINE_VIDEO_MB = int(os.environ.get("MAX_INLINE_VIDEO_MB", "50"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "604800"))

try:
    with open('video_ltx2_5_i2v_API.json', 'r') as f:
        BASE_WORKFLOW = json.load(f)
except:
    raise RuntimeError("Worker cannot start without workflow JSON.")

try:
    with open('video_ltx2_5_t2v_API.json', 'r') as f:
        T2V_BASE_WORKFLOW = json.load(f)
except:
    raise RuntimeError("Worker cannot start without workflow JSON.")

NODE_MAP = {
    "image": "395",
    "prompt": "398:376",
    "seed1": "398:338",
    "seed2": "398:339",
    "output": "75",
}

# --- 2. ADVANCED AI LOGIC ---
class AIEngine:
    @staticmethod
    def enhance_prompt(prompt: str) -> str:
        """Auto-injects cinematic modifiers if the user provides a lazy prompt."""
        if len(prompt.split()) < 5:
            return f"{prompt}, cinematic lighting, highly detailed, 8k resolution, unreal engine 5 render, photorealistic, masterpiece"
        return prompt

    @staticmethod
    def safety_check(prompt: str) -> bool:
        prompt_lower = prompt.lower()
        return not any(word in prompt_lower for word in NSFW_BANNED_WORDS)

# --- 3. THE DISTRIBUTED CIRCUIT BREAKER (My Custom Addition) ---
class GPUFleetManager:
    @staticmethod
    async def get_best_node(session: aiohttp.ClientSession, is_vip: bool) -> str:
        """Finds the least busy GPU. Skips 'DEAD' nodes using the Circuit Breaker."""
        best_node = None
        min_queue = 999
        max_q_limit = 15 if is_vip else 5 # VIP users bypass standard queue caps
        
        for node in COMFY_NODES:
            # CIRCUIT BREAKER: Check if node is flagged as dead in Redis
            if await redis_client.get(f"circuit_breaker:{node}"):
                continue 

            try:
                async with session.get(f"http://{node}/queue", timeout=1.5) as resp:
                    data = await resp.json()
                    q_size = len(data.get("queue_running", [])) + len(data.get("queue_pending", []))
                    if q_size < min_queue:
                        min_queue = q_size
                        best_node = node
            except Exception:
                # Flag node as DEAD for 60 seconds if it fails to respond
                await redis_client.setex(f"circuit_breaker:{node}", 60, "DEAD")
                logger.warning(f"CIRCUIT BREAKER TRIPPED: {node} flagged as offline.")
                
        if not best_node or min_queue >= max_q_limit:
            raise RuntimeError("FLEET OVERLOAD: All GPUs are busy or offline.")
        return best_node

# --- 4. CLOUD NATIVE STORAGE ---
async def upload_to_s3_with_retry(
    filepath: str, storage_key: str, content_type: str | None = None
) -> str:
    bucket = os.environ.get("AWS_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("AWS_BUCKET_NAME missing.")
    
    boto_config = Config(retries={'max_attempts': 3, 'mode': 'standard'})
    def _upload():
        s3 = boto3.client('s3', config=boto_config)
        extra_args = {'ContentType': content_type} if content_type else None
        if extra_args:
            s3.upload_file(filepath, bucket, storage_key, ExtraArgs=extra_args)
        else:
            s3.upload_file(filepath, bucket, storage_key)
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': storage_key},
            ExpiresIn=604800,
        )
    
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_upload)
        except Exception as e:
            if attempt == 2:
                raise e
            await asyncio.sleep(2 ** attempt)


def decode_cached_response(raw_value: str) -> dict | None:
    try:
        cached_response = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(cached_response, dict) or cached_response.get("status") != "success":
        return None

    response = dict(cached_response)
    response["cached"] = True
    return response


async def build_result_payload(filepath: str, job_id: str) -> dict:
    if os.environ.get("AWS_BUCKET_NAME"):
        return {
            "video_url": await upload_to_s3_with_retry(
                filepath,
                f"renders/{job_id}.mp4",
                "video/mp4",
            )
        }

    file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
    if file_size_mb > MAX_INLINE_VIDEO_MB:
        raise RuntimeError(
            f"Video output is {file_size_mb:.1f}MB, which exceeds MAX_INLINE_VIDEO_MB={MAX_INLINE_VIDEO_MB}. "
            "Configure S3 upload or raise the inline limit."
        )

    with open(filepath, "rb") as video_file:
        return {"video_base64": base64.b64encode(video_file.read()).decode("utf-8")}


def build_job_image_inputs(
    job_id: str, images: list[dict[str, str]] | None
) -> tuple[dict[str, str], list[dict[str, str]]]:
    if not images:
        return {}, []

    replacements: dict[str, str] = {}
    prepared_images: list[dict[str, str]] = []

    for image in images:
        original_name = image.get("name")
        image_data = image.get("image")
        if not original_name or not image_data:
            raise ValueError(
                "'images' must be a list of objects with 'name' and 'image' keys."
            )

        unique_name = str(Path(job_id) / Path(original_name)).replace("\\", "/")
        replacements[original_name] = unique_name
        prepared_images.append({"name": unique_name, "image": image_data})

    return replacements, prepared_images


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


async def build_output_entry(
    filepath: str,
    job_id: str,
    entry: dict[str, str],
    index: int,
) -> dict:
    media_type = guess_media_type(entry["filename"], entry["media_kind"])
    path = Path(filepath)

    if entry["media_kind"] == "video":
        file_size_mb = path.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_INLINE_VIDEO_MB and not os.environ.get("AWS_BUCKET_NAME"):
            raise RuntimeError(
                f"Video output is {file_size_mb:.1f}MB, which exceeds MAX_INLINE_VIDEO_MB={MAX_INLINE_VIDEO_MB}. "
                "Configure S3 upload or raise the inline limit."
            )

    if os.environ.get("AWS_BUCKET_NAME"):
        storage_key = f"renders/{job_id}/{index:02d}-{path.name}"
        data = await upload_to_s3_with_retry(filepath, storage_key, media_type)
        output_type = "url"
    else:
        with open(filepath, "rb") as file_handle:
            data = base64.b64encode(file_handle.read()).decode("utf-8")
        output_type = "base64"

    return {
        "filename": entry["filename"],
        "subfolder": entry.get("subfolder", ""),
        "type": output_type,
        "data": data,
        "media_type": media_type,
    }


async def build_workflow_output_payload(history_entry: dict, job_id: str) -> dict:
    entries = collect_output_entries(history_entry.get("outputs", {}))
    if not entries:
        raise RuntimeError("Workflow completed without supported outputs.")

    output: dict[str, list[dict]] = {"images": [], "videos": []}
    for index, entry in enumerate(entries):
        output_path = build_output_path(COMFY_OUTPUT_DIR, entry)
        if not output_path.exists():
            raise RuntimeError(f"Expected output file missing: {output_path}")

        payload = await build_output_entry(str(output_path), job_id, entry, index)
        collection = "images" if entry["media_kind"] == "image" else "videos"
        output[collection].append(payload)

    return {key: value for key, value in output.items() if value}


def extract_custom_video_filename(history_entry: dict) -> str | None:
    node_output = history_entry.get('outputs', {}).get(NODE_MAP["output"], {})
    for key in ["videos", "gifs"]:
        if key in node_output and node_output[key]:
            return node_output[key][0]['filename']
    return None


async def wait_for_workflow_completion(
    session: aiohttp.ClientSession,
    target_node: str,
    prompt_id: str,
    start_time: float,
) -> dict:
    fail_count = 0
    while True:
        elapsed = time.time() - start_time
        if elapsed > 900:
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


async def execute_workflow_with_failover(
    session: aiohttp.ClientSession,
    workflow: dict,
    job_id: str,
    is_vip: bool,
    start_time: float,
) -> tuple[str, dict]:
    for failover_attempt in range(2):
        target_node = None
        try:
            target_node = await GPUFleetManager.get_best_node(session, is_vip)
            await redis_client.hset(
                f"job_status:{job_id}",
                mapping={"status": "rendering", "node": target_node},
            )
            logger.info(f"[{job_id}] Routed to Node: {target_node}")

            async with session.post(
                f"http://{target_node}/prompt", json={"prompt": workflow}
            ) as resp:
                prompt_response = await resp.json()
                prompt_id = prompt_response["prompt_id"]

            history_entry = await wait_for_workflow_completion(
                session,
                target_node,
                prompt_id,
                start_time,
            )
            return target_node, history_entry
        except Exception as e:
            logger.warning(f"[{job_id}] GPU {target_node} failed. ({str(e)})")
            if target_node:
                await redis_client.setex(f"circuit_breaker:{target_node}", 60, "DEAD")
            if failover_attempt == 1:
                raise RuntimeError("All failover attempts exhausted.")
            logger.info(f"[{job_id}] Initiating Seamless Failover to new GPU...")

    raise RuntimeError("All failover attempts exhausted.")


async def handle_custom_job(job_id: str, job_input: dict, start_time: float) -> dict:
    api_key = job_input.get("api_key")
    if api_key != API_KEY_SECRET:
        raise PermissionError("401 Unauthorized")

    priority = job_input.get("priority", "standard")
    is_vip = priority == "vip"

    rate_key = "rate_limit:legacy"
    req_count = await redis_client.incr(rate_key)
    if req_count == 1:
        await redis_client.expire(rate_key, 60)
    limit = 50 if is_vip else 10
    if req_count > limit:
        raise PermissionError("429 Too Many Requests. Rate Limit Exceeded.")

    raw_prompt = job_input.get("prompt", "")
    image_url = job_input.get("image_url", "")

    mode = job_input.get("mode", "i2v")
    if mode not in ("i2v", "t2v"):
        raise ValueError(f"Unsupported mode: {mode}.")

    if mode == "i2v":
        if not image_url or not raw_prompt:
            raise ValueError("Missing 'image_url' or 'prompt'.")
    else:
        if not raw_prompt:
            raise ValueError("Missing 'prompt'.")

    if not AIEngine.safety_check(raw_prompt):
        raise ValueError("Prompt violates safety protocols.")

    enhanced_prompt = AIEngine.enhance_prompt(raw_prompt)
    cache_hash = hashlib.sha256(f"{mode}_{image_url}_{enhanced_prompt}".encode()).hexdigest()
    lock_token = str(uuid.uuid4())

    redis_state = await redis_client.get(cache_hash)
    cached_response = decode_cached_response(redis_state)
    if cached_response:
        await redis_client.hset(
            f"job_status:{job_id}",
            mapping={"status": "completed", "cache_hit": "true"},
        )
        return cached_response

    lock_acquired = await redis_client.set(cache_hash, lock_token, ex=1200, nx=True)
    if not lock_acquired:
        logger.info(f"[{job_id}] DEDUPLICATION ACTIVE. Waiting...")
        await redis_client.hset(
            f"job_status:{job_id}",
            mapping={"status": "waiting_in_queue"},
        )
        for _ in range(240):
            await asyncio.sleep(5)
            new_state = await redis_client.get(cache_hash)
            cached_response = decode_cached_response(new_state)
            if cached_response:
                return cached_response
        raise TimeoutError("Deduplication timeout.")

    try:
        if mode == "i2v":
            workflow = json.loads(json.dumps(BASE_WORKFLOW))
            workflow[NODE_MAP["image"]]["inputs"]["image"] = image_url
        else:
            workflow = json.loads(json.dumps(T2V_BASE_WORKFLOW))
        workflow[NODE_MAP["prompt"]]["inputs"]["value"] = enhanced_prompt
        workflow[NODE_MAP["seed1"]]["inputs"]["noise_seed"] = random.randint(1, 10**15)
        workflow[NODE_MAP["seed2"]]["inputs"]["noise_seed"] = random.randint(1, 10**15)

        http_timeout = aiohttp.ClientTimeout(total=1000)
        async with aiohttp.ClientSession(timeout=http_timeout) as session:
            target_node, history_entry = await execute_workflow_with_failover(
                session, workflow, job_id, is_vip, start_time
            )

        video_filename = extract_custom_video_filename(history_entry)
        if not video_filename:
            raise RuntimeError("Workflow completed without a video output.")

        await redis_client.hset(f"job_status:{job_id}", mapping={"status": "uploading"})
        output_video_path = os.path.join(COMFY_OUTPUT_DIR, video_filename)
        result_payload = await build_result_payload(output_video_path, job_id)
        response = {
            "status": "success",
            **result_payload,
            "metadata": {
                "render_time_sec": round(time.time() - start_time, 2),
                "node_used": target_node,
            },
        }

        current_lock = await redis_client.get(cache_hash)
        if current_lock == lock_token:
            await redis_client.set(cache_hash, json.dumps(response), ex=CACHE_TTL_SECONDS)

        return response
    finally:
        try:
            if await redis_client.get(cache_hash) == lock_token:
                await redis_client.delete(cache_hash)
        except Exception:
            pass


async def handle_workflow_job(job_id: str, job_input: dict, start_time: float) -> dict:
    workflow = job_input.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("Missing 'workflow'.")

    images = job_input.get("images")
    priority = job_input.get("priority", "standard")
    is_vip = priority == "vip"

    cache_hash = build_workflow_cache_key(workflow, images)
    lock_token = str(uuid.uuid4())

    redis_state = await redis_client.get(cache_hash)
    cached_response = decode_cached_response(redis_state)
    if cached_response:
        await redis_client.hset(
            f"job_status:{job_id}",
            mapping={"status": "completed", "cache_hit": "true"},
        )
        return cached_response

    lock_acquired = await redis_client.set(cache_hash, lock_token, ex=1200, nx=True)
    if not lock_acquired:
        logger.info(f"[{job_id}] DEDUPLICATION ACTIVE. Waiting...")
        await redis_client.hset(
            f"job_status:{job_id}",
            mapping={"status": "waiting_in_queue"},
        )
        for _ in range(240):
            await asyncio.sleep(5)
            new_state = await redis_client.get(cache_hash)
            cached_response = decode_cached_response(new_state)
            if cached_response:
                return cached_response
        raise TimeoutError("Deduplication timeout.")

    written_input_files: list[str] = []
    try:
        name_map, prepared_images = build_job_image_inputs(job_id, images)
        prepared_workflow = apply_input_filename_map(workflow, name_map)
        written_input_files = write_input_images(COMFY_INPUT_DIR, prepared_images)

        http_timeout = aiohttp.ClientTimeout(total=1000)
        async with aiohttp.ClientSession(timeout=http_timeout) as session:
            target_node, history_entry = await execute_workflow_with_failover(
                session,
                prepared_workflow,
                job_id,
                is_vip,
                start_time,
            )

        await redis_client.hset(f"job_status:{job_id}", mapping={"status": "uploading"})
        output_payload = await build_workflow_output_payload(history_entry, job_id)
        response = {
            "status": "success",
            "output": output_payload,
            "metadata": {
                "render_time_sec": round(time.time() - start_time, 2),
                "node_used": target_node,
            },
        }

        current_lock = await redis_client.get(cache_hash)
        if current_lock == lock_token:
            await redis_client.set(cache_hash, json.dumps(response), ex=CACHE_TTL_SECONDS)

        return response
    finally:
        cleanup_input_files(written_input_files)
        try:
            if await redis_client.get(cache_hash) == lock_token:
                await redis_client.delete(cache_hash)
        except Exception:
            pass

# --- 5. THE MASTER HANDLER ---
async def handler(job: dict) -> dict:
    job_id = job.get('id', uuid.uuid4().hex)
    job_input = job.get('input', {})
    start_time = time.time()

    if job_input.get("health_check") is True:
        return {"status": "healthy", "service": "ltx-2.5-worker"}
    
    # TELEMETRY: Announce Job Start
    await redis_client.hset(f"job_status:{job_id}", mapping={"status": "initializing", "progress": "0%"})
    await redis_client.expire(f"job_status:{job_id}", 3600)

    try:
        if is_workflow_job(job_input):
            response = await handle_workflow_job(job_id, job_input, start_time)
        else:
            response = await handle_custom_job(job_id, job_input, start_time)

        await redis_client.hset(f"job_status:{job_id}", mapping={"status": "completed"})
        return response

    except Exception as e:
        await redis_client.hset(f"job_status:{job_id}", mapping={"status": "failed", "error": str(e)})
        return {"status": "error", "error": str(e)}

logger.info("Initializing Indro Serverless Engine V5 (GOD-TIER)...")
runpod.serverless.start({"handler": handler})
