/** Show the duration limit only for timed capture; never alter the saved limit. */
export function mountCaptureSettings(root) {
  const continuous = root.querySelector('[data-block-config-field="continuous_capture"]');
  const duration = root.querySelector("[data-microphone-duration]");
  if (!continuous || !duration) return () => {};
  const input = duration.querySelector("input");
  const wasReadOnly = input?.readOnly === true;
  const refresh = () => {
    duration.hidden = continuous.checked;
    // Hidden, unused timing must not block Apply on a range-validation error.
    if (input) input.readOnly = wasReadOnly || continuous.checked;
  };
  refresh();
  continuous.addEventListener("change", refresh);
  return () => {
    continuous.removeEventListener("change", refresh);
    if (input) input.readOnly = wasReadOnly;
  };
}
