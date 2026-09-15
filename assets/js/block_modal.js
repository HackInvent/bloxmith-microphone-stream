/**
 * Role: Mounts Microphone Stream modal capture controls.
 * File Name: block_modal.js
 * Author: OpenAI Codex
 * Created Date: 2026-09-04
 */


import { mountControls } from "./common.js";

/**
 * Read one numeric generic config input from the mounted modal.
 *
 * @param {HTMLElement} root - Microphone modal root.
 * @param {string} name - Config field name.
 * @param {number} fallback - Value used when the input is absent.
 * @returns {number} Current numeric input value.
 */
function numberField(root, name, fallback) {
  const field = root.querySelector(`[data-block-config-field="${name}"]`);
  return Number(field?.value) || fallback;
}

/**
 * Bind Start/Stop controls to the generic browser audio ingress facade.
 *
 * @param {HTMLElement} root - Mounted block modal.
 * @param {object} api - Generic block UI API.
 * @param {object} context - Block-owned capture node snapshot.
 * @returns {Function} UI-only cleanup; capture remains attached to its Run.
 */
export function mount(root, api, context = {}) {
  return mountControls(root, api, context, () => ({
    timesliceMs: numberField(root, "timeslice_ms", 250),
    audioBitsPerSecond: numberField(root, "audio_bits_per_second", 128000),
    channelCount: numberField(root, "channel_count", 1),
    maxDurationSec: numberField(root, "max_duration_sec", 3600),
  }));
}
