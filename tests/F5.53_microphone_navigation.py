#!/usr/bin/env python3
"""FB1/FB2/FB3/FB6/FB7/FB8: real capture survives composite and modal navigation."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT_DIR), str(ROOT_DIR / "tests")]

from playwright.sync_api import sync_playwright
from blocs.microphone_stream.block import MicrophoneStreamBlock
from blocs.save_audio.block import SaveAudioBlock
from blocs.composite.block import CompositeBlock
from ui_smoke_common import create_project_api, graph_payload, http_json, isolated_server, project_editor_url, wait_for_run_predicate
from block_test_artifacts import artifact_path
from block_test_packages import install_test_package, surface_payload


def pair(prefix: str) -> tuple[list[dict], list[dict]]:
    """Build a real microphone/save pair with separate audio and command edges."""
    nodes = [
        MicrophoneStreamBlock().build_node_payload(node_id=f"{prefix}-mic", position={"x": 80, "y": 130}),
        SaveAudioBlock().build_node_payload(node_id=f"{prefix}-save", position={"x": 430, "y": 130},
                                           config_overrides={"output_dir": "exports/micro-navigation"}),
    ]
    nodes[0]["block_version"] = MicrophoneStreamBlock().model["version"]
    edges = [{"id": f"{prefix}-{port}", "kind": "data", "from": {"node": f"{prefix}-mic", "port": port},
              "to": {"node": f"{prefix}-save", "port": port}} for port in (1, 2)]
    return nodes, edges


def test_navigation(page, server, *, origin="managed") -> None:
    """Exercise real editor, permission, encoder, WS and Save Audio, without paid providers."""
    model = install_test_package(server, "microphone_stream", origin=origin, variant=origin == "linked")
    nodes, edges = pair("root")
    for surface in ("modal", "node_card", "inspector_panel"):
        surface_payload(server, model, nodes[0], surface)
    inner_nodes, inner_edges = pair("inner")
    if origin == "linked":
        inner_nodes[0]["block_version"] = "0.0.0"  # Synthetic second release, never published.
    composite = CompositeBlock().build_node_payload(node_id="composite-test", position={"x": 790, "y": 130})
    composite["inputs"], composite["outputs"] = [], []
    composite["config"]["composite"] = {"nodes": inner_nodes, "edges": inner_edges, "input_mappings": [], "output_mappings": []}
    nodes.append(composite)
    created = create_project_api(server, title="Microphone navigation", document=graph_payload("Microphone navigation", nodes, edges))["project"]
    graph_id = created.get("graph_id") or created["project_id"]
    commands = []
    page.on("request", lambda request: commands.append(request.post_data_json)
            if "/microphone_stream@" in unquote(request.url) and request.url.endswith("/ui-action")
            and request.post_data_json.get("action") == "publish_capture_command" else None)
    page.add_init_script("""(() => {
      window.micTest = {streams: [], recorders: [], frames: 0};
      const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async options => {
        const stream = await getUserMedia(options);
        window.micTest.streams.push(stream);
        if (window.micTest.holdPermission) await new Promise(resolve => { window.micTest.releasePermission = resolve; });
        return stream;
      };
      const NativeRecorder = window.MediaRecorder;
      window.MediaRecorder = class extends NativeRecorder {
        constructor(...args) {
          super(...args); window.micTest.recorders.push(this);
          this.addEventListener('dataavailable', () => window.micTest.frames++);
        }
      };
    })();""")
    page.goto(project_editor_url(server.base_url, graph_id, workspace_project_id=created["workspace_project_id"]))
    page.wait_for_selector('[data-node-id="root-mic"] [data-microphone-stream-toggle]')
    page.click("#activeRuntimeModeButton")
    with page.expect_response(lambda response: response.url.endswith("/runs/prepare") and response.request.method == "POST") as prepared:
        page.click("#loadRunButton")
    run_id = prepared.value.json().get("run_id")
    assert run_id, "The isolated graph must prepare an Active Runtime."

    def card(node_id="root-mic"):
        return page.locator(f'[data-node-id="{node_id}"] [data-microphone-stream-toggle]')

    def recording():
        page.wait_for_function("window.micTest.recorders.at(-1)?.state === 'recording'")

    def enter():
        page.evaluate("enterCompositeGraph('composite-test')")
        page.wait_for_selector('[data-node-id="inner-mic"] [data-microphone-stream-toggle]')
        assert page.locator('.canvas-node[data-node-id="root-mic"]').count() == 0

    def leave():
        page.evaluate("navigateToGraphContext(0)")
        page.wait_for_selector('[data-node-id="root-mic"] [data-microphone-stream-toggle]')

    def open_modal(node_id="root-mic"):
        page.locator(f'[data-node-id="{node_id}"] h3').dblclick()
        modal = page.locator('.cw-microphone-stream-modal')
        modal.wait_for(state="visible")
        return modal

    def saved(sink_id, stream_id):
        """Wait for backend finalization and return its independently measured file counters."""
        run = wait_for_run_predicate(server, run_id, lambda run: any(
            file.get("stream_id") == stream_id
            for file in run.get("results", {}).get(sink_id, {}).get("save_audio", {}).get("saved_files", [])
        ), "Save Audio did not finalize the uninterrupted microphone stream.", timeout_sec=12)
        page.wait_for_timeout(1)  # Dispatch browser request observations received during backend polling.
        result = run["results"][sink_id]["save_audio"]
        return next(file for file in result["saved_files"] if file["stream_id"] == stream_id)

    card().click()
    recording()
    first_stream = commands[-1]["values"]["stream_id"]
    assert commands[-1]["node"]["block_version"] == model["version"]
    assert commands[-1]["kind"] == f'microphone_stream@{model["version"]}'
    for _ in range(3):
        frames_before = page.evaluate("window.micTest.frames")
        enter()
        page.wait_for_function("before => window.micTest.frames >= before + 3", arg=frames_before)
        assert page.evaluate("window.micTest.streams[0].getTracks().every(track => track.readyState === 'live')")
        assert len(commands) == 1, "Navigation must not publish stop/start or change stream_id."
        leave()
        assert card().inner_text() == "Stop"
    modal = open_modal()
    assert modal.locator('[data-microphone-stream-start]').is_disabled(), "No second capture from the modal."
    assert modal.locator('[data-microphone-stream-stop]').is_enabled()
    page.screenshot(path=artifact_path(f"microphone-{origin}-navigation-active.png"))
    modal.locator('[data-close-block-modal]').click()
    recording()
    assert page.evaluate("window.micTest.recorders.length") == 1
    card().click()
    recording_file = saved("root-save", first_stream)
    stop = commands[-1]["values"]
    assert stop["action"] == "stop" and not stop["aborted"] and stop["frame_count"] >= 9
    assert stop["stream_id"] == first_stream
    assert recording_file["frames"] == stop["frame_count"] and recording_file["bytes"] == stop["byte_count"]
    assert Path(recording_file["absolute_path"]).stat().st_size == stop["byte_count"]
    assert page.evaluate("window.micTest.streams[0].getTracks().every(track => track.readyState === 'ended')")

    # A modal-started session must use its original node even after that modal is destroyed.
    modal = open_modal()
    modal.locator('[data-microphone-stream-start]').click()
    recording()
    second_stream = commands[-1]["values"]["stream_id"]
    modal.locator('[data-close-block-modal]').click()
    enter()
    other = open_modal("inner-mic")
    frames_before = page.evaluate("window.micTest.frames")
    page.wait_for_function("before => window.micTest.frames >= before + 2", arg=frames_before)
    other.locator('[data-close-block-modal]').click()
    leave()
    card().click()
    saved("root-save", second_stream)
    assert commands[-1]["node"]["id"] == "root-mic"

    # Symmetric case: capture within a composite continues when returning to the parent.
    enter()
    card("inner-mic").click()
    recording()
    third_stream = commands[-1]["values"]["stream_id"]
    leave()
    frames_before = page.evaluate("window.micTest.frames")
    page.wait_for_function("before => window.micTest.frames >= before + 3", arg=frames_before)
    enter()
    assert card("inner-mic").inner_text() == "Stop"
    card("inner-mic").focus()
    page.keyboard.press("Space")
    saved("inner-save", third_stream)
    leave()

    # The maximum duration still finalizes the original stream with no surface mounted.
    modal = open_modal()
    modal.locator('[data-block-config-field="max_duration_sec"]').fill("2")
    modal.locator('[data-microphone-stream-start]').click()
    recording()
    timed_stream = commands[-1]["values"]["stream_id"]
    modal.locator('[data-close-block-modal]').click()
    enter()
    saved("root-save", timed_stream)
    leave()
    page.wait_for_function("document.querySelector('.canvas-node[data-node-id=\"root-mic\"] [data-microphone-stream-toggle]')?.textContent === 'Start'")

    # An outstanding permission prompt survives navigation, but never resurrects a stopped Run.
    page.evaluate("window.micTest.holdPermission = true")
    card().click()
    page.wait_for_function("typeof window.micTest.releasePermission === 'function'")
    count_before_cancel = len(commands)
    enter()
    page.click("#stopRunButton")
    page.wait_for_function("!['prepared', 'running'].includes(state.currentRunStatus)")
    page.wait_for_timeout(350)
    page.evaluate("window.micTest.releasePermission(); window.micTest.holdPermission = false")
    page.wait_for_function("window.micTest.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))")
    assert len(commands) == count_before_cancel, "Cancelled permission must publish no start into a stopped/new Run."
    leave()

    # A new Run stays idle until a new explicit microphone gesture.
    with page.expect_response(lambda response: response.url.endswith("/runs/prepare") and response.request.method == "POST") as prepared:
        page.click("#loadRunButton")
    run_id = prepared.value.json()["run_id"]
    page.wait_for_function("document.querySelector('.canvas-node[data-node-id=\"root-mic\"] [data-microphone-stream-toggle]')?.disabled === false")
    assert page.evaluate("window.micTest.recorders.every(recorder => recorder.state === 'inactive')")

    # Page exit/bfcache releases the device; a return must not automatically resume capture.
    card().click()
    recording()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")
    page.wait_for_function("window.micTest.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))")
    page.wait_for_function("document.querySelector('.canvas-node[data-node-id=\"root-mic\"] [data-microphone-stream-toggle]')?.textContent === 'Start'")
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")
    assert page.evaluate("window.micTest.recorders.every(recorder => recorder.state === 'inactive')")

    # A real Run Stop while the card is absent must still release all hardware tracks.
    card().click()
    recording()
    enter()
    page.click("#stopRunButton")
    page.wait_for_function("window.micTest.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))")
    leave()
    page.wait_for_function("document.querySelector('.canvas-node[data-node-id=\"root-mic\"] [data-microphone-stream-toggle]')?.disabled")
    assert card().inner_text() == "Start"
    assert card().is_disabled()

    modal = open_modal()
    for width, height, label in ((1440, 900, "desktop"), (390, 740, "mobile"), (320, 568, "small")):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        bounds = modal.evaluate("""panel => {
          const rect = panel.getBoundingClientRect();
          const apply = panel.querySelector('[data-block-apply]').getBoundingClientRect();
          const close = panel.querySelector('[data-close-block-modal]').getBoundingClientRect();
          return {inside: rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight,
            apply: apply.bottom <= innerHeight, close: close.top >= 0,
            overflow: panel.scrollWidth > panel.clientWidth + 1};
        }""")
        page.screenshot(path=artifact_path(f"microphone-{origin}-navigation-{label}.png"))
        assert bounds["inside"] and bounds["apply"] and bounds["close"] and not bounds["overflow"], (label, bounds)
    run = http_json(server.base_url, f"/api/runs/{run_id}")
    assert run.get("status") == "cancelled"
    assert len([command for command in commands if command["values"]["action"] == "start"]) == 6
    for command in commands:
        expected_version = "0.0.0" if origin == "linked" and command["node"]["id"] == "inner-mic" else model["version"]
        assert command["node"]["block_version"] == expected_version
        assert command["kind"] == f"microphone_stream@{expected_version}"
    assert page.evaluate("!window.CWMicrophoneStream && !window.CWBlockUiBlocks?.microphone_streamNodeCard")
    print("[ok] Real microphone → WS → Save Audio: navigation, shared controls, final counts, hidden Run Stop and responsive modal")


def main(*, origin="managed") -> None:
    """Use a deterministic fake microphone device, with real Chromium capture and runtime IO."""
    with isolated_server() as server, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        try:
            context = browser.new_context(permissions=["microphone"], viewport={"width": 1440, "height": 900})
            test_navigation(context.new_page(), server, origin=origin)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
