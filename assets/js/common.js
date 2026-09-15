/** Page-local microphone sessions. UI surfaces observe a capture; they never own its lifetime. */
import { createStreamer } from "./stream_capture.js";
const mounts = new WeakMap();
const sessions = new Map();
const listeners = new Set();
const runKey = scope => JSON.stringify([scope.workspaceProjectId, scope.graphId, scope.instanceId, scope.runId]);
const key = scope => JSON.stringify([runKey(scope), scope.nodeId]);
const notify = () => { for (const refresh of listeners) refresh(); };

/** Retain bounded status only after cleanup, never obsolete browser/API resources. */
function prune() {
  for (const [oldKey, record] of sessions) {
    if (sessions.size <= 64) break;
    if (!record.active) sessions.delete(oldKey);
  }
}

/** Start one capture with a frozen node/run scope, even if its modal is later replaced.
 * @param {object} api - Public block surface API, used only for audio and block-domain requests.
 * @param {object} node - Serialized capture node, including its declared command output.
 * @param {Function} readScope - Current page run context, independent of the mounted DOM.
 * @param {object} config - Capture settings sampled by the user gesture.
 */
function start(api, node, readScope, config) {
  const scope = { ...readScope() };
  if (!scope.available || api.isReadOnly?.() || sessions.get(key(scope))?.active) return;
  const record = { active: true, stopping: false, message: "Autorisation du microphone…", streamer: null };
  sessions.set(key(scope), record);
  prune();
  if (typeof api.blockRequest !== "function" || typeof window.CWBlockUi?.createRuntimeAudioStreamsApi !== "function") {
    record.active = false;
    record.message = "Passerelle de capture indisponible. Rechargez la page après la mise à jour du bloc.";
    notify();
    return;
  }
  const sameRun = () => {
    const current = readScope();
    return current.available && runKey(current) === runKey(scope);
  };
  // The public facade owns transport addressing. Freezing its context prevents
  // a late permission response or stop from targeting the newly visible block.
  const audio = window.CWBlockUi.createRuntimeAudioStreamsApi({
    actions: { getRuntimeAudioStreamContext: () => ({ ...scope, available: sameRun() }) },
  }, () => node);
  let watcher;
  const streamer = createStreamer({
    api: {
      runtimeAudioStreams: audio,
      applyAction(action, values) {
        if (!sameRun()) throw new Error("Le Run de cette capture n’est plus actif.");
        // Explicit body bypasses the surface's live modal/composite context;
        // this is a non-mutating action on the original node, not a graph patch.
        return api.blockRequest("ui-action", { method: "POST", body: JSON.stringify({
          kind: `microphone_stream@${node.block_version}`, node: { ...node, id: scope.nodeId },
          project_id: scope.graphId, action, values,
        }) });
      },
    },
    config: () => config,
    onStatus: message => { record.message = message; notify(); },
    onStop: () => {
      window.clearInterval(watcher);
      record.active = false;
      record.stopping = false;
      record.streamer = null;
      prune();
      notify();
    },
    onError: (_error, message) => api.log?.(`[microphone-stream-error] ${message}`),
  });
  record.streamer = streamer;
  watcher = window.setInterval(() => {
    if (!sameRun()) void streamer.dispose();
  }, 250);
  notify();
  void streamer.start();
}

/** Release hardware on page exit, including bfcache; never resume a capture automatically. */
window.addEventListener("pagehide", () => {
  for (const record of sessions.values()) void record.streamer?.dispose();
});

/** Bind one card/modal to the shared capture and detach only its UI listeners.
 * @param {HTMLElement} root - Owning block UI surface.
 * @param {object} api - Injected public facade.
 * @param {object} context - Block render context with an immutable capture-node snapshot.
 * @param {Function} config - Read current settings only when starting a new capture.
 * @returns {Function} Idempotent UI-only cleanup, deliberately not an audio stop.
 */
export function mountControls(root, api, context, config) {
  mounts.get(root)?.();
  // UI node objects need not expose the persisted block_version field. The
  // package-owned snapshot is authoritative and remains valid after navigation.
  const node = { ...api.getNode?.(), ...context.capture_node };
  if (!node.id || !node.block_version) return;
  const provider = api.actions?.getRuntimeAudioStreamContext;
  const readScope = () => provider ? provider(node) : (api.runtimeAudioStreams?.getContext?.() || {});
  const toggle = root.querySelector("[data-microphone-stream-toggle]");
  const startButton = root.querySelector("[data-microphone-stream-start]");
  const stopButton = root.querySelector("[data-microphone-stream-stop]");
  const status = root.querySelector("[data-microphone-stream-status]");
  let disposed = false;
  const record = () => sessions.get(key(readScope()));
  /** Hydrate controls from the capture, including after composite navigation or modal reopening. */
  const refresh = () => {
    if (disposed) return;
    const current = record();
    const active = Boolean(current?.active);
    const stopping = Boolean(current?.stopping);
    const unavailable = !readScope().available || Boolean(api.isReadOnly?.());
    const message = current?.message || (unavailable ? "Lancez Run en Active Runtime pour activer le micro." : "Prêt à capturer le microphone.");
    if (status && status.textContent !== message) { status.textContent = message; status.title = message; }
    if (toggle) {
      const label = stopping ? "Arrêt…" : active ? "Arrêter" : "Démarrer";
      if (toggle.textContent !== label) toggle.textContent = label;
      toggle.classList.toggle("is-streaming", active);
      toggle.setAttribute("aria-pressed", String(active));
      toggle.disabled = stopping || (unavailable && !active);
      toggle.title = unavailable ? message : active ? "Arrêter la capture du microphone" : "Démarrer la capture du microphone";
    }
    if (startButton) { startButton.disabled = active || unavailable; startButton.title = unavailable ? message : ""; }
    if (stopButton) stopButton.disabled = !active || stopping;
  };
  const begin = () => start(api, node, readScope, config());
  const stop = () => {
    const current = record();
    if (!current?.streamer || current.stopping) return;
    current.stopping = true;
    current.message = "Finalisation de la capture…";
    notify();
    void current.streamer.stop();
  };
  const toggleCapture = event => { event.stopPropagation(); if (record()?.active) stop(); else begin(); };
  toggle?.addEventListener("click", toggleCapture);
  startButton?.addEventListener("click", begin);
  stopButton?.addEventListener("click", stop);
  listeners.add(refresh);
  root.microphoneRefresh = refresh;
  refresh();
  // Only the UI subscription follows DOM removal. Run polling reads existing
  // local scope, makes no HTTP requests and does not follow composite visibility.
  const observer = new MutationObserver(() => { if (!root.isConnected) cleanup(); });
  observer.observe(document.body, { childList: true, subtree: true });
  const timer = window.setInterval(refresh, 250);
  function cleanup() {
    if (disposed) return;
    disposed = true;
    mounts.delete(root);
    observer.disconnect();
    window.clearInterval(timer);
    listeners.delete(refresh);
    delete root.microphoneRefresh;
    toggle?.removeEventListener("click", toggleCapture);
    startButton?.removeEventListener("click", begin);
    stopButton?.removeEventListener("click", stop);
  }
  mounts.set(root, cleanup);
  return cleanup;
}
