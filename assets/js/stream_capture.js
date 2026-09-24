/**
 * Role: Captures MediaRecorder chunks and publishes them through audio_out.
 * File Name: stream_capture.js
 * Author: OpenAI Codex
 * Created Date: 2026-09-04
 */



/**
 * Select explicit WebM/Opus or Ogg/Opus support, without silently falling back to AAC.
 *
 * @returns {{mimeType: string, codec: string}|null} Selected profile or null.
 */
function preferredRecorderProfile() {
  if (typeof MediaRecorder === "undefined") {
    return null;
  }
  const candidates = [
    { mimeType: "audio/webm;codecs=opus", codec: "opus" },
    { mimeType: "audio/ogg;codecs=opus", codec: "opus" },
  ];
  if (typeof MediaRecorder.isTypeSupported !== "function") {
    return null;
  }
  return candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate.mimeType)) || null;
}

/**
 * Stop all hardware tracks owned by one capture session.
 *
 * @param {MediaStream|null} stream - Browser stream to release.
 * @returns {void}
 */
function stopTracks(stream) {
  for (const track of stream?.getTracks?.() || []) {
    track.stop();
  }
}

/**
 * Convert unknown thrown values to a concise operator-facing message.
 *
 * @param {unknown} error - Error or arbitrary rejected value.
 * @returns {string} Human-readable message.
 */
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error || "erreur inconnue");
}

/**
 * Create one start/stop microphone controller, independent of any block UI surface.
 *
 * @param {object} options - Generic API, config callback, and UI callbacks.
 * @param {object} options.api - Block UI API containing runtimeAudioStreams and applyAction.
 * @param {Function} options.config - Returns bounded capture settings.
 * @param {Function} [options.onStatus] - Receives current capture text.
 * @param {Function} [options.onStart] - Called once MediaRecorder starts.
 * @param {Function} [options.onStop] - Called after all resources close.
 * @param {Function} [options.onError] - Receives transport/capture errors.
 * @returns {{start: Function, stop: Function, dispose: Function, isActive: Function}} Controller.
 */
export function createStreamer(options = {}) {
  let current = null;
  let disposed = false;
  const status = (message) => options.onStatus?.(message);

  /** Report a capture/command failure while retaining ownership until cleanup. */
  function report(session, error) {
    session.failed = true;
    const message = errorMessage(error);
    status(`Flux interrompu : ${message}`);
    options.onError?.(error, message);
  }

  /** Publish only JSON command metadata through the explicit graph output. */
  async function publishCommand(session, action) {
    const values = { action, stream_id: session.publisher.descriptor.stream_id };
    if (action === "stop") {
      Object.assign(values, {
        frame_count: session.frames, byte_count: session.bytes, aborted: session.failed,
      });
    }
    const result = await options.api.applyAction("publish_capture_command", values);
    if (result?.error || result?.active_runtime_actions_result?.ok !== true) {
      throw new Error(result?.error || "The microphone command publication was not confirmed.");
    }
  }

  /** Finalize once, after the final dataavailable, then release every resource. */
  function finish(session) {
    if (session.finishing) return session.finishing;
    session.closing = true;
    window.clearTimeout(session.timer);
    stopTracks(session.stream);
    session.finishing = (async () => {
      try {
        // WebSocket close follows queued binary chunks. The independent data
        // command carries counts so the receiver can wait for their delivery.
        session.publisher?.close?.();
        if (session.startAttempted) await publishCommand(session, "stop");
        if (!session.failed) status("Microphone stopped · stop command sent.");
      } catch (error) {
        report(session, error);
      } finally {
        session.publisher = null;
        session.stream = null;
        session.recorder = null;
        if (current === session) current = null;
        session.resolveDone();
        options.onStop?.();
      }
    })();
    return session.finishing;
  }

  /** Stop recording on a capture/transport failure and send an aborted stop. */
  function fail(session, error) {
    if (session.failed || session.closing) return;
    report(session, error);
    session.cancelled = true;
    if (session.recorder && session.recorder.state !== "inactive") {
      try { session.recorder.stop(); } catch (_error) { void finish(session); }
    } else if (!session.starting) {
      void finish(session);
    }
  }

  /** Require Opus support before permission, then attach audio and publish start before encoding. */
  async function start() {
    if (disposed || current) return;
    const session = {
      starting: true, cancelled: false, failed: false, closing: false,
      finishing: null, stream: null, publisher: null, recorder: null,
      frames: 0, bytes: 0, startAttempted: false, timer: 0,
    };
    current = session;
    session.done = new Promise(resolve => { session.resolveDone = resolve; });
    status("Autorisation du microphone...");
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("MediaRecorder capture is unavailable in this browser.");
      }
      if (typeof options.api?.runtimeAudioStreams?.openOutput !== "function"
          || typeof options.api?.applyAction !== "function") {
        throw new Error("The audio gateway or the command publication is unavailable.");
      }
      const profile = preferredRecorderProfile();
      if (!profile) throw new Error("This browser cannot record Opus in WebM or Ogg. Use a browser compatible.");
      const raw = typeof options.config === "function" ? options.config() : {};
      const config = {
        timesliceMs: Math.max(100, Math.min(2000, Number(raw.timesliceMs) || 250)),
        audioBitsPerSecond: Math.max(16000, Math.min(320000, Number(raw.audioBitsPerSecond) || 128000)),
        channelCount: Number(raw.channelCount) === 2 ? 2 : 1,
        maxDurationSec: Math.max(1, Math.min(86400, Number(raw.maxDurationSec) || 3600)),
        continuousCapture: raw.continuousCapture === true,
      };
      session.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: { ideal: config.channelCount }, sampleRate: { ideal: 48000 } },
        video: false,
      });
      if (session.cancelled) return await finish(session);
      const settings = session.stream.getAudioTracks?.()[0]?.getSettings?.() || {};
      // Opus is transported on its 48 kHz decoding clock, independently of the device input rate.
      const sampleRateHz = 48000;
      const channels = [1, 2].includes(Number(settings.channelCount)) ? Number(settings.channelCount) : config.channelCount;
      const recorder = new MediaRecorder(session.stream, {
        mimeType: profile.mimeType, audioBitsPerSecond: config.audioBitsPerSecond,
      });
      session.recorder = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (session.failed || session.closing || !event.data || event.data.size <= 0) return;
        try {
          if (!session.publisher.sendFrame(event.data)) {
            throw new Error("The WebSocket buffer is saturated; the capture was stopped.");
          }
          session.frames += 1;
          session.bytes += event.data.size;
        } catch (error) {
          fail(session, error);
        }
      });
      recorder.addEventListener("stop", () => void finish(session), { once: true });
      recorder.addEventListener("error", (event) => {
        fail(session, event.error || new Error("MediaRecorder reported an error."));
      });
      status("Connecting to the audio_out and command_out ports...");
      session.publisher = await options.api.runtimeAudioStreams.openOutput({
        outputPort: "audio_out", codec: profile.codec, sampleRateHz, channels,
        onState: (event) => {
          options.onTransportState?.(event);
          if (event.type === "runtime_audio_stream.closed" && !session.closing) {
            fail(session, new Error("The audio connection was closed."));
          }
        },
        onError: (error) => fail(session, error),
      });
      if (session.cancelled) return await finish(session);
      if (!session.publisher.descriptor?.stream_id) throw new Error("Stream identifier unavailable.");
      session.startAttempted = true;
      await publishCommand(session, "start");
      if (session.cancelled) return await finish(session);
      recorder.start(config.timesliceMs);
      session.starting = false;
      status(`Microphone streaming (${profile.codec}, ${sampleRateHz} Hz, ${channels} channel(s)).`);
      if (!config.continuousCapture) {
        session.timer = window.setTimeout(stop, config.maxDurationSec * 1000);
      }
      options.onStart?.({ codec: profile.codec, sampleRateHz, channels });
    } catch (error) {
      report(session, error);
      await finish(session);
    } finally {
      session.starting = false;
    }
  }

  /** Request the final chunk and resolve after stop publication and resource cleanup. */
  function stop() {
    const session = current;
    if (!session) return Promise.resolve();
    if (session.closing) return session.done;
    session.cancelled = true;
    window.clearTimeout(session.timer);
    if (session.recorder && session.recorder.state !== "inactive") {
      try { session.recorder.stop(); } catch (error) { fail(session, error); }
    } else if (!session.starting) {
      void finish(session);
    }
    return session.done;
  }

  /** Cancel pending permission/connection and stop an already running capture. */
  function dispose() {
    disposed = true;
    return stop();
  }

  return { start, stop, dispose, isActive: () => current !== null };
}
