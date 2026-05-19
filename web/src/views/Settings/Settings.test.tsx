import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings.tsx";
import { TAB_ORDER, type TabId } from "./types.ts";

function navButton(tab: TabId): HTMLElement {
  const buttons = screen.getAllByRole("button");
  const hit = buttons.find((b) => b.getAttribute("data-tab") === tab);
  if (!hit) {
    throw new Error(`nav button for tab ${tab} not found`);
  }
  return hit;
}

describe("Settings", () => {
  let originalFetch: typeof globalThis.fetch | undefined;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    // Force GET /config to fail so the fixture path is exercised.
    globalThis.fetch = vi.fn(async () => {
      throw new Error("no server in test env");
    }) as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    if (originalFetch) {
      globalThis.fetch = originalFetch;
    }
  });

  it("renders all seven tabs in the nav", () => {
    render(<Settings />);
    for (const tab of TAB_ORDER) {
      expect(navButton(tab)).toBeInTheDocument();
    }
  });

  it("switches tabs when nav items are clicked", async () => {
    render(<Settings />);

    // Default is Project — repo slug input is present.
    expect(screen.getByLabelText(/^Repo slug$/)).toBeInTheDocument();

    fireEvent.click(navButton("lanes"));
    expect(
      screen.getByLabelText(/default parallel lanes/i),
    ).toBeInTheDocument();

    fireEvent.click(navButton("gates"));
    await waitFor(() =>
      expect(screen.getByDisplayValue("lint")).toBeInTheDocument(),
    );

    fireEvent.click(navButton("health"));
    expect(screen.getByText(/Health · trends/i)).toBeInTheDocument();
  });

  it("respects the controlled tab prop", () => {
    render(<Settings tab="health" />);
    expect(screen.getByText(/Health · trends/i)).toBeInTheDocument();
  });

  it("validates repo slug on blur", async () => {
    render(<Settings />);
    const input = screen.getByLabelText(/^Repo slug$/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "not-a-slug" } });
    fireEvent.blur(input);
    expect(await screen.findByRole("alert")).toHaveTextContent(/expected owner\/name/i);
  });

  it("clamps lanes slider to 1–8", () => {
    render(<Settings tab="lanes" />);
    const slider = screen.getByLabelText(/default parallel lanes/i) as HTMLInputElement;
    expect(slider.min).toBe("1");
    expect(slider.max).toBe("8");
  });
});
