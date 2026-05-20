import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HoldResumeControls } from "./HoldResumeControls.tsx";
import * as interveneMod from "./intervene.ts";

function mockOk(action: interveneMod.InterveneAction) {
  return {
    ok: true as const,
    sliceId: "slice-1",
    action,
    detail: "ok",
  };
}

describe("HoldResumeControls — state-aware affordances", () => {
  it("renders HOLD + MARK FAILED + SKIP and locks open-terminal in WORK", () => {
    render(<HoldResumeControls sliceId="slice-1" state="work" />);
    expect(screen.getByRole("button", { name: "HOLD" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "RESUME" })).toBeNull();
    expect(screen.getByRole("button", { name: "MARK FAILED" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "SKIP" })).toBeEnabled();
    // Locked open-terminal button is rendered as disabled with the
    // padlock + tooltip; the unlocked one is NOT rendered.
    const locked = screen.getByRole("button", { name: /🔒 OPEN IN TERMINAL/ });
    expect(locked).toBeDisabled();
  });

  it("renders RESUME (new) and unlocked open-terminal in HELD", () => {
    render(<HoldResumeControls sliceId="slice-1" state="held" />);
    expect(screen.getByRole("button", { name: "RESUME" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "OPEN IN TERMINAL" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "HOLD" })).toBeNull();
    expect(screen.queryByRole("button", { name: /🔒/ })).toBeNull();
  });

  it("hides HOLD in PR state (pr is not held-able)", () => {
    render(<HoldResumeControls sliceId="slice-1" state="pr" prUrl="https://example/pr/1" />);
    expect(screen.queryByRole("button", { name: "HOLD" })).toBeNull();
    // Open-terminal unlocked once the lane has reached PR.
    expect(screen.getByRole("button", { name: "OPEN IN TERMINAL" })).toBeEnabled();
    // PR link rendered.
    expect(screen.getByRole("link", { name: /VIEW PR/ })).toHaveAttribute(
      "href",
      "https://example/pr/1",
    );
  });

  it("RESUME button POSTs the resume action via useIntervene", async () => {
    const spy = vi.spyOn(interveneMod, "intervene").mockResolvedValue(mockOk("resume"));
    render(<HoldResumeControls sliceId="slice-7" state="held" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "RESUME" }));
    });

    expect(spy).toHaveBeenCalledWith("slice-7", "resume");
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Resume requested");
    });

    spy.mockRestore();
  });

  it("HOLD click shows the 'waiting for current turn' flash until state flips", async () => {
    const spy = vi.spyOn(interveneMod, "intervene").mockResolvedValue(mockOk("hold"));

    render(<HoldResumeControls sliceId="slice-2" state="work" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "HOLD" }));
    });

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(
        "Hold requested · waiting for current turn",
      );
    });

    spy.mockRestore();
  });

  it("flash bar can be dismissed via the × button", async () => {
    const spy = vi.spyOn(interveneMod, "intervene").mockResolvedValue(mockOk("resume"));

    render(<HoldResumeControls sliceId="slice-3" state="held" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "RESUME" }));
    });
    await waitFor(() => {
      expect(screen.getByRole("status")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    });
    expect(screen.queryByRole("status")).toBeNull();

    spy.mockRestore();
  });

  it("renders a 409 error message inline when the server refuses", async () => {
    const spy = vi.spyOn(interveneMod, "intervene").mockResolvedValue({
      ok: false,
      sliceId: "slice-1",
      action: "open-terminal",
      status: 409,
      error: "slice is in 'work'",
      currentState: "work",
    });

    // Render HELD so OPEN IN TERMINAL is visible; the mock simulates a
    // server-side race where the slice moved back to work before the
    // click landed.
    render(<HoldResumeControls sliceId="slice-1" state="held" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "OPEN IN TERMINAL" }));
    });

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("slice is in 'work'");
    });

    spy.mockRestore();
  });
});
