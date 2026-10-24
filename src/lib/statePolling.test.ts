// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import type { RadarState } from "./api";
import { watchState } from "./statePolling";

const state: RadarState = {
  generated_at: "2026-10-25T00:00:00Z", last_message_at: "2026-10-25T00:00:00Z",
  data_age_sec: 0, last_event_at: null, pipeline_lag_sec: 0, stale: false,
  events: [], zone_counts: {}, active_events: 0, active_zones: 0,
};

describe("visible map polling", () => {
  let hidden: boolean;
  let stop: (() => void) | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    vi.spyOn(api, "state").mockResolvedValue(state);
  });

  afterEach(() => {
    stop?.();
    stop = undefined;
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("loads immediately and refreshes without a permanent connection", async () => {
    const receive = vi.fn();
    const socket = vi.spyOn(globalThis, "WebSocket");
    stop = watchState(receive, vi.fn());
    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.state).toHaveBeenCalledTimes(2);
    expect(receive).toHaveBeenCalledWith(state);
    expect(socket).not.toHaveBeenCalled();
  });

  it("pauses background tabs and immediately refreshes on return", async () => {
    hidden = true;
    stop = watchState(vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api.state).not.toHaveBeenCalled();
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(api.state).toHaveBeenCalledTimes(1);
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api.state).toHaveBeenCalledTimes(1);
  });

  it("does not overlap slow requests", async () => {
    let finish!: (value: RadarState) => void;
    vi.mocked(api.state).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    stop = watchState(vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(14_000);
    expect(api.state).toHaveBeenCalledTimes(1);
    finish(state);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.state).toHaveBeenCalledTimes(2);
  });

  it("ignores cancelled responses after the tab becomes visible again", async () => {
    const pending: Array<(value: RadarState) => void> = [];
    vi.mocked(api.state).mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
    const receive = vi.fn();
    stop = watchState(receive, vi.fn());
    const oldSignal = vi.mocked(api.state).mock.calls[0][0];
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    expect(oldSignal?.aborted).toBe(true);
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    pending[0](state);
    await vi.advanceTimersByTimeAsync(0);
    expect(receive).not.toHaveBeenCalled();
    pending[1](state);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(receive).toHaveBeenCalledTimes(1);
    expect(api.state).toHaveBeenCalledTimes(3);
  });

  it("recovers after a failed request", async () => {
    vi.mocked(api.state).mockRejectedValueOnce(new Error("unavailable"));
    const online = vi.fn();
    stop = watchState(vi.fn(), online);
    await vi.advanceTimersByTimeAsync(0);
    expect(online).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(online).toHaveBeenLastCalledWith(true);
  });

  it("aborts a timed-out request and schedules a retry", async () => {
    vi.mocked(api.state).mockImplementation((signal) => new Promise((_, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const online = vi.fn();
    stop = watchState(vi.fn(), online);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(online).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.state).toHaveBeenCalledTimes(2);
  });

  it("cancels pending work when the map is unmounted", async () => {
    let finish!: (value: RadarState) => void;
    vi.mocked(api.state).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const receive = vi.fn();
    stop = watchState(receive, vi.fn());
    const signal = vi.mocked(api.state).mock.calls[0][0];
    stop();
    expect(signal?.aborted).toBe(true);
    finish(state);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(receive).not.toHaveBeenCalled();
    expect(api.state).toHaveBeenCalledTimes(1);
  });
});
