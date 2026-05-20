import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import * as interveneMod from "./intervene.ts";
import { useIntervene } from "./useIntervene.ts";

describe("useIntervene", () => {
  it("toggles pending around a run() call", async () => {
    const interveneSpy = vi.spyOn(interveneMod, "intervene").mockResolvedValue({
      ok: true,
      sliceId: "slice-1",
      action: "hold",
      detail: "queued",
    });

    const { result } = renderHook(() => useIntervene("slice-1"));
    expect(result.current.pending).toBe(false);
    expect(result.current.lastResult).toBeNull();

    let resultPromise!: ReturnType<typeof result.current.run>;
    act(() => {
      resultPromise = result.current.run("hold");
    });
    expect(result.current.pending).toBe(true);

    const interveneResult = await resultPromise;
    expect(interveneResult.ok).toBe(true);
    await waitFor(() => {
      expect(result.current.pending).toBe(false);
    });
    expect(result.current.lastResult?.ok).toBe(true);
    expect(interveneSpy).toHaveBeenCalledWith("slice-1", "hold");

    interveneSpy.mockRestore();
  });

  it("records a failure result without throwing", async () => {
    const interveneSpy = vi.spyOn(interveneMod, "intervene").mockResolvedValue({
      ok: false,
      sliceId: "slice-2",
      action: "open-terminal",
      status: 409,
      error: "slice is in 'work'",
      currentState: "work",
    });

    const { result } = renderHook(() => useIntervene("slice-2"));
    await act(async () => {
      await result.current.run("open-terminal");
    });

    expect(result.current.pending).toBe(false);
    expect(result.current.lastResult?.ok).toBe(false);
    if (result.current.lastResult && !result.current.lastResult.ok) {
      expect(result.current.lastResult.status).toBe(409);
      expect(result.current.lastResult.currentState).toBe("work");
    }

    interveneSpy.mockRestore();
  });

  it("clear() resets the last result so the flash bar can be dismissed", async () => {
    const interveneSpy = vi.spyOn(interveneMod, "intervene").mockResolvedValue({
      ok: true,
      sliceId: "slice-1",
      action: "hold",
    });

    const { result } = renderHook(() => useIntervene("slice-1"));
    await act(async () => {
      await result.current.run("hold");
    });
    expect(result.current.lastResult).not.toBeNull();

    act(() => {
      result.current.clear();
    });
    expect(result.current.lastResult).toBeNull();

    interveneSpy.mockRestore();
  });
});
