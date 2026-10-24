import { api } from "./api";
import type { RadarState } from "./api";

const POLL_MS = 10_000;
const REQUEST_TIMEOUT_MS = 15_000;

/** Only visible maps poll; a slow request never starts an overlapping one. */
export function watchState(
  onState: (state: RadarState) => void,
  onOnline: (online: boolean) => void
): () => void {
  let active = true;
  let timer: number | null = null;
  let request: AbortController | null = null;

  const pull = async () => {
    if (!active || document.hidden || request) return;
    const current = new AbortController();
    request = current;
    const timeout = window.setTimeout(() => current.abort(), REQUEST_TIMEOUT_MS);
    try {
      const payload = await api.state(current.signal);
      if (!active || request !== current || current.signal.aborted) return;
      onState(payload);
      onOnline(true);
    } catch {
      if (active && request === current && !document.hidden) onOnline(false);
    } finally {
      window.clearTimeout(timeout);
      if (request === current) {
        request = null;
        if (active && !document.hidden) timer = window.setTimeout(pull, POLL_MS);
      }
    }
  };

  const visibilityChanged = () => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    request?.abort();
    request = null;
    if (!document.hidden) void pull();
  };

  document.addEventListener("visibilitychange", visibilityChanged);
  void pull();
  return () => {
    active = false;
    document.removeEventListener("visibilitychange", visibilityChanged);
    if (timer !== null) window.clearTimeout(timer);
    request?.abort();
    request = null;
  };
}
