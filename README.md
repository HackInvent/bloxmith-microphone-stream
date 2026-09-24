# Microphone Stream

<!-- block-metadata:start -->
[![Block version: 0.1.1](https://img.shields.io/badge/block-0.1.1-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->


Capture browser microphone audio and publish sound and commands through **two explicit links**. The block neither saves audio nor constructs ingress URLs; it uses public APIs injected by the framework.

## Ports

- **`audio_out`**, ID 1: `audio_stream`, `audio/*`, **Opus in WebM or Ogg**, 48 kHz decode clock, mono/stereo; fan-out to compatible consumers.
- **`command_out`**, ID 2: `message`, `application/json`; capture `start` and `stop` events.
- No inputs and no implicit data channel inside audio.

## Usage

1. Connect `audio_out` to `Save Audio.audio_in`.
2. Separately connect `command_out` to `Save Audio.command_in`.
3. Start **Run** in Active Runtime. **Play is not required.**
4. Choose **Start microphone**, grant browser access, then choose **Stop**.
5. Wait for Save Audio to report finalization before stopping the Run.

Each capture has a new `stream_id`. Old one-output instances must be recreated from the new model and rewired. User blueprints are not automatically modified.

## Data protocol

The block-owned UI action `publish_capture_command` validates metadata and requests generic publication on `command_out`, without changing configuration or topology.

```json
{"action":"start","stream_id":"session-unique"}
{"action":"stop","stream_id":"session-unique","frame_count":12,"byte_count":32000,"aborted":false}
```

`start` is published after the bridge opens and before MediaRecorder starts. `stop` follows the final `dataavailable` event; counts include all chunks accepted by the local WebSocket, **not consumer acknowledgements**. Audio and data have no common ordering guarantee, so receivers must correlate them.

Capture/transport errors send `stop` with `aborted: true` when possible and release resources. Rejected publication is reported explicitly. Commands use ordinary graph delivery, without automatic retry or a guarantee of remote processing.

## Settings

| Field | Default | Range |
| --- | --- | --- |
| `timeslice_ms` | 250 | 100–2,000 ms requested MediaRecorder interval |
| `audio_bits_per_second` | 128,000 | 16,000–320,000 bits/s |
| `channel_count` | See `model.json` | Mono or stereo |
| `continuous_capture` | `false` | Explicitly enable untimed capture until manual/Run stop |
| `max_duration_sec` | 3,600 | 1–86,400 seconds; timed capture only |

Encoding settings are read at capture start; they do not reconfigure an active stream. Apply saves settings; closing properties does not stop capture. The modal separates capture controls from settings, explains why Start is unavailable without a Run, and keeps Close/Apply outside its scrolling area even on mobile.

For long conversations, enable **Continuous capture** in the modal or inspector,
apply, then start the microphone. The timed-stop field is hidden while continuous
mode is selected; its value is retained for later timed captures. Existing
blueprints remain timed unless this option is explicitly enabled. Only the
duration timer is disabled: buffer limits, error handling and lifecycle cleanup
remain active. Closing properties or entering a composite never renews or
restarts MediaRecorder. Stop still sends the final frame/byte counts for the same
`stream_id`. Downstream blocks keep their own limits; OpenAI usage remains billed.

The block explicitly requests WebM/Opus, falling back to Ogg/Opus. MP4/AAC and generic MIME types without a guaranteed codec are not used. If MediaRecorder supports neither Opus format, the block requests a compatible browser **before** opening the microphone or publishing start. It neither transcodes nor replays chunks.

The device rate may differ from the declared 48 kHz Opus decode clock. Actual track channel count is used when available.

This format is shared with **OpenAI TTS Stream** (Opus/Ogg). Either source can feed **Save Audio** or **OpenAI Realtime STT** through audio + command links, or **Audio Play Stream** through audio only. Existing two-output microphone nodes remain usable; recreating them also refreshes persisted port capabilities.

## Lifecycle and limits

### Release UI contract (0.1.1)

`model.json.ui_assets` declares every modal, inspector and card asset, including
the shared relative imports. Surface entrypoints are ES modules; no block UI or
capture helper is registered on an unversioned browser global. CSS selectors
are scoped to `microphone_stream@0.1.1`.

The shared module keeps capture sessions separate by release, blueprint instance,
Run and node. Card/modal teardown removes only UI subscriptions. Start/stop
commands keep the originating node **and release version**, even after another
modal or composite becomes visible. Capture and audio/data protocols are unchanged.

Runtime readiness reads the public audio service's `available` property, not its
concrete Python class: the same check accepts a native client or the supervised
package host's service proxy. A disconnected port still reports capture unavailable.
The linked navigation suite also places a synthetic second release inside the
same blueprint's composite and checks version-specific command routing.

Use an explicitly versioned installed or linked package. Existing `0.1.0` releases
and blueprint references are not rewritten; install/reload and explicitly upgrade
the node version. There is no legacy unversioned UI fallback.

- Active Runtime capture is UI-driven, without Play. One capture is shared by the card and properties for a given block, Run and browser.
- Entering/leaving a composite, refreshing a card or closing properties **does not stop the microphone**. MediaRecorder, WebSocket and `stream_id` remain the same. Returning controls show actual capture state and can stop it; opening properties does not start another microphone. This also applies to a microphone inside a composite.
- Simulation returns `skipped`, without microphone access or publication.
- Manual stop, maximum duration in timed mode, errors, Run stop/pause/change, blueprint change or page closure release tracks and the bridge. Surface unmount only removes UI subscriptions. A new Run or page never automatically restarts capture.
- Commands retain their originating block context even when another modal opens. Lifecycle checks use the local public Run context, without network polling; bridge closure stops capture.
- Abrupt page or Run closure cannot guarantee final `stop` delivery. Stop the microphone first and check Save Audio.
- The WebSocket buffer is bounded. Saturation stops capture and marks it incomplete.

## Verification

From the private integration workspace:

```sh
python3 -B tests/run_tests.py microphone_stream
```

Captures go to ignored results without personal paths. The suites cover both modes, commands, WebM/Opus and Ogg/Opus selection, AAC-only browser rejection, device-independent Opus clock and cleanup.

`F5.53_microphone_navigation.py` installs the current package and uses real Chromium, MediaRecorder, the bridge and Save Audio with a synthetic microphone. It tests composite navigation, capture inside composites, shared card/modal controls, saved counts, off-screen duration limits, cancelled late permission, new Runs, page exit and stopping a Run while the microphone is out of view. Properties are checked on desktop and narrow screens. `F5.54_microphone_linked_navigation.py` repeats the same assertions using a linked package. `F5.44` also exercises the installed package in both runtime modes and imports the actual capture ES module for deterministic lifecycle checks. The TTS suite covers all six shared-format source/consumer connections.

Continuous-mode tests check the absence of an automatic timer, manual stop and
final counters, strict boolean opt-in, default timed behavior, capture past its
otherwise scheduled deadline while navigating, and settings persistence/reopening.

## Compatibility policy

[compatibility.json](compatibility.json) records HackInvent's verified BloxSmith versions and test evidence. The outer harness is a **bundled-block test installation**; the release-specific suites described above additionally install and link this package through the real framework. Evidence covers those explicit cases, not every distribution format, browser/OS or live provider. Other framework versions are unverified, not necessarily incompatible.

The block-version badge follows `model.json`, not a published Git tag. `unversioned` means that no block release version is declared; no number is inferred from the framework version. The framework still uses `model.json` for its runtime/install contract; the tester-owned JSON does not replace it. Official integration tests run in the private `bloxmith-blocs` workspace. Test helpers and the proprietary framework are not bundled in this public block repository.

## Properties ergonomics

Modal and inspector styles are owned by this package and scoped to its exact
release. Forms adapt to narrow panels, checkboxes stay beside their labels, and
long values do not widen the inspector. Existing labels are associated with
controls; keyboard navigation complements the block’s own tab handlers.
These presentation helpers do not change port bindings, authored settings,
runtime behavior or the block’s original surface cleanup.
