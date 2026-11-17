# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=ubuntu:24.04

FROM ${BASE_IMAGE} AS base

ARG COMFYUI_VERSION=v0.33.1
ARG COMFY_CLI_VERSION=1.16.0
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130
ARG PYTORCH_PACKAGES="torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0"
ARG EXTRA_PYTHON_PACKAGES=""
ARG EXTRA_PYTHON_INDEX_URL=""
ARG INSTALL_LTX_VIDEO_NODES=true
ARG LTX_VIDEO_REF=ac4d99839020b983e956a8ab67ec38aec1b6e65a
ARG INSTALL_COMFYUI_DOWNLOADER=true
ARG COMFYUI_DOWNLOADER_REF=03146df738191004a8aad8264dca5c3530907f56
ARG LTX25_PRELOAD_VARIANT=""
ARG LTX25_PRELOAD_PROMPT_ENHANCER=true

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1 \
    CMAKE_BUILD_PARALLEL_LEVEL=8 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:${PATH}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        libjemalloc2 \
        openssh-server \
        python3.12 \
        python3.12-venv \
        redis-server \
        wget \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && python -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install ${PYTORCH_PACKAGES} --index-url "${PYTORCH_INDEX_URL}"

RUN git clone --depth=1 --branch "${COMFYUI_VERSION}" \
        https://github.com/Comfy-Org/ComfyUI.git /comfyui \
    && rm -rf /comfyui/.git

COPY requirements.txt /requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install \
        "comfy-cli==${COMFY_CLI_VERSION}" \
        -r /comfyui/requirements.txt \
        -r /comfyui/manager_requirements.txt \
        -r /requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    if [ "${INSTALL_COMFYUI_DOWNLOADER}" = "true" ]; then \
        git clone --filter=blob:none --no-checkout https://github.com/romandev-codex/ComfyUI-Downloader.git \
            /comfyui/custom_nodes/ComfyUI-Downloader \
        && git -C /comfyui/custom_nodes/ComfyUI-Downloader checkout "${COMFYUI_DOWNLOADER_REF}" \
        && python -m pip install -r /comfyui/custom_nodes/ComfyUI-Downloader/requirements.txt \
        && rm -rf /comfyui/custom_nodes/ComfyUI-Downloader/.git; \
    fi \
    && if [ "${INSTALL_LTX_VIDEO_NODES}" = "true" ]; then \
        git clone --filter=blob:none --no-checkout https://github.com/Lightricks/ComfyUI-LTXVideo.git \
            /comfyui/custom_nodes/ComfyUI-LTXVideo \
        && git -C /comfyui/custom_nodes/ComfyUI-LTXVideo checkout "${LTX_VIDEO_REF}" \
        && python -m pip install -r /comfyui/custom_nodes/ComfyUI-LTXVideo/requirements.txt \
        && rm -rf /comfyui/custom_nodes/ComfyUI-LTXVideo/.git; \
    fi \
    && if [ -n "${EXTRA_PYTHON_PACKAGES}" ]; then \
        if [ -n "${EXTRA_PYTHON_INDEX_URL}" ]; then \
            python -m pip install --index-url "${EXTRA_PYTHON_INDEX_URL}" ${EXTRA_PYTHON_PACKAGES}; \
        else \
            python -m pip install ${EXTRA_PYTHON_PACKAGES}; \
        fi; \
    fi \
    && find /opt/venv -type d -name __pycache__ -prune -exec rm -rf '{}' + \
    && find /opt/venv -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

ARG COMFY_WORKFLOW_TEMPLATES_REF=c74863f87d56b57265cfce844ab00b127c32e137
ARG LTX25_EDITOR_WORKFLOW_SHA256=bcd3239835e8e5bf287a664954c253c67cd31147a4a4193ef5975525e246a7a0
ARG LTX25_T2V_EDITOR_WORKFLOW_SHA256=b8ab11a3cb349bf6dccd9ad09307213e0088d833d1867270d23e1f794bab6a9d
RUN wget -nv \
        -O /video_ltx2_5_i2v.json \
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/${COMFY_WORKFLOW_TEMPLATES_REF}/templates/video_ltx2_5_i2v.json" \
    && wget -nv \
        -O /video_ltx2_5_t2v.json \
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/${COMFY_WORKFLOW_TEMPLATES_REF}/templates/video_ltx2_5_t2v.json" \
    && printf '%s  %s\n%s  %s\n' \
        "${LTX25_EDITOR_WORKFLOW_SHA256}" \
        /video_ltx2_5_i2v.json \
        "${LTX25_T2V_EDITOR_WORKFLOW_SHA256}" \
        /video_ltx2_5_t2v.json \
        | sha256sum -c -

WORKDIR /comfyui
COPY src/extra_model_paths.yaml ./extra_model_paths.yaml

WORKDIR /
COPY src/start.sh src/bootstrap_workspace.sh src/bootstrap_ltx25.sh src/verify_comfy_models.py src/network_volume.py handler.py workflow_support.py frontend_app.py ltx_payload_builder.py video_ltx2_5_i2v_API.json video_ltx2_5_t2v_API.json test_input.json ./
COPY frontend /frontend
COPY scripts/comfy-node-install.sh /usr/local/bin/comfy-node-install
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN chmod +x \
        /start.sh \
        /bootstrap_workspace.sh \
        /bootstrap_ltx25.sh \
        /usr/local/bin/comfy-node-install \
        /usr/local/bin/comfy-manager-set-mode

ENV LTX25_PRELOAD_VARIANT=${LTX25_PRELOAD_VARIANT} \
    LTX25_PRELOAD_PROMPT_ENHANCER=${LTX25_PRELOAD_PROMPT_ENHANCER}

CMD ["/start.sh"]
