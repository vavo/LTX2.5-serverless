# Development and local testing

## Prerequisites

- Python 3.12 for parity with the image
- Node.js 24 and pnpm 11 for release tooling
- Docker with Buildx
- An NVIDIA GPU, compatible driver, and NVIDIA Container Toolkit for container runtime tests

macOS can run host-side tests and build `linux/amd64` images through Buildx, but it cannot execute this CUDA worker locally.

## Host-side setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
corepack enable
pnpm install --frozen-lockfile
```

```bash
git clone https://github.com/vavo/LTX2.5-serverless.git
cd LTX2.5-serverless
```

## Tests

Run the same host-safe validation used by CI:

```bash
bash -n src/*.sh scripts/*.sh tests/*.sh
bash tests/test_restore_snapshot.sh
bash tests/test_bootstrap_workspace.sh
bash tests/test_bootstrap_ltx25.sh

python3 -m unittest \
  tests.test_ltx_payload_builder \
  tests.test_ltx25_workflow \
  tests.test_verify_comfy_models \
  tests.test_workflow_support \
  -v

python3 -m json.tool .runpod/hub.json >/dev/null
python3 -m json.tool video_ltx2_5_i2v_API.json >/dev/null
python3 -m json.tool video_ltx2_5_t2v_API.json >/dev/null
docker buildx bake --print ltx2-5-distilled-int8 >/dev/null
docker buildx bake --print ltx2-5-distilled-int8-cu128 >/dev/null
```

`tests/test_handler.py` and `tests/test_frontend_app.py` are legacy integration-oriented modules and are not part of the current CI gate. Do not claim the full `unittest discover` suite passes unless those dependencies and mocks have been repaired.

## Local GPU stack

All runtime source files are copied into the image; there is no source bind mount. Rebuild after changing Python or shell code:

```bash
docker compose down
docker build --platform linux/amd64 --target base -t ltx25-worker:dev .
docker compose up
```

The compose service uses `RUN_MODE=local-api` and exposes:

- RunPod-compatible local API: `http://localhost:8000`
- ComfyUI: `http://localhost:8188`
- Bundled frontend: `http://localhost:7777`

Its persistent data is stored under `./data/runpod-volume`. Set the LTX preload variables in an override file or environment if the local volume does not already contain the model stack.

## GPU release gate

Before publishing a release:

1. Build the exact bake target for `linux/amd64`.
2. Boot it on the intended Blackwell GPU and CUDA-compatible driver.
3. Confirm the preload is reused after restart.
4. Run the checked-in I2V and T2V workflows through the handler.
5. Verify the returned video and S3 mode if enabled.
6. Repeat on the CUDA 12.8 target only if that fallback will be published.

Passing host tests proves the plumbing is coherent. It does not prove a 22B video model fits, starts, or renders on a GPU that was never involved.
