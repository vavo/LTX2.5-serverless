# Configuration

The container is configured through environment variables. The model profile baked into an image is only a startup default; runtime variables can override it.

## Runtime

| Variable | Description | Default |
| --- | --- | --- |
| `RUN_MODE` | `worker`, `local-api`, or `pod`. | `worker` |
| `SERVE_API_LOCALLY` | Legacy fallback to `local-api` when `RUN_MODE` is unset. | `false` |
| `LTX_FRONTEND_ENABLED` | Start the bundled frontend on port `7777`. | `true` |
| `PUBLIC_KEY` | Optional SSH public key. When set, SSH starts in the container. | unset |
| `COMFY_LOG_LEVEL` | ComfyUI log level. | `DEBUG` |
| `LTX25_MODEL_DISCOVERY_CHECK` | Fail startup unless preloaded LTX models are visible through ComfyUI's live model API. | `true` |
| `COMFY_MODEL_DISCOVERY_TIMEOUT_SECONDS` | Maximum time to wait for live model discovery. | `120` |
| `COMFY_MODEL_DISCOVERY_URL` | Local ComfyUI URL used by the startup acceptance check. | `http://127.0.0.1:8188` |

`worker` starts ComfyUI, the frontend, and the RunPod serverless handler. `local-api` replaces the RunPod worker loop with a local compatible API on port `8000`. `pod` starts ComfyUI and the frontend without a handler.

## LTX 2.5 preload

| Variable | Description | Default |
| --- | --- | --- |
| `LTX25_PRELOAD_VARIANT` | Startup model profile. Supported value: `distilled-int8`; empty disables preload. | image default |
| `LTX25_PRELOAD_PROMPT_ENHANCER` | Download the Gemma 4 prompt enhancer when a profile is enabled. | `true` |
| `LTX25_DOWNLOAD_BACKEND` | `auto`, `hf_hub`, or `wget`. | `auto` |
| `HUGGINGFACE_ACCESS_TOKEN` | Hugging Face read token used for gated downloads. `HF_TOKEN` and `HUGGINGFACE_TOKEN` are accepted aliases. | unset |

Accept the [LTX 2.5 model terms](https://huggingface.co/Lightricks/LTX-2.5) before first boot. A token is required while the gated files are missing; an already-populated persistent volume does not need to redownload them.

Recommended serverless values:

```env
PERSIST_WORKSPACE=true
RUN_MODE=worker
COMFY_NODES=127.0.0.1:8188
LTX25_PRELOAD_VARIANT=distilled-int8
LTX25_PRELOAD_PROMPT_ENHANCER=true
HUGGINGFACE_ACCESS_TOKEN=hf_xxx
```

## Persistent workspace

| Variable | Description | Default |
| --- | --- | --- |
| `PERSIST_WORKSPACE` | Persist ComfyUI, the venv, caches, workflows, and models. | `true` |
| `WORKSPACE_ROOT` | Override the detected persistent root. | `/workspace` when available |
| `WORKSPACE_STATE_ROOT` | Persisted ComfyUI/venv/cache state directory. | `<WORKSPACE_ROOT>/worker-comfyui` |
| `COMFY_BOOTSTRAP_REFRESH_CUSTOM_NODES` | Comma-separated baked node directories refreshed during bootstrap. | `ComfyUI-Downloader` |
| `COMFY_BOOTSTRAP_WORKFLOWS` | Comma-separated editor-format workflows copied into the persisted ComfyUI user directory. | `video_ltx2_5_i2v.json,video_ltx2_5_t2v.json` |
| `BOOTSTRAP_PROGRESS_HEARTBEAT_SECONDS` | Interval for long seed-operation progress logs. | `15` |
| `BOOTSTRAP_LOCK_TIMEOUT_SECONDS` | Maximum wait for the shared bootstrap lock. | `600` |
| `BOOTSTRAP_LOCK_POLL_SECONDS` | Shared-lock polling interval. | `2` |
| `BOOTSTRAP_LOCK_STALE_SECONDS` | Age at which an unrefreshed lock is considered stale. | `120` |
| `BOOTSTRAP_LOCK_HEARTBEAT_SECONDS` | Lock timestamp refresh interval. | `5` |

On Serverless, RunPod mounts a network volume at `/runpod-volume`; startup aliases it to `/workspace`. Multiple workers sharing a volume coordinate first-boot seeding with `/workspace/worker-comfyui/.bootstrap.lock`.

| Purpose | Path |
| --- | --- |
| ComfyUI code and user state | `/workspace/worker-comfyui/comfyui` |
| Python virtualenv | `/workspace/worker-comfyui/venv` |
| Download/compiler caches | `/workspace/worker-comfyui/cache` |
| Models | `/workspace/models`, linked at `/comfyui/models` |
| Handler input/output | `/comfyui/input`, `/comfyui/output` |
| Extra model path configuration | `/comfyui/extra_model_paths.yaml` |

ComfyUI Manager is forced to offline mode at every boot. Install custom nodes in the image; runtime Manager installs are intentionally unavailable.

The handler and bundled frontend execute `/video_ltx2_5_i2v_API.json` and `/video_ltx2_5_t2v_API.json`. ComfyUI's user workflow library receives the separate editor-format `/video_ltx2_5_i2v.json` and `/video_ltx2_5_t2v.json`; bootstrap removes both older API-format files from that library during migration.

## Handler and ComfyUI

| Variable | Description | Default |
| --- | --- | --- |
| `COMFY_NODES` | Comma-separated ComfyUI API hosts used by the handler. | `127.0.0.1:8188` |
| `LOCAL_COMFY_NODE` | ComfyUI host used by the bundled frontend. | `127.0.0.1:8188` |
| `COMFY_INPUT_DIR` | Uploaded workflow input staging directory. | `/comfyui/input` |
| `COMFY_OUTPUT_DIR` | Generated artifact pickup directory. | `/comfyui/output` |
| `COMFYUI_MANAGER_CONFIG` | Manager `config.ini` updated during startup. | `/comfyui/user/__manager/config.ini` |
| `REDIS_URL` | Local Redis only. Leave unset; external servers and URL options are rejected. Accepts `127.0.0.1` or `localhost` on port `6379`, with no path, `/`, or `/0`; always connects to `127.0.0.1`. | `redis://127.0.0.1:6379` |
| `CACHE_TTL_SECONDS` | Successful response cache lifetime in seconds. | `604800` |
| `MAX_INLINE_VIDEO_MB` | Maximum inline video response size before S3 becomes mandatory. | `50` |
| `INDRO_API_KEY` | Authentication for the legacy `prompt` + `image_url` path only. | `dev_token_123` |

## Redis and cached results

Redis is the worker's temporary job notebook. In `worker` and `local-api` modes, startup launches it inside the container on `127.0.0.1:6379`, with protected mode enabled and disk persistence disabled. Leave `REDIS_URL` unset; no external Redis service is needed. External addresses, credentials, query options, alternate ports, and nonzero databases are rejected without printing the supplied URL. Direct handler startup, including Hub validation, also enforces the local address. `pod` mode skips Redis entirely.

The worker stores the following in Redis:

- Job status and progress, duplicate-job locks, and temporarily unavailable ComfyUI nodes.
- Completed responses for `CACHE_TTL_SECONDS` (seven days by default). These contain the actual base64-encoded artifacts when S3 is disabled, or download links when S3 is enabled.
- A request counter for the legacy API. Authentication happens before counting, and the counter name contains no API key.

This state belongs to one worker: caching, deduplication, and rate limits are not coordinated across workers. Restarting the bundled Redis process or replacing its container loses that state. This does not delete generated files, S3 objects, or the model and compiler caches on persistent storage.

The local connection restriction prevents the worker from sending this state to an external Redis provider. Pod and host administrators can still inspect local processes. Data sent to an external Redis server by an older image remains there until separately removed; see [upgrading from external Redis](deployment.md#upgrading-from-external-redis).

## S3 artifact uploads

When `AWS_BUCKET_NAME` is unset, artifacts are returned inline as base64. When it is set, the handler uploads artifacts with `boto3` and returns presigned URLs.

| Variable | Description |
| --- | --- |
| `AWS_BUCKET_NAME` | Bucket used for generated artifacts. Enables S3 mode. |
| `AWS_ACCESS_KEY_ID` | AWS access key ID with `s3:PutObject` access. |
| `AWS_SECRET_ACCESS_KEY` | Matching secret access key. |
| `AWS_DEFAULT_REGION` | Bucket region. |

Example workflow response:

```json
{
  "status": "success",
  "output": {
    "videos": [
      {
        "filename": "LTX-2.5_i2v.mp4",
        "type": "url",
        "data": "https://example-bucket.s3.amazonaws.com/renders/job-123/00-LTX-2.5_i2v.mp4?...",
        "media_type": "video/mp4"
      }
    ]
  },
  "metadata": {
    "render_time_sec": 42.1,
    "node_used": "127.0.0.1:8188"
  }
}
```
