# Customization

Keep custom nodes in the image and large weights on the network volume. That split keeps deployments reproducible without turning each Docker pull into a model transfer wearing a fake moustache.

## Custom nodes

Create a small derived image from a published `base` tag:

```Dockerfile
FROM <registry>/<image>:<version>-base

RUN comfy-node-install <registry-node-name-or-git-url>
```

Build it for RunPod's architecture:

```bash
docker build --platform linux/amd64 -t <registry>/<image>:<tag> .
docker push <registry>/<image>:<tag>
```

The image includes `comfy-node-install`, a wrapper that fails visibly when installation fails. Pin custom-node revisions whenever the installer supports it, and test their dependency set against the pinned PyTorch/ComfyUI stack.

ComfyUI Manager is forced offline at boot, so runtime Manager installation is not a deployment strategy. Bake required nodes into the image.

## Models

Prefer the attached network volume for weights:

```text
/workspace/models/diffusion_models/<transformer>.safetensors
/workspace/models/text_encoders/<encoder>.safetensors
/workspace/models/vae/<vae>.safetensors
/workspace/models/latent_upscale_models/<upscaler>.safetensors
/workspace/models/loras/<lora>.safetensors
```

See [Network volumes and model paths](network-volumes.md) for the full mapping. Do not add LTX weights to a Dockerfile unless an immutable, self-contained image is an explicit requirement; the default images are small precisely because they do not do that.

If a small static asset genuinely belongs in the image, copy it explicitly:

```Dockerfile
FROM <registry>/<image>:<version>-base

COPY input/reference.png /comfyui/input/reference.png
```

## Workflows

To seed additional editor-format workflows into persisted ComfyUI state:

1. Copy the workflow JSON into the derived image.
2. Set `COMFY_BOOTSTRAP_WORKFLOWS` to a comma-separated list of image paths.

```Dockerfile
FROM <registry>/<image>:<version>-base

COPY my_workflow.json /my_workflow.json
ENV COMFY_BOOTSTRAP_WORKFLOWS="video_ltx2_5_i2v.json,video_ltx2_5_t2v.json,my_workflow.json"
```

The handler accepts API-format workflows directly in each request. Do not seed those files into the UI library; ComfyUI's canvas requires editor/save-format JSON with top-level `nodes` and `links`.

## Dependency conflicts

Custom nodes share the container's Python environment. When an import fails, inspect the node's dependency constraints before adding overrides. Put deliberate overrides in the derived Dockerfile, pin them, and rerun both the repository tests and a GPU workflow. Randomly upgrading `torch`, `diffusers`, or `transformers` inside a running pod is not debugging; it is archaeology with billing enabled.
