/**
 * Role: Mounts Microphone Stream controls on the canvas node card.
 * File Name: node_card.js
 * Author: OpenAI Codex
 * Created Date: 2026-09-04
 */


import { mountControls } from "./common.js";

/** Prevent canvas gestures from consuming the microphone control click. */
function isolateControl(event) {
  event.stopPropagation();
}

/** Preserve capture resources across runtime start/stop output refreshes. */
export function update(root, api) {
  const node = api.getNode?.() || {};
  const title = root.querySelector("h3");
  if (title) title.textContent = node.title || "Microphone Stream";
  const config = node.config || {};
  for (const [key, field] of Object.entries({
    timesliceMs: "timeslice_ms", audioBitsPerSecond: "audio_bits_per_second",
    channelCount: "channel_count", maxDurationSec: "max_duration_sec",
    continuousCapture: "continuous_capture",
  })) {
    if (config[field] != null) root.dataset[key] = String(config[field]);
  }
  root.microphoneRefresh?.();
}
/**
 * Bind the compact toggle to a block-owned streaming controller.
 *
 * @param {HTMLElement} root - Mounted node-card body.
 * @param {object} api - Generic block UI API.
 * @param {object} context - Block-owned capture node snapshot.
 * @returns {Function|undefined} UI-only cleanup; capture remains owned by the Run.
 */
export function mount(root, api, context = {}) {
  const button = root.querySelector("[data-microphone-stream-toggle]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  const detach = mountControls(root, api, context, () => ({
    timesliceMs: Number(root.dataset.timesliceMs) || 250,
    audioBitsPerSecond: Number(root.dataset.audioBitsPerSecond) || 128000,
    channelCount: Number(root.dataset.channelCount) || 1,
    maxDurationSec: Number(root.dataset.maxDurationSec) || 3600,
    continuousCapture: root.dataset.continuousCapture === "true",
  }));
  for (const eventName of ["pointerdown", "pointerup", "mousedown", "mouseup", "dblclick", "contextmenu"]) {
    button.addEventListener(eventName, isolateControl);
  }
  return () => {
    detach?.();
    for (const eventName of ["pointerdown", "pointerup", "mousedown", "mouseup", "dblclick", "contextmenu"]) {
      button.removeEventListener(eventName, isolateControl);
    }
  };
}
