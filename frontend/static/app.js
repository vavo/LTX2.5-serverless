const state = {
  aspectRatio: "16:9",
  fps: 24,
  file: null,
  filePreviewUrl: "",
  payload: null,
  payloadJson: "",
  runMode: "worker",
  submissionMode: "endpoint",
  mode: "i2v",
  textToVideoEnabled: false,
};

const heroCopyByMode = {
  i2v: "Generate the JSON body for the LTX image-to-video API without doing frame math by hand like a prisoner.",
  t2v: "Generate the JSON body for the LTX text-to-video API. No image required, still no frame math by hand.",
};

const ratioDimensions = {
  "16:9": { width: 1280, height: 720 },
  "9:16": { width: 720, height: 1280 },
  "1:1": { width: 1024, height: 1024 },
};

const form = document.querySelector("#payload-form");
const promptField = document.querySelector("#prompt");
const optimizeField = document.querySelector("#optimize-prompt");
const secondsField = document.querySelector("#seconds");
const secondsNumberField = document.querySelector("#seconds-number");
const secondsMinLabel = document.querySelector("#seconds-min-label");
const secondsMaxLabel = document.querySelector("#seconds-max-label");
const framesValue = document.querySelector("#frames-value");
const fpsValue = document.querySelector("#fps-value");
const resolutionValue = document.querySelector("#resolution-value");
const charCount = document.querySelector("#char-count");
const feedback = document.querySelector("#feedback");
const uploadPanel = document.querySelector("#upload-panel");
const uploadIcon = document.querySelector("#upload-icon");
const uploadTitle = document.querySelector("#upload-title");
const uploadCopy = document.querySelector("#upload-copy");
const sourceImageInput = document.querySelector("#source-image");
const filePill = document.querySelector("#file-pill");
const fileName = document.querySelector("#file-name");
const imagePreviewCard = document.querySelector("#image-preview-card");
const imagePreview = document.querySelector("#image-preview");
const previewName = document.querySelector("#preview-name");
const payloadPanel = document.querySelector("#payload-panel");
const payloadOutput = document.querySelector("#payload-output");
const payloadSummary = document.querySelector("#payload-summary");
const generateButton = document.querySelector("#generate-button");
const submitButton = document.querySelector("#submit-button");
const copyButton = document.querySelector("#copy-button");
const downloadButton = document.querySelector("#download-button");
const endpointPanel = document.querySelector("#endpoint-panel");
const endpointUrlField = document.querySelector("#endpoint-url");
const authTokenField = document.querySelector("#auth-token");
const submitModeTitle = document.querySelector("#submit-mode-title");
const submitModeCopy = document.querySelector("#submit-mode-copy");
const helperCopy = document.querySelector("#helper-copy");
const responsePanel = document.querySelector("#response-panel");
const responseSummary = document.querySelector("#response-summary");
const responseStatus = document.querySelector("#response-status");
const responseMedia = document.querySelector("#response-media");
const responseOutput = document.querySelector("#response-output");
const aspectButtons = document.querySelectorAll("[data-aspect-ratio]");
const modePills = document.querySelectorAll("[data-mode]");
const modeT2v = document.querySelector("#mode-t2v");
const secondaryGrid = document.querySelector(".secondary-grid");
const heroCopy = document.querySelector("#hero-copy");
const POD_STATUS_POLL_INTERVAL_MS = 2000;
const POD_STATUS_POLL_TIMEOUT_MS = 15 * 60 * 1000;

function secondsToFrames(seconds) {
  return Math.round(seconds * state.fps) + 1;
}

function setFeedback(message, type = "") {
  feedback.textContent = message;
  feedback.className = type ? `feedback ${type}` : "feedback";
}

function updatePromptCounter() {
  charCount.textContent = String(promptField.value.length);
}

function formatSecondsLabel(seconds) {
  return `${Number(seconds).toString()}s`;
}

function updateDurationSummary() {
  const seconds = Number.parseFloat(secondsField.value);
  const dimensions = ratioDimensions[state.aspectRatio];
  secondsNumberField.value = String(seconds);
  framesValue.textContent = String(secondsToFrames(seconds));
  fpsValue.textContent = String(state.fps);
  resolutionValue.textContent = `${dimensions.width} × ${dimensions.height}`;
}

function updateDurationFromNumber() {
  const seconds = Number.parseFloat(secondsNumberField.value);
  if (!Number.isFinite(seconds)) {
    return;
  }

  const minimum = Number.parseFloat(secondsField.min);
  const maximum = Number.parseFloat(secondsField.max);
  secondsField.value = String(Math.min(maximum, Math.max(minimum, Math.round(seconds))));
  updateDurationSummary();
}

function updateAspectButtons() {
  aspectButtons.forEach((button) => {
    const isActive = button.dataset.aspectRatio === state.aspectRatio;
    button.classList.toggle("aspect-button-active", isActive);
    button.setAttribute("aria-checked", String(isActive));
  });
}

function updateModeUi() {
  modePills.forEach((pill) => {
    const isActive = pill.dataset.mode === state.mode;
    pill.classList.toggle("mode-pill-active", isActive);
    pill.setAttribute("aria-checked", String(isActive));
  });

  if (state.mode === "t2v") {
    uploadPanel.hidden = true;
    secondaryGrid.classList.add("secondary-grid-single");
  } else {
    uploadPanel.hidden = false;
    secondaryGrid.classList.remove("secondary-grid-single");
  }

  heroCopy.textContent = heroCopyByMode[state.mode];
}

function setMode(mode) {
  if (mode === state.mode) {
    return;
  }

  state.mode = mode;
  if (mode === "t2v") {
    setFile(null);
  }
  updateModeUi();
  setFeedback("");
}

function updateSubmitModeUi() {
  if (state.submissionMode === "pod") {
    endpointPanel.hidden = true;
    endpointUrlField.disabled = true;
    authTokenField.disabled = true;
    generateButton.hidden = true;
    generateButton.disabled = true;
    generateButton.type = "button";
    payloadPanel.hidden = true;
    helperCopy.hidden = true;
    submitButton.textContent = "Generate Video";
    submitButton.classList.remove("secondary-hero-action");
    submitButton.classList.add("primary-action");
    submitModeTitle.textContent = "Local pod";
    submitModeCopy.textContent =
      "The frontend queues the workflow into the local ComfyUI pod and then polls for completion.";
    return;
  }

  endpointPanel.hidden = false;
  endpointUrlField.disabled = false;
  authTokenField.disabled = false;
  generateButton.hidden = false;
  generateButton.disabled = false;
  generateButton.type = "submit";
  helperCopy.hidden = false;
  submitModeTitle.textContent = "Real endpoint";
  submitModeCopy.innerHTML =
    'Full POST URL, for example <code>https://api.runpod.ai/v2/&lt;endpoint_id&gt;/runsync</code>';
  submitButton.textContent = "Submit to Endpoint";
  submitButton.classList.remove("primary-action");
  submitButton.classList.add("secondary-hero-action");
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setFile(file) {
  if (state.filePreviewUrl) {
    URL.revokeObjectURL(state.filePreviewUrl);
    state.filePreviewUrl = "";
  }

  state.file = file;
  if (!file) {
    sourceImageInput.value = "";
    filePill.hidden = true;
    fileName.textContent = "";
    imagePreviewCard.hidden = true;
    imagePreview.removeAttribute("src");
    previewName.textContent = "";
    uploadIcon.hidden = false;
    uploadTitle.hidden = false;
    uploadCopy.hidden = false;
    uploadPanel.classList.remove("has-preview");
    return;
  }

  fileName.textContent = file.name;
  state.filePreviewUrl = URL.createObjectURL(file);
  imagePreview.src = state.filePreviewUrl;
  previewName.textContent = file.name;
  imagePreviewCard.hidden = false;
  uploadIcon.hidden = true;
  uploadTitle.hidden = true;
  uploadCopy.hidden = true;
  uploadPanel.classList.add("has-preview");
  filePill.hidden = true;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Failed to read the source image."));
    reader.readAsDataURL(file);
  });
}

function renderSummary(summary) {
  const chips = [];
  if (summary.mode === "t2v" || summary.mode === "i2v") {
    chips.push(summary.mode === "t2v" ? "text to video" : "image to video");
  }
  chips.push(
    `${summary.frames} frames`,
    `${summary.seconds.toFixed(1)}s`,
    `${summary.width} × ${summary.height}`,
    `${summary.aspect_ratio}`,
    `${summary.fps} fps`,
    summary.optimize_prompt ? "AI prompt on" : "AI prompt off",
  );

  payloadSummary.innerHTML = "";
  chips.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "payload-summary-chip";
    chip.textContent = text;
    payloadSummary.appendChild(chip);
  });
}

async function copyPayload() {
  if (!state.payloadJson) {
    return;
  }

  await navigator.clipboard.writeText(state.payloadJson);
  setFeedback("Payload copied.", "success");
}

function downloadPayload() {
  if (!state.payloadJson) {
    return;
  }

  const blob = new Blob([state.payloadJson], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "ltx-payload.json";
  link.click();
  URL.revokeObjectURL(url);
}

function renderResponse(result) {
  const chips = [
    result.ok ? `HTTP ${result.status_code} ok` : `HTTP ${result.status_code}`,
    result.content_type,
    result.endpoint_url || "local ComfyUI",
  ];

  responseSummary.innerHTML = "";
  chips.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "payload-summary-chip";
    chip.textContent = text;
    responseSummary.appendChild(chip);
  });

  if (state.submissionMode === "pod") {
    renderResponseMedia(result.response_json?.output || null);
    renderPodStatus(result.response_json || {});
    responseOutput.hidden = true;
    responseOutput.textContent = "";
  } else {
    responseStatus.hidden = true;
    responseStatus.textContent = "";
    renderResponseMedia(null);
    responseOutput.hidden = false;
    responseOutput.textContent = result.response_json
      ? JSON.stringify(result.response_json, null, 2)
      : (result.response_text || "");
  }

  responsePanel.hidden = false;
}

function renderPodStatus(responseJson) {
  const status = responseJson?.status || "";
  const hasOutput = Boolean(responseJson?.output);
  const renderTimeSec = responseJson?.metadata?.render_time_sec;

  if (hasOutput) {
    responseStatus.textContent =
      typeof renderTimeSec === "number"
        ? `Generation time: ${renderTimeSec.toFixed(2)}s`
        : "Generation complete.";
    responseStatus.hidden = false;
    return;
  }

  const statusCopy = {
    queued: "Workflow queued locally.",
    running: "ComfyUI is rendering the video.",
    completed: "Render completed.",
  };

  responseStatus.textContent = statusCopy[status] || "Working on your video.";
  responseStatus.hidden = false;
}

function renderResponseMedia(output) {
  responseMedia.innerHTML = "";

  if (!output || (!output.videos?.length && !output.images?.length)) {
    responseMedia.hidden = true;
    return;
  }

  const renderArtifactCard = (artifact, mediaKind) => {
    const isVideo = (artifact.media_type || "").startsWith("video/") || mediaKind === "video";
    const card = document.createElement("article");
    card.className = "response-media-card";

    const header = document.createElement("div");
    header.className = "response-media-header";

    const title = document.createElement("p");
    title.className = "response-media-title";
    title.textContent = artifact.filename;

    const saveLink = document.createElement("a");
    saveLink.className = "response-media-link";
    saveLink.href = artifact.download_url || artifact.url;
    saveLink.textContent = isVideo ? "Save video" : "Save image";
    saveLink.setAttribute("download", artifact.filename);

    header.appendChild(title);
    header.appendChild(saveLink);
    card.appendChild(header);

    if (isVideo) {
      const video = document.createElement("video");
      video.className = "response-video";
      video.src = artifact.url;
      video.controls = true;
      video.preload = "metadata";
      card.appendChild(video);
    } else {
      const image = document.createElement("img");
      image.className = "response-image";
      image.src = artifact.url;
      image.alt = artifact.filename;
      card.appendChild(image);
    }

    return card;
  };

  output.videos?.forEach((artifact) => {
    responseMedia.appendChild(renderArtifactCard(artifact, "video"));
  });

  output.images?.forEach((artifact) => {
    responseMedia.appendChild(renderArtifactCard(artifact, "image"));
  });

  responseMedia.hidden = false;
}

async function pollPodSubmitStatus(promptId, nodeUsed) {
  const deadline = Date.now() + POD_STATUS_POLL_TIMEOUT_MS;
  const statusUrl = new URL(`/api/pod-submit/${encodeURIComponent(promptId)}`, window.location.origin);
  if (nodeUsed) {
    statusUrl.searchParams.set("node", nodeUsed);
  }

  while (Date.now() < deadline) {
    const response = await fetch(statusUrl);
    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.detail || "Failed to poll local pod status.");
    }

    renderResponse(result);

    if (result.response_json?.status === "completed") {
      return result;
    }

    await sleep(POD_STATUS_POLL_INTERVAL_MS);
  }

  throw new Error("Local pod run timed out while waiting for ComfyUI history.");
}

async function initializeConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error("Failed to load app configuration.");
  }

  const config = await response.json();
  state.fps = config.fps;
  state.runMode = config.run_mode || "worker";
  state.submissionMode = config.submission_mode || "endpoint";
  state.textToVideoEnabled = Boolean(config.text_to_video_enabled);
  modeT2v.disabled = !state.textToVideoEnabled;
  secondsField.min = String(config.seconds.min);
  secondsField.max = String(config.seconds.max);
  secondsField.step = String(config.seconds.step);
  secondsField.value = String(config.seconds.default);
  secondsNumberField.min = String(config.seconds.min);
  secondsNumberField.max = String(config.seconds.max);
  secondsNumberField.step = String(config.seconds.step);
  secondsNumberField.value = String(config.seconds.default);
  secondsMinLabel.textContent = formatSecondsLabel(config.seconds.min);
  secondsMaxLabel.textContent = formatSecondsLabel(config.seconds.max);
  updateSubmitModeUi();
  updateDurationSummary();
  updateModeUi();
}

async function buildPayload({
  scroll = true,
  showPreview = true,
  successMessage = "Payload ready.",
} = {}) {
  if (!promptField.value.trim()) {
    setFeedback("Prompt is required.", "error");
    promptField.focus();
    return null;
  }

  if (state.mode === "i2v" && !state.file) {
    setFeedback("Source image is required for the current workflow.", "error");
    return null;
  }

  const body = {
    prompt: promptField.value,
    seconds: Number.parseFloat(secondsField.value),
    aspect_ratio: state.aspectRatio,
    optimize_prompt: optimizeField.checked,
    mode: state.mode,
  };

  if (state.mode === "i2v") {
    body.image_name = state.file.name;
    body.image_data_url = await readFileAsDataUrl(state.file);
  }

  const response = await fetch("/api/payload", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Failed to generate payload.");
  }

  state.payload = data.payload;
  state.payloadJson = JSON.stringify(data.payload, null, 2);
  payloadOutput.textContent = state.payloadJson;
  renderSummary(data.summary);
  payloadPanel.hidden = !showPreview;
  if (scroll && showPreview) {
    payloadPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (successMessage) {
    setFeedback(successMessage, "success");
  }
  return data.payload;
}

aspectButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.aspectRatio = button.dataset.aspectRatio;
    updateAspectButtons();
    updateDurationSummary();
  });
});

modePills.forEach((pill) => {
  pill.addEventListener("click", () => {
    setMode(pill.dataset.mode);
  });
});

promptField.addEventListener("input", updatePromptCounter);
secondsField.addEventListener("input", updateDurationSummary);
secondsNumberField.addEventListener("input", updateDurationFromNumber);
copyButton.addEventListener("click", copyPayload);
downloadButton.addEventListener("click", downloadPayload);

uploadPanel.addEventListener("click", () => sourceImageInput.click());
uploadPanel.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    sourceImageInput.click();
  }
});

sourceImageInput.addEventListener("change", (event) => {
  const [file] = event.target.files;
  setFile(file || null);
});

["dragenter", "dragover"].forEach((eventName) => {
  uploadPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadPanel.classList.add("drag-active");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  uploadPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadPanel.classList.remove("drag-active");
  });
});

uploadPanel.addEventListener("drop", (event) => {
  const [file] = event.dataTransfer.files;
  if (file && file.type.startsWith("image/")) {
    setFile(file);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.submissionMode === "pod") {
    submitButton.click();
    return;
  }

  setFeedback("");

  generateButton.disabled = true;
  generateButton.textContent = "Generating...";

  try {
    await buildPayload();
  } catch (error) {
    setFeedback(error.message, "error");
  } finally {
    generateButton.disabled = false;
    generateButton.textContent = "Generate Payload";
  }
});

submitButton.addEventListener("click", async () => {
  setFeedback("");

  let endpointUrl = "";
  if (state.submissionMode === "endpoint") {
    endpointUrl = endpointUrlField.value.trim();
  }

  if (state.submissionMode === "endpoint" && !endpointUrl) {
    setFeedback("Endpoint URL is required before submit.", "error");
    endpointUrlField.focus();
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = state.submissionMode === "pod" ? "Running..." : "Submitting...";

  try {
    const payload = await buildPayload({
      scroll: false,
      showPreview: state.submissionMode !== "pod",
      successMessage:
        state.submissionMode === "pod"
          ? ""
          : "Payload refreshed for submit.",
    });
    if (!payload) {
      return;
    }

    const response = await fetch(
      state.submissionMode === "pod" ? "/api/pod-submit" : "/api/submit",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          payload,
          ...(state.submissionMode === "endpoint"
            ? {
                endpoint_url: endpointUrl,
                auth_token: authTokenField.value,
              }
            : {}),
        }),
      }
    );

    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "Submit failed.");
    }

    renderResponse(result);
    responsePanel.scrollIntoView({ behavior: "smooth", block: "start" });

    if (state.submissionMode === "pod") {
      const promptId = result.response_json?.prompt_id || "";
      const nodeUsed = result.response_json?.node_used || "";
      if (!promptId) {
        throw new Error("Local pod submit did not return a prompt_id.");
      }

      setFeedback("Workflow queued locally. Waiting for ComfyUI to finish.", "success");
      submitButton.textContent = "Waiting for ComfyUI...";
      const completedResult = await pollPodSubmitStatus(promptId, nodeUsed);
      setFeedback(
        completedResult.ok
          ? "Local pod run finished successfully."
          : `Local pod run returned HTTP ${completedResult.status_code}.`,
        completedResult.ok ? "success" : "error"
      );
      return;
    }

    setFeedback(
      result.ok
        ? `Submitted successfully. Endpoint returned HTTP ${result.status_code}.`
        : `Submitted. Endpoint returned HTTP ${result.status_code}.`,
      result.ok ? "success" : "error"
    );
  } catch (error) {
    setFeedback(error.message, "error");
  } finally {
    submitButton.disabled = false;
    updateSubmitModeUi();
  }
});

updatePromptCounter();
updateAspectButtons();
modeT2v.disabled = !state.textToVideoEnabled;
updateModeUi();
setFile(null);
initializeConfig().catch((error) => {
  setFeedback(error.message, "error");
});
