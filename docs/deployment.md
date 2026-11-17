# Deployment

Deploy the CUDA 13.0 LTX 2.5 image as the primary path. Keep the CUDA 12.8 tag only for environments where the cu130 image cannot run.

Both targets use an Ubuntu 24.04 base. Their pinned PyTorch wheels install the selected CUDA user-space runtime; RunPod supplies the compatible NVIDIA host driver through the container runtime.

## 1. Build and publish

The repository does not prove that any public image tag has already been published. Build and push to a registry you control:

```bash
docker buildx bake ltx2-5-distilled-int8 \
  --set 'ltx2-5-distilled-int8.tags=<registry>/<image>:<version>-ltx2.5-distilled-int8-cu130' \
  --push
```

Fallback target:

```bash
docker buildx bake ltx2-5-distilled-int8-cu128 \
  --set 'ltx2-5-distilled-int8-cu128.tags=<registry>/<image>:<version>-ltx2.5-distilled-int8-cu128' \
  --push
```

The bake file pins `linux/amd64`. If you use `docker build` directly, specify `--platform linux/amd64`; images built natively on Apple Silicon will not become RunPod-compatible through positive thinking.

## 2. Create a Serverless template

Create a Serverless template in the RunPod console and set:

- Container image: the fully qualified tag pushed above.
- Runtime command: leave the image default unchanged.
- Registry credentials: configure them only for a private registry.
- Environment: use the first-boot values below.
- Container disk: size for the image and temporary runtime data; keep model weights on a network volume.

```env
PERSIST_WORKSPACE=true
RUN_MODE=worker
COMFY_NODES=127.0.0.1:8188
LTX25_PRELOAD_VARIANT=distilled-int8
LTX25_PRELOAD_PROMPT_ENHANCER=true
HUGGINGFACE_ACCESS_TOKEN=hf_xxx
```

Accept the [LTX 2.5 license](https://huggingface.co/Lightricks/LTX-2.5) before booting the template.

Leave `REDIS_URL` unset. Each worker starts Redis locally; external Redis services are unsupported. Job tracking and result caching are local to each worker, even when workers share a model volume. See [Redis and cached results](configuration.md#redis-and-cached-results).

## 3. Create the endpoint

Create a queue-based Serverless endpoint from the template:

- Start with one GPU per worker.
- Prioritize a Blackwell GPU compatible with the CUDA 13 image.
- Start at 48 GB VRAM for the distilled INT8 workflow; validate memory use with the exact resolution, duration, and node graph before production.
- Attach at least 100 GB of persistent network storage for the default weights and caches.
- Start with one worker until first-boot preload completes, then configure scaling.
- Set execution timeout above the measured render time of the longest supported request.

RunPod mounts Serverless volumes at `/runpod-volume`; this worker aliases that path to `/workspace`. Current RunPod settings and GPU choices are documented in the [endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations) and [network volumes](https://docs.runpod.io/storage/network-volumes) guides.

## 4. Smoke test

Use one of the checked-in workflows — [`video_ltx2_5_i2v_API.json`](../video_ltx2_5_i2v_API.json) or [`video_ltx2_5_t2v_API.json`](../video_ltx2_5_t2v_API.json) — and the request shape in the [README](../README.md#run-a-worker-job). Verify:

1. Bootstrap reports the persisted LTX stack as ready.
2. ComfyUI starts without missing-node or missing-model errors.
3. `/health` responds.
4. `/runsync` completes a small I2V job.
5. The response contains `output.videos[]` and the file is decodable.
6. A second worker boot reuses the persisted state instead of downloading weights again.

The repository test suite validates configuration and workflow transformation without a GPU. It is not a substitute for this smoke test.

RunPod Hub validation sets `RUNPOD_HUB_VALIDATION=true` and uses the
credential-free `health_check` handler input. This test-only startup path skips
workspace setup, model downloads, ComfyUI, and the frontend, so the public Hub
build does not need a Hugging Face token or wait for the full runtime. Real
workers do not set this flag; they preload the gated model stack at startup and
require `HF_TOKEN`, `HUGGINGFACE_TOKEN`, or `HUGGINGFACE_ACCESS_TOKEN` with
accepted LTX 2.5 access.

## Upgrading from external Redis

The local Redis restriction is included in source commit `ed6617d`. A Git push alone does not update an existing image or running worker.

1. Build and publish an image containing that commit or a later revision, using a new version tag and the `linux/amd64` build target above.
2. Remove the external `REDIS_URL` setting from the RunPod template or container environment. Leaving it configured causes startup to fail with `External Redis is disabled; unset REDIS_URL to use local Redis.`
3. Update the deployment to the new image and replace the existing workers or containers. Merely restarting an old image will keep the old behavior.
4. For `worker` or `local-api`, confirm that startup reports Redis ready or already available at `redis://127.0.0.1:6379`, then complete the generation smoke test above. Pod mode reports that it skips Redis.

Old Redis data is not migrated or deleted. If an earlier deployment used external Redis, remove its cached results separately. If you used the legacy `input.api_key` route, rotate `INDRO_API_KEY` and update its callers: older versions included that secret in Redis counter names. The new worker uses a counter name without key material.

Expect an empty result cache after replacement. Models and download/compiler caches remain on the attached volume. This update restricts Redis connections; it does not add authentication to the frontend or change S3 uploads.

## Image and API compatibility

| Target | CUDA | Tag suffix |
| --- | --- | --- |
| `base` | 13.0.2 | `<version>-base` |
| `base-cuda12-8-1` | 12.8.1 | `<version>-base-cuda12.8.1` |
| `ltx2-5-distilled-int8` | 13.0.2 | `<version>-ltx2.5-distilled-int8-cu130` |
| `ltx2-5-distilled-int8-cu128` | 12.8.1 | `<version>-ltx2.5-distilled-int8-cu128` |

New clients should send `input.workflow` plus optional `input.images`. The older `input.prompt`, `input.image_url`, and `input.api_key` route remains only for compatibility; it defaults to image-to-video and needs an explicit `input.mode` of `t2v`, without `input.image_url`, to run text-to-video. Audio can participate in the bundled LTX workflow, but the handler currently exposes only image and video artifact collections.

## Pod mode

For a persistent Pod instead of Serverless, use the same image and model volume with:

```env
PERSIST_WORKSPACE=true
RUN_MODE=pod
LOCAL_COMFY_NODE=127.0.0.1:8188
LTX25_PRELOAD_VARIANT=distilled-int8
LTX25_PRELOAD_PROMPT_ENHANCER=true
HUGGINGFACE_ACCESS_TOKEN=hf_xxx
```

Pod mode starts ComfyUI on `8188` and the frontend on `7777`, and skips the RunPod serverless handler.
