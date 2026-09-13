# -----------------------------------------------------------------------------
# Role: Streams browser microphone chunks through one graph audio output port.
# File Name: block.py
# Author: OpenAI Codex
# Created Date: 2026-09-04
# -----------------------------------------------------------------------------

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json

from bloxsmith_app.block_api import (
    APPLICATION_JSON,
    BlockDefinition,
    BlockRuntimeContext,
    BlockRuntimePreparation,
    BlockRuntimePreparationContext,
    BlockRuntimeResult,
    RuntimeAudioStreamClient,
    TEXT_PLAIN,
    render_inspector_template,
    render_node_card_template,
)


DEFAULT_TIMESLICE_MS = 250
DEFAULT_AUDIO_BITS_PER_SECOND = 128_000
DEFAULT_CHANNEL_COUNT = 1
DEFAULT_MAX_DURATION_SEC = 3_600


# Functional behavior:
# FB1 - Capture the operator browser microphone from block-owned JavaScript controls.
# FB2 - Publish MediaRecorder chunks through the single graph-wired audio_out port.
# FB3 - Keep capture bounded, expose transport failures, and release microphone/WebSocket resources.
# FB4 - Declare fixed audio_out and command_out ports and reject altered runtime ports.
# FB5 - Stay available for browser ingress in zeromq_active and no-op cleanly in centralized simulation.
# FB6 - Render block-owned node-card, modal, inspector, and capture assets.
# FB7 - Publish explicit start/stop JSON commands on command_out, never through the audio transport.
# FB8 - Share one run-scoped capture across cards/modals and composite navigation, with lifecycle cleanup.
class MicrophoneStreamBlock(BlockDefinition):
    """Expose one browser microphone as a continuous graph audio stream.

    Python owns the runtime contract and lifecycle declaration. The browser-owned
    UI owns microphone permission, MediaRecorder encoding, and publication through
    the generic Runtime Audio Streams facade injected by the framework.
    """

    kind = "microphone_stream"

    def ui_assets(self, surface: str = "modal") -> list[dict[str, str]]:
        """Return block-owned assets for the requested UI surface.

        Args:
            surface: Modal, inspector, or node-card surface name.
        """

        if surface == "modal":
            return [
                {"kind": "css", "path": "assets/css/block_ui.css"},
                {"kind": "js", "path": "assets/js/stream_capture.js"},
                {"kind": "js", "path": "assets/js/common.js"},
                {"kind": "js", "path": "assets/js/block_modal.js"},
            ]
        if surface == "inspector_panel":
            return [{"kind": "css", "path": "assets/css/block_ui.css"}]
        if surface == "node_card":
            return [
                {"kind": "css", "path": "assets/css/block_ui.css"},
                {"kind": "js", "path": "assets/js/stream_capture.js"},
                {"kind": "js", "path": "assets/js/common.js"},
                {"kind": "js", "path": "assets/js/node_card.js"},
            ]
        return []

    def render_node_card(
        self,
        *,
        node: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render controls and the command-node snapshot used beyond the card's lifetime."""

        del payload
        config = self._config(node.get("config"))
        rendered = render_node_card_template(
            block=self,
            node=node,
            node_classes=["microphone-stream-node"],
            replacements={
                "title": str(node.get("title") or self.default_title()),
                "timeslice_ms": str(config["timeslice_ms"]),
                "audio_bits_per_second": str(config["audio_bits_per_second"]),
                "channel_count": str(config["channel_count"]),
                "max_duration_sec": str(config["max_duration_sec"]),
                "profile": f"{config['channel_count']} canal · {config['audio_bits_per_second'] // 1000} kb/s",
            },
        )
        rendered.setdefault("context", {})["capture_node"] = {"id": node.get("id"), "outputs": node.get("outputs", [])}
        return rendered

    def render_inspector_panel(
        self,
        *,
        node: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render validated capture settings and the fixed port contract."""

        config = self._config(node.get("config"))
        template = (self.directory / "inspector_panel.html").read_text(encoding="utf-8")
        html = render_inspector_template(
            template=template,
            node={**node, "type": self.kind, "kind": self.kind},
            payload=payload,
            replacements=self._ui_replacements(config),
            show_duplicate=True,
        )
        return {
            "html": html,
            "context": {"node_id": str(node.get("id") or ""), "full_panel": True, **config},
        }

    def render_modal(
        self,
        *,
        node: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render settings and controls attached to the same capture as the canvas card."""

        config = self._config(node.get("config"))
        template = (self.directory / "block_modal.html").read_text(encoding="utf-8")
        for key, value in self._ui_replacements(config).items():
            template = template.replace(f"{{{{ {key} }}}}", str(value))
        html = self._render_generic_modal_template(template=template, node=node, payload=payload or {})
        return {
            "html": html,
            "context": {"node_id": str(node.get("id") or ""), "node_kind": self.kind, **config,
                        "capture_node": {"id": node.get("id"), "outputs": node.get("outputs", [])}},
        }

    def handle_ui_action(
        self,
        *,
        node: dict[str, Any],
        action: str,
        values: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish capture commands or normalize durable capture settings.

        Args:
            node: Serialized microphone node being edited.
            action: publish_capture_command or a generic settings update.
            values: Stream command metadata or the generic field patch.
            payload: Optional framework request context.
        """

        if action == "publish_capture_command":
            try:
                command = self._capture_command(values)
                outputs = node.get("outputs") or node.get("ports", {}).get("outputs", [])
                if not any(port.get("id") == 2 and port.get("name") == "command_out"
                           and port.get("transport", "message") == "message" for port in outputs):
                    raise ValueError("Le port command_out manque : recréez le bloc avec son nouveau modèle.")
            except (TypeError, ValueError) as exc:
                return {"error": str(exc)}
            return {
                "allow_while_running": True,
                "active_runtime_actions": [{
                    "action": "publish_output", "node_id": str(node.get("id") or ""),
                    "port_id": 2, "port_name": "command_out", "value": json.dumps(command, ensure_ascii=False),
                    "content_type": APPLICATION_JSON,
                }],
                "rerender_inspector": False,
            }

        result = super().handle_ui_action(
            node=node,
            action=action,
            values=values,
            payload=payload,
        )
        node_patch = result.get("node_patch")
        if not isinstance(node_patch, dict) or not isinstance(node_patch.get("config"), dict):
            return result
        raw_config = dict(node.get("config") or {})
        raw_config.update(node_patch["config"])
        normalized = self._config(raw_config)
        allowed = set(self.default_config())
        node_patch["config"] = {
            key: normalized[key]
            for key in node_patch["config"]
            if key in allowed
        }
        return result

    def prepare_runtime(self, context: BlockRuntimePreparationContext) -> BlockRuntimePreparation:
        """Validate the fixed port shape and keep the active ingress worker alive.

        Args:
            context: Side-effect-free preparation context for the selected mode.
        """

        self._validate_port_contract(context)
        if context.runtime_mode != "zeromq_active":
            return BlockRuntimePreparation()
        return BlockRuntimePreparation(keep_alive=True)

    def execute_runtime(self, context: BlockRuntimeContext) -> BlockRuntimeResult:
        """Expose readiness for browser ingress or skip unsupported simulation.

        Audio bytes never pass through this call: the generic browser WebSocket
        ingress publishes them directly through the compiled ``audio_out`` route.
        """

        try:
            self._validate_port_contract(context)
        except ValueError as exc:
            return self._failure(context, str(exc))

        config = self._config(context.config)
        metadata = {"microphone_stream": {"output_port": "audio_out", **config}}
        if context.runtime_mode != "zeromq_active":
            message = "Capture micro disponible uniquement en Active Runtime."
            return BlockRuntimeResult(
                status="skipped",
                outputs=[],
                logs=[f"[microphone-stream] {context.node_id}: {message}"],
                last_message=message,
                content_type=TEXT_PLAIN,
                worker_received="simulation sans flux audio",
                metadata=metadata,
            )

        client = context.services.get("runtime_audio_streams")
        connected = isinstance(client, RuntimeAudioStreamClient) and client.available
        if not connected:
            message = "Le port audio_out n'est pas relié à une entrée audio compatible."
            return BlockRuntimeResult(
                status="skipped",
                outputs=[],
                logs=[f"[microphone-stream] {context.node_id}: {message}"],
                last_message=message,
                content_type=TEXT_PLAIN,
                worker_received="audio_out non connecté",
                metadata=metadata,
            )

        message = "Passerelle micro prête sur audio_out ; démarrez la capture depuis le bloc."
        metadata["microphone_stream"]["connected"] = True
        return BlockRuntimeResult(
            status="success",
            outputs=[],
            logs=[f"[microphone-stream] {context.node_id}: {message}"],
            last_message=message,
            content_type=TEXT_PLAIN,
            worker_received="passerelle micro prête",
            metadata=metadata,
        )

    def _config(self, raw_config: Mapping[str, Any] | None) -> dict[str, int]:
        """Return bounded browser-capture settings from untrusted node config."""

        source = raw_config if isinstance(raw_config, Mapping) else {}
        channel_count = self._bounded_int(source.get("channel_count"), DEFAULT_CHANNEL_COUNT, 1, 2)
        return {
            "timeslice_ms": self._bounded_int(
                source.get("timeslice_ms"), DEFAULT_TIMESLICE_MS, 100, 2_000
            ),
            "audio_bits_per_second": self._bounded_int(
                source.get("audio_bits_per_second"),
                DEFAULT_AUDIO_BITS_PER_SECOND,
                16_000,
                320_000,
            ),
            "channel_count": channel_count,
            "max_duration_sec": self._bounded_int(
                source.get("max_duration_sec"), DEFAULT_MAX_DURATION_SEC, 1, 86_400
            ),
        }

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """Coerce one integer config value and clamp it to documented bounds."""

        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _ui_replacements(config: Mapping[str, int]) -> dict[str, str]:
        """Build escaped template values for modal and inspector settings."""

        return {
            "timeslice_ms": str(config["timeslice_ms"]),
            "audio_bits_per_second": str(config["audio_bits_per_second"]),
            "channel_count": str(config["channel_count"]),
            "channel_1_selected": "selected" if config["channel_count"] == 1 else "",
            "channel_2_selected": "selected" if config["channel_count"] == 2 else "",
            "max_duration_sec": str(config["max_duration_sec"]),
        }

    @staticmethod
    def _capture_command(values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a bounded capture event before publishing it on the data edge.

        Stop includes counts of successfully enqueued chunks, including the last
        MediaRecorder chunk, so a consumer can detect loss across transports.
        """
        action = values.get("action")
        stream_id = values.get("stream_id")
        if not isinstance(action, str) or action not in {"start", "stop"}:
            raise ValueError("Commande micro attendue : start ou stop.")
        if not isinstance(stream_id, str) or not stream_id.strip() or len(stream_id) > 128:
            raise ValueError("Identifiant de flux micro invalide.")
        command = {"action": action, "stream_id": stream_id}
        if action == "stop":
            for key in ("frame_count", "byte_count"):
                value = values.get(key)
                if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
                    raise ValueError(f"Compteur micro invalide : {key}.")
                command[key] = value
            aborted = values.get("aborted", False)
            if type(aborted) is not bool:
                raise ValueError("Le champ aborted doit être booléen.")
            command["aborted"] = aborted
        return command

    @staticmethod
    def _validate_port_contract(context: Any) -> None:
        """Reject graph nodes that alter the fixed microphone audio port shape."""

        inputs = tuple(getattr(context, "input_ports", ()) or ())
        outputs = tuple(getattr(context, "output_ports", ()) or ())
        if inputs or len(outputs) != 2:
            raise ValueError("Microphone Stream requires audio_stream audio_out and message command_out; recreate legacy nodes.")
        ports = {getattr(port, "id", 0): port for port in outputs}
        output = ports.get(1)
        if int(getattr(output, "id", 0) or 0) != 1 or str(getattr(output, "name", "") or "") != "audio_out":
            raise ValueError("Microphone Stream output must remain port 1 named audio_out.")
        if str(getattr(output, "transport", "") or "") != "audio_stream":
            raise ValueError("Microphone Stream audio_out must use transport audio_stream.")
        command = ports.get(2)
        if getattr(command, "name", "") != "command_out" or getattr(command, "transport", "message") != "message":
            raise ValueError("Microphone Stream command_out must be message port 2.")

    @staticmethod
    def _failure(context: BlockRuntimeContext, message: str) -> BlockRuntimeResult:
        """Return one consistent port-contract failure result."""

        return BlockRuntimeResult(
            status="failed",
            outputs=[],
            logs=[f"[microphone-stream-error] {context.node_id}: {message}"],
            error=message,
            exit_code=1,
            last_message=message,
            content_type=TEXT_PLAIN,
            worker_received="-",
        )
