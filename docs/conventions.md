# Project conventions

This repository packages ComfyUI as a RunPod worker for LTX 2.5. CUDA 13.0 is the primary path; CUDA 12.8 is the only fallback.

## Runtime design

- Docker images contain code, pinned dependencies, ComfyUI, and required custom nodes.
- Model weights, ComfyUI state, the Python venv, and caches persist under `/workspace`.
- The recommended target is `ltx2-5-distilled-int8` on CUDA 13.0.
- The image starts from Ubuntu 24.04; pinned PyTorch wheels provide CUDA user-space libraries without duplicating an NVIDIA CUDA runtime base.
- The checked-in API workflows are [`video_ltx2_5_i2v_API.json`](../video_ltx2_5_i2v_API.json) and [`video_ltx2_5_t2v_API.json`](../video_ltx2_5_t2v_API.json); the pinned official editor workflows are fetched during the image build and seeded into ComfyUI's user library.
- ComfyUI Manager is forced offline at startup. Bake custom nodes into the image.

## Docker

RunPod images must be built for `linux/amd64`:

```bash
docker build --platform linux/amd64 --target base -t ltx25-worker:dev .
```

Prefer bake targets for release images. Do not bake large model weights into layers; use the persistent model root described in [Network volumes and model paths](network-volumes.md).

Runtime files are copied into the image, so handler and startup changes require a rebuild before container testing.

## API

New integrations use:

```json
{
  "input": {
    "workflow": {},
    "images": []
  }
}
```

- `workflow` is a ComfyUI API-format object.
- `images` is optional; each entry supplies a workflow filename and base64 content.
- Successful responses contain `output.images[]` and/or `output.videos[]`.
- Each artifact includes `filename`, `type`, `data`, and `media_type`.
- The handler polls ComfyUI `/history/{prompt_id}` until completion.
- The old `prompt` + `image_url` input remains compatibility-only.

See [Configuration](configuration.md) for environment variables and [Deployment](deployment.md) for the GPU smoke-test gate.

## Model directory detection

Loader node types determine where referenced files belong:

- `UpscaleModelLoader` -> `upscale_models`
- `VAELoader` -> `vae`
- `UNETLoader`, `UnetLoaderGGUF`, `Hy3DModelLoader` -> `diffusion_models`
- `DualCLIPLoader`, `TripleCLIPLoader` -> `text_encoders`
- `LoraLoader` -> `loras`

Keep workflow filenames identical to the files on the volume. Friendly guesses are not a model resolver.

## Tests and dependencies

Run the focused commands in [Development](development.md#tests). The current CI does not run the entire legacy test suite.

Python dependencies are pinned in `requirements.txt` and installed with `pip` during the image build. Custom nodes share that environment, so dependency overrides must be explicit, pinned, and GPU-tested. Follow PEP 8; no formatter or linter is currently enforced.
