#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Role: Verifies the autonomous browser microphone streaming block.
# File Name: F5.44_microphone_stream_block.py
# Author: OpenAI Codex
# Created Date: 2026-09-04
# -----------------------------------------------------------------------------

"""F5.44 - Microphone Stream contract, UI, and dual-runtime coverage."""

# Test cases:
# - FB1/FB2/FB3 - Verify block-owned MediaRecorder capture publishes bounded chunks through the generic audio facade.
# - FB4 - Enforce separate fixed audio_out and command_out ports.
# - FB5 - Verify centralized no-op and active readiness/keep-alive behavior.
# - FB6 - Render and serve the block-owned card, modal, inspector, CSS, and JavaScript.
# - FB7 - Publish validated start/stop metadata through explicit runtime output actions.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen
import sys
import json
import subprocess


ROOT_DIR = Path(__file__).resolve().parents[3]
TESTS_DIR = ROOT_DIR / "tests"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from blocs.microphone_stream.block import MicrophoneStreamBlock  # noqa: E402
from bloxsmith_app.block_runtime import BlockRuntimeContext, BlockRuntimePreparation  # noqa: E402
from bloxsmith_app.runtime_audio_streams import (  # noqa: E402
    RuntimeAudioStreamBinding,
    RuntimeAudioStreamPortRoute,
    RuntimeAudioStreamService,
)
from ui_smoke_common import (  # noqa: E402
    create_run_api,
    expect,
    graph_payload,
    http_json,
    isolated_server,
    play_run_api,
    prepare_run_api,
    stop_run_api,
    wait_for_run_predicate,
    wait_for_run_terminal,
)
from block_test_packages import install_test_package, release_key, surface_payload


def audio_output() -> SimpleNamespace:
    """Build the fixed output shape exposed to direct runtime contexts."""

    return SimpleNamespace(
        id=1,
        name="audio_out",
        transport="audio_stream",
        audio_stream=SimpleNamespace(codecs=("opus",), sample_rates_hz=(48000,), channels=(1, 2)),
    )


def microphone_context(*, runtime_mode: str, services: dict | None = None) -> BlockRuntimeContext:
    """Build one direct context using the canonical fixed microphone port."""

    return BlockRuntimeContext(
        run_id="run-microphone-unit",
        node_id="microphone-unit",
        kind="microphone_stream",
        title="Microphone",
        config={
            "timeslice_ms": 250,
            "audio_bits_per_second": 128_000,
            "channel_count": 1,
            "max_duration_sec": 60,
        },
        input_ports=(),
        output_ports=(audio_output(), SimpleNamespace(id=2, name="command_out", transport="message")),
        runtime_mode=runtime_mode,
        services=services,
        root_dir=ROOT_DIR,
    )


def microphone_node(node_id: str = "microphone-1") -> dict:
    """Return one graph node directly from the autonomous block manifest."""

    return MicrophoneStreamBlock().build_node_payload(
        node_id=node_id,
        title="Microphone Stream",
        position={"x": 80, "y": 120},
        config_overrides={"max_duration_sec": 60},
    )


def save_audio_node(node_id: str = "save-audio-1") -> dict:
    """Return a minimal compatible audio sink for graph-mode tests."""

    from blocs.save_audio.block import SaveAudioBlock

    return SaveAudioBlock().build_node_payload(
        node_id=node_id,
        title="Save Audio",
        position={"x": 420, "y": 120},
        config_overrides={"output_dir": "exports/microphone-test", "idle_finalize_sec": 0.25},
    )


def audio_document() -> dict:
    """Return the smallest graph containing one compatible audio stream edge."""

    return graph_payload(
        "F5.44 Microphone Stream",
        [microphone_node(), save_audio_node()],
        [
            {
                "id": "edge-microphone-save",
                "from": {"node": "microphone-1", "port": 1},
                "to": {"node": "save-audio-1", "port": 1},
                "kind": "data",
            }
        ],
    )


def test_model_preparation_and_direct_execution() -> None:
    """Validate FB4/FB5 fixed ports, preparation, config bounds, and readiness."""

    block = MicrophoneStreamBlock()
    model_ports = block.model["ports"]
    expect(model_ports["inputs"] == [], "Microphone Stream must declare no input.")
    expect(len(model_ports["outputs"]) == 2, "Microphone Stream must declare separate audio and data outputs.")
    expect(model_ports["outputs"][1]["transport"] == "message", "command_out must be explicit data.")
    output = model_ports["outputs"][0]
    expect(output["name"] == "audio_out", "The single output must be named audio_out.")
    expect(output["transport"] == "audio_stream", "audio_out must use audio_stream transport.")
    expect(output["audio_stream"]["codecs"] == ["opus"], "The microphone must guarantee Opus, never silently emit AAC.")
    expect(output["audio_stream"]["sample_rates_hz"] == [48000], "Opus uses its 48 kHz decoding clock.")
    expect(block.model["runtime"]["supports_runtime_audio_streams"] is True, "Audio runner capability is required.")

    centralized_context = microphone_context(runtime_mode="centralized")
    centralized_preparation = block.prepare_runtime(centralized_context)
    expect(not centralized_preparation.keep_alive, "Centralized simulation must not keep a worker alive.")
    centralized = block.execute_runtime(centralized_context)
    expect(centralized.status == "skipped", "Centralized microphone capture must no-op cleanly.")
    expect(centralized.outputs == [], "Continuous audio must not become a classic graph output.")

    topic = "graph-port/microphone-test"
    route = RuntimeAudioStreamPortRoute(
        port_id=1,
        port_name="audio_out",
        direction="output",
        topic=topic,
        codecs=("opus", "aac"),
        channels=(1, 2),
    )
    service = RuntimeAudioStreamService(run_id="run-microphone-unit")
    service.register_preparations(
        {"microphone-unit": BlockRuntimePreparation()},
        additional_bindings={
            "microphone-unit": (RuntimeAudioStreamBinding(topic=topic, publish=True, codecs=("opus", "aac"), channels=(1, 2)),),
        },
    )
    service.open()
    try:
        client = service.client_for("microphone-unit", port_routes=(route,))
        active_context = microphone_context(
            runtime_mode="zeromq_active",
            services={"runtime_audio_streams": client},
        )
        active_preparation = block.prepare_runtime(active_context)
        expect(active_preparation.keep_alive, "Active browser ingress must remain alive until Stop.")
        active = block.execute_runtime(active_context)
        expect(active.status == "success", "A connected active audio_out must report ready.")
        expect("Microphone gateway ready" in active.last_message, "Readiness must be actionable in the UI.")
    finally:
        service.close()

    invalid = microphone_context(runtime_mode="centralized")
    invalid.output_ports = (SimpleNamespace(id=1, name="audio_out", transport="message"), invalid.output_ports[1])
    failed = block.execute_runtime(invalid)
    expect(failed.status == "failed", "A mutated message output must be rejected.")
    expect("audio_stream" in failed.error, "The port error must name the required transport.")

    patched = block.handle_ui_action(
        node=microphone_node("microphone-config"),
        action="modal_update_fields",
        values={
            "node_patch": {
                "config": {
                    "timeslice_ms": 1,
                    "audio_bits_per_second": 999_999,
                    "channel_count": 9,
                    "max_duration_sec": 0,
                }
            }
        },
    )
    normalized = patched["node_patch"]["config"]
    expect(normalized["timeslice_ms"] == 100, "timeslice_ms must be clamped to 100 ms.")
    expect(normalized["audio_bits_per_second"] == 320_000, "Bitrate must be bounded.")
    expect(normalized["channel_count"] == 2, "Channel count must stay mono or stereo.")
    expect(normalized["max_duration_sec"] == 1, "Capture duration must stay positive.")
    expect(block._config({})["continuous_capture"] is False, "FB9: preserve timed capture for existing blueprints.")
    expect(block._config({"continuous_capture": True})["continuous_capture"] is True, "FB9: explicit continuous mode.")
    for invalid in ("true", 1, None):
        expect(block._config({"continuous_capture": invalid})["continuous_capture"] is False, "FB9: require a real boolean.")

    for action in ("start", "stop"):
        response = block.handle_ui_action(
            node=microphone_node(), action="publish_capture_command",
            values={"action": action, "stream_id": "capture-1", "frame_count": 2, "byte_count": 42},
        )
        publication = response["active_runtime_actions"][0]
        expect(publication["port_id"] == 2 and publication["port_name"] == "command_out", "FB7: publish on data only.")
        expect(json.loads(publication["value"])["action"] == action, "Capture action must survive JSON serialization.")
        expect("node_patch" not in response, "Commands must not mutate graph configuration.")
    invalid_command = block.handle_ui_action(
        node=microphone_node(), action="publish_capture_command",
        values={"action": "stop", "stream_id": "s", "frame_count": -1, "byte_count": 4},
    )
    expect(invalid_command.get("error"), "Negative counters must be rejected.")


def test_owned_ui_and_browser_transport() -> None:
    """Validate FB1/FB2/FB3/FB6 HTML and served JavaScript transport behavior."""

    block = MicrophoneStreamBlock()
    node = microphone_node("microphone-ui")
    card = block.render_node_card(node=node)
    modal = block.render_modal(node=node)
    inspector = block.render_inspector_panel(node=node)
    expect("data-microphone-stream-toggle" in card["html"], "Node card must own a capture toggle.")
    expect("data-microphone-stream-start" in modal["html"], "Modal must own Start capture.")
    expect('data-block-config-field="timeslice_ms"' in inspector["html"], "Inspector must edit chunk duration.")
    expect(modal["html"].count("data-block-apply") == 1, "The modal must have one reachable Apply action, not an extra button in the scrolling fields.")
    expect(modal["context"]["capture_node"]["outputs"] == node["outputs"], "Modal capture must retain the original declared command port.")
    expect(card["context"]["capture_node"]["id"] == node["id"], "Card capture must retain the original node identity.")
    for rendered in (modal, card):
        expect(rendered["context"]["capture_node"]["block_version"] == block.model["version"],
               "Capture identity must come from the owning package, even when the UI node omits block_version.")

    with isolated_server() as server:
        model = install_test_package(server, "microphone_stream")
        node["block_version"] = model["version"]
        rendered = surface_payload(server, model, node)
        assets = rendered.get("assets") or []
        capture_asset = next(asset for asset in assets if asset["path"].endswith("/assets/js/stream_capture.js"))
        with urlopen(
            f'{server.base_url}/api/blocks/{release_key(model)}/assets/{capture_asset["path"]}',
            timeout=5,
        ) as response:
            script = response.read().decode("utf-8")
        expect("getUserMedia" in script and "MediaRecorder" in script, "Capture must remain browser-owned.")
        expect('outputPort: "audio_out"' in script, "Capture must address only the stable output port.")
        expect("runtimeAudioStreams.openOutput" in script, "Capture must use the injected generic facade.")
        expect("sendFrame(event.data)" in script, "Each MediaRecorder chunk must be published as binary.")
        expect("/api/" not in script and "new WebSocket" not in script, "The block must not construct framework transport URLs.")
        expect("saturated" in script and "stopped" in script, "Backpressure must stop capture explicitly.")


def test_graph_modes() -> None:
    """Run FB5 through centralized and loaded/played active graph paths."""

    block = MicrophoneStreamBlock()
    for available in (False, True):
        result = block.execute_runtime(microphone_context(runtime_mode="zeromq_active",
            services={"runtime_audio_streams": SimpleNamespace(available=available)}))
        expect(result.status == ("success" if available else "skipped"),
               "Audio readiness must depend on the public interface, not the concrete client class.")

    with isolated_server() as server:
        model = install_test_package(server, "microphone_stream")
        document = audio_document()
        next(node for node in document["nodes"] if node["id"] == "microphone-1")["block_version"] = model["version"]
        centralized_created = create_run_api(server, document, runtime_mode="centralized")
        centralized = wait_for_run_terminal(server, str(centralized_created["run_id"]), timeout_sec=15)
        expect(centralized.get("status") == "success", "Audio simulation warnings must remain non-blocking.")
        microphone_result = centralized.get("results", {}).get("microphone-1", {})
        expect(
            "only available in Active Runtime" in str(microphone_result.get("last_message") or ""),
            "Centralized microphone node must explain that capture is skipped.",
        )

        prepared = prepare_run_api(server, document, runtime_mode="zeromq_active")
        run_id = str(prepared.get("run_id") or "")
        expect(run_id, f"Active graph must prepare: {prepared}")
        play_run_api(server, run_id)
        running = wait_for_run_predicate(
            server,
            run_id,
            lambda state: state.get("status") == "running"
            and state.get("node_statuses", {}).get("microphone-1") == "success"
            and "Microphone gateway ready" in str(state.get("results", {}).get("microphone-1", {}).get("last_message", "")),
            "Connected Microphone Stream did not report ready after Play.",
            timeout_sec=20,
        )
        expect("Microphone gateway ready" in str(running["results"]["microphone-1"].get("last_message") or ""), "Active readiness result is missing.")
        stop_run_api(server, run_id)



def test_browser_command_lifecycle() -> None:
    """FB1/FB2/FB3/FB7/FB9: deterministic capture, finite/continuous timers, transport and cleanup."""
    script = r"""
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
const timers = new Map();
let timerId = 0;
global.window = {
  setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
  clearTimeout(id) { timers.delete(id); }, addEventListener() {},
};
let deny = false, holdPermission = null, released = 0, started = 0;
const stream = {
  getTracks: () => [{ stop: () => released++ }],
  getAudioTracks: () => [{ getSettings: () => ({ sampleRate: 44100, channelCount: 1 }) }],
};
global.navigator = { mediaDevices: { getUserMedia: async () => {
  if (deny) throw new Error("permission denied");
  if (holdPermission) return new Promise(resolve => { holdPermission.resolve = resolve; });
  return stream;
} } };
class Recorder {
  static last;
  static isTypeSupported() { return true; }
  constructor(_stream, options) { this.options = options; this.state = "inactive"; this.listeners = {}; Recorder.last = this; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, value) { for (const fn of this.listeners[name] || []) fn(value); }
  start() { started++; this.state = "recording"; }
  stop() {
    this.state = "inactive";
    this.emit("dataavailable", { data: new Blob(["final"]) });
    this.emit("stop", {});
  }
}
global.MediaRecorder = Recorder;
const { createStreamer } = await import(pathToFileURL(process.argv[2]));
let commands = [], chunks = [], errors = [], publishers = [], rejectFrames = false, rejectStart = false;
const api = {
  runtimeAudioStreams: { openOutput: async options => {
    assert.equal(options.outputPort, "audio_out");
    assert.equal(options.codec, "opus");
    assert.equal(options.sampleRateHz, 48000, "Do not confuse the device clock with the Opus clock.");
    assert.equal(options.channels, 1);
    const publisher = {
      descriptor: { stream_id: "stream-" + (publishers.length + 1) },
      sendFrame(data) { if (rejectFrames) return false; chunks.push(data); return true; },
      close() { this.closed = true; options.onState({ type: "runtime_audio_stream.closed" }); },
    };
    publishers.push(publisher);
    return publisher;
  } },
  applyAction: async (action, values) => {
    assert.equal(action, "publish_capture_command");
    commands.push(structuredClone(values));
    return { active_runtime_actions_result: { ok: !(rejectStart && values.action === "start") } };
  },
};
const make = () => createStreamer({
  api, config: () => ({}), onError: (_, message) => errors.push(message),
});
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
(async () => {
  const capture = make();
  await capture.start();
  assert.equal(commands[0].action, "start");
  Recorder.last.emit("dataavailable", { data: new Blob(["head"]) });
  capture.stop();
  capture.stop();
  await settle();
  assert.deepEqual(commands[1], {
    action: "stop", stream_id: "stream-1", frame_count: 2, byte_count: 9, aborted: false,
  });
  assert.equal(chunks.length, 2);
  assert.ok(publishers[0].closed && released > 0 && !capture.isActive());
  await capture.start();
  capture.stop();
  await settle();
  assert.equal(commands[3].frame_count, 1, "Counters must reset between sessions.");

  rejectFrames = true;
  await capture.start();
  Recorder.last.emit("dataavailable", { data: new Blob(["lost"]) });
  await settle();
  assert.equal(commands.at(-1).aborted, true);
  assert.equal(commands.at(-1).frame_count, 0);
  assert.ok(errors.some(message => message.includes("saturated")));
  assert.ok(!capture.isActive());
  rejectFrames = false;

  rejectStart = true;
  const before = started;
  await capture.start();
  assert.equal(started, before, "Do not start encoding when start publication fails.");
  assert.equal(commands.at(-1).aborted, true);
  assert.ok(!capture.isActive() && publishers.at(-1).closed);
  rejectStart = false;

  deny = true;
  await capture.start();
  assert.ok(!capture.isActive());
  deny = false;
  holdPermission = {};
  const pending = capture.start();
  capture.stop();
  holdPermission.resolve(stream);
  await pending;
  holdPermission = null;
  assert.ok(!capture.isActive());
  const mounted = make();
  await mounted.start();
  mounted.dispose();
  await settle();
  assert.equal(commands.at(-1).action, "stop");
  assert.ok(!mounted.isActive());

  // FB9: continuous capture has no automatic deadline; explicit Stop still drains.
  assert.equal(timers.size, 0);
  const continuous = createStreamer({ api, config: () => ({ continuousCapture: true, maxDurationSec: 1 }) });
  await continuous.start();
  const liveRecorder = Recorder.last;
  assert.equal(timers.size, 0, "Continuous capture must not schedule a duration stop.");
  assert.equal(liveRecorder.state, "recording");
  Recorder.last.emit("dataavailable", { data: new Blob(["after-one-hour"]) });
  await continuous.stop();
  assert.equal(commands.at(-1).aborted, false);
  assert.equal(commands.at(-1).frame_count, 2);
  assert.ok(!continuous.isActive() && publishers.at(-1).closed);
  for (const value of [false, undefined, "true", 1]) {
    const timed = createStreamer({ api, config: () => ({ continuousCapture: value, maxDurationSec: 2 }) });
    await timed.start();
    assert.equal(timers.size, 1, "Only an explicit boolean true disables the safety timer.");
    const timer = [...timers.values()][0];
    assert.equal(timer.delay, 2000);
    await timer.fn();
    assert.ok(!timed.isActive() && publishers.at(-1).closed);
    assert.equal(commands.at(-1).aborted, false);
  }

  Recorder.isTypeSupported = mime => mime === "audio/ogg;codecs=opus";
  const ogg = createStreamer({ api, config: () => ({ channelCount: 2 }) });
  await ogg.start();
  assert.equal(Recorder.last.options.mimeType, "audio/ogg;codecs=opus");
  await ogg.stop();
  Recorder.isTypeSupported = mime => mime === "audio/mp4" || mime === "audio/webm";
  const publisherCount = publishers.length;
  const unsupported = createStreamer({ api, onError: e => errors.push(e.message) });
  await unsupported.start();
  assert.equal(publishers.length, publisherCount, "Unsupported browsers must fail before opening a stream or publishing start.");
  assert.match(errors.at(-1), /Opus/);

  const { update } = await import(pathToFileURL(process.argv[3]));
  const root = { dataset: {}, querySelector: () => ({ textContent: "" }) };
  update(root, {
    getNode: () => ({ title: "Live", config: { timeslice_ms: 333, continuous_capture: true } }),
  });
  assert.equal(root.dataset.timesliceMs, "333");
  assert.equal(root.dataset.continuousCapture, "true");
  update(root, { getNode: () => ({ config: { continuous_capture: false } }) });
  assert.equal(root.dataset.continuousCapture, "false");
  console.log("[ok] microphone browser start/audio/stop lifecycle");
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    completed = subprocess.run(
        ["node", "--experimental-default-type=module", "-", str(ROOT_DIR / "blocs/microphone_stream/assets/js/stream_capture.js"),
         str(ROOT_DIR / "blocs/microphone_stream/assets/js/node_card.js")],
        input=script, text=True, capture_output=True, timeout=15, check=False,
    )
    expect(completed.returncode == 0, completed.stdout + completed.stderr)

def main() -> None:
    """Run every Microphone Stream block-local scenario."""

    test_model_preparation_and_direct_execution()
    test_owned_ui_and_browser_transport()
    test_graph_modes()
    test_browser_command_lifecycle()
    print("[ok] F5.44_microphone_stream_block")


if __name__ == "__main__":
    main()
