/** Mount properties accessibility for a static surface. */
import { enhanceProperties } from "./properties.js";
import { mountCaptureSettings } from "./capture_settings.js";
export function mount(root) {
  const detachProperties = enhanceProperties(root);
  const detachSettings = mountCaptureSettings(root);
  return { dispose() { detachSettings(); detachProperties(); } };
}
