import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useDrawerUrl } from "./useDrawerUrl";

function setUrl(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

describe("useDrawerUrl", () => {
  beforeEach(() => setUrl(""));

  it("reads the initial slice id from ?drawer=", () => {
    setUrl("?drawer=slice-4");
    const { result } = renderHook(() => useDrawerUrl());
    expect(result.current.sliceId).toBe("slice-4");
  });

  it("opens and writes the id to the URL", () => {
    const { result } = renderHook(() => useDrawerUrl());
    expect(result.current.sliceId).toBe(null);
    act(() => result.current.open("slice-9"));
    expect(result.current.sliceId).toBe("slice-9");
    expect(window.location.search).toContain("drawer=slice-9");
  });

  it("close() clears the URL param", () => {
    setUrl("?drawer=slice-2");
    const { result } = renderHook(() => useDrawerUrl());
    act(() => result.current.close());
    expect(result.current.sliceId).toBe(null);
    expect(window.location.search).not.toContain("drawer=");
  });
});
