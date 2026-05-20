import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type InterveneAction, SliceDrawer } from "./SliceDrawer";
import { drawerFixtureWork, drawerFixturesByState } from "./fixtures";
import { TERMINAL_LOCKED_STATES, TERMINAL_UNLOCKED_STATES, isTerminalLocked } from "./format";
import type { InterveneResult, SliceDrawerData, SliceState } from "./types";

const NOOP_INTERVENE = async (): Promise<InterveneResult> => ({ ok: true, status: 200 });

function renderDrawer(
  opts: {
    data?: SliceDrawerData;
    onClose?: () => void;
    onIntervene?: (id: string, action: InterveneAction) => Promise<InterveneResult>;
  } = {},
) {
  const onClose = opts.onClose ?? vi.fn();
  const intervene = opts.onIntervene ?? NOOP_INTERVENE;
  const data = opts.data ?? drawerFixtureWork;
  const utils = render(<SliceDrawer data={data} onClose={onClose} onIntervene={intervene} />);
  return { onClose, intervene, ...utils };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SliceDrawer — locking semantics", () => {
  it("expected locked/unlocked state sets are exhaustive and disjoint", () => {
    const all: SliceState[] = [
      "pending",
      "setup",
      "work",
      "pr",
      "review",
      "merged",
      "failed",
      "needs_human_review",
      "blocked_upstream",
      "abandoned",
      "held",
    ];
    for (const s of all) {
      const inLocked = TERMINAL_LOCKED_STATES.has(s);
      const inUnlocked = TERMINAL_UNLOCKED_STATES.has(s);
      // every state lands in exactly one bucket
      expect(inLocked !== inUnlocked).toBe(true);
    }
  });

  it.each(["setup", "work", "pr"] as const)("locks Open-in-terminal when state=%s", (state) => {
    renderDrawer({ data: { ...drawerFixtureWork, state } });
    const btn = screen.getByTestId("open-in-terminal");
    expect(btn).toHaveAttribute("data-locked", "true");
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toMatch(/HOLD first/);
    expect(btn.getAttribute("title")).toContain(state.toUpperCase());
  });

  it.each([
    "held",
    "review",
    "merged",
    "failed",
    "needs_human_review",
    "abandoned",
    "blocked_upstream",
  ] as const)("unlocks Open-in-terminal when state=%s", (state) => {
    renderDrawer({ data: { ...drawerFixtureWork, state } });
    const btn = screen.getByTestId("open-in-terminal");
    expect(btn).toHaveAttribute("data-locked", "false");
    expect(btn).not.toBeDisabled();
  });

  it("renders the gating note only when locked", () => {
    const { unmount } = renderDrawer({ data: { ...drawerFixtureWork, state: "work" } });
    expect(screen.getByTestId("slice-drawer-gate-note")).toBeInTheDocument();
    unmount();

    renderDrawer({ data: { ...drawerFixtureWork, state: "held" } });
    expect(screen.queryByTestId("slice-drawer-gate-note")).not.toBeInTheDocument();
  });

  it("disables HOLD when slice is already in a resting state (nothing to hold)", () => {
    renderDrawer({ data: { ...drawerFixtureWork, state: "merged" } });
    expect(screen.getByTestId("hold-button")).toBeDisabled();
  });

  it("isTerminalLocked() matches the unlocked-state set", () => {
    expect(isTerminalLocked("work")).toBe(true);
    expect(isTerminalLocked("held")).toBe(false);
    expect(isTerminalLocked("review")).toBe(false);
  });
});

describe("SliceDrawer — HOLD POST + flash", () => {
  it("sends a POST when HOLD is clicked and flashes 'Hold requested'", async () => {
    const intervene = vi.fn(async (): Promise<InterveneResult> => ({ ok: true, status: 200 }));
    renderDrawer({ data: { ...drawerFixtureWork, state: "work" }, onIntervene: intervene });

    fireEvent.click(screen.getByTestId("hold-button"));

    await waitFor(() => expect(intervene).toHaveBeenCalledTimes(1));
    expect(intervene).toHaveBeenCalledWith("slice-4", "hold");
    expect(await screen.findByTestId("slice-drawer-flash")).toHaveTextContent("Hold requested");
  });

  it("shows an error flash when HOLD POST fails", async () => {
    const intervene = vi.fn(
      async (): Promise<InterveneResult> => ({ ok: false, status: 500, error: "boom" }),
    );
    renderDrawer({ data: { ...drawerFixtureWork, state: "work" }, onIntervene: intervene });

    fireEvent.click(screen.getByTestId("hold-button"));

    const flash = await screen.findByTestId("slice-drawer-flash");
    expect(flash).toHaveTextContent("hold failed");
    expect(flash.className).toContain("naml-drawer__flash--error");
  });

  it("does not POST when Open-in-terminal is locked", () => {
    const intervene = vi.fn(async (): Promise<InterveneResult> => ({ ok: true, status: 200 }));
    renderDrawer({ data: { ...drawerFixtureWork, state: "work" }, onIntervene: intervene });
    fireEvent.click(screen.getByTestId("open-in-terminal"));
    expect(intervene).not.toHaveBeenCalled();
  });

  it("POSTs open-terminal when unlocked", async () => {
    const intervene = vi.fn(async (): Promise<InterveneResult> => ({ ok: true, status: 200 }));
    renderDrawer({ data: { ...drawerFixtureWork, state: "held" }, onIntervene: intervene });
    fireEvent.click(screen.getByTestId("open-in-terminal"));
    await waitFor(() => expect(intervene).toHaveBeenCalledWith("slice-4", "open-terminal"));
  });
});

describe("SliceDrawer — close affordances", () => {
  it("calls onClose when × is clicked", () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });
    fireEvent.click(screen.getByLabelText("Close drawer"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the overlay is clicked outside the drawer", () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });
    fireEvent.click(screen.getByTestId("slice-drawer-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onClose when clicking inside the drawer", () => {
    const onClose = vi.fn();
    renderDrawer({ onClose });
    fireEvent.click(screen.getByTestId("slice-drawer"));
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("SliceDrawer — content", () => {
  it("renders the slice id, title, and state pill", () => {
    renderDrawer();
    expect(screen.getByText("slice-4")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /HTML renderer with mock-target snapshot/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("slice-drawer-state-pill")).toHaveTextContent("WORK");
  });

  it("shows the AC checklist with done count", () => {
    renderDrawer();
    const ac = screen.getByTestId("slice-drawer-ac");
    expect(ac).toHaveTextContent("3 of 5 verified");
  });

  it("renders the timeline with retry + current markers", () => {
    renderDrawer();
    const timeline = screen.getByTestId("slice-drawer-timeline");
    expect(timeline.querySelector('[data-status="retry"]')).not.toBeNull();
    expect(timeline.querySelector('[data-status="current"]')).not.toBeNull();
    expect(timeline).toHaveTextContent("GATE FAIL");
  });

  it("renders the gate-output pre block", () => {
    renderDrawer();
    expect(screen.getByTestId("slice-drawer-gate-output")).toHaveTextContent(/typecheck/);
  });

  it("renders the review verdict block as PENDING when state < review", () => {
    renderDrawer();
    expect(screen.getByTestId("slice-drawer-review")).toHaveTextContent("PENDING");
  });

  it("renders LGTM verdict when state reaches review", () => {
    renderDrawer({ data: drawerFixturesByState.review });
    expect(screen.getByTestId("slice-drawer-review")).toHaveTextContent("LGTM");
  });

  it("renders for every state without crashing", () => {
    for (const state of Object.keys(drawerFixturesByState) as SliceState[]) {
      const { unmount } = renderDrawer({ data: drawerFixturesByState[state] });
      expect(screen.getByTestId("slice-drawer")).toHaveAttribute("data-state", state);
      unmount();
    }
  });
});
