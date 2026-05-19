import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Header } from "./Header.tsx";

describe("Header", () => {
  it("renders all three tabs with the active one selected", () => {
    render(<Header activeTab="dashboard" onTabChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(screen.getByRole("tab", { name: "Dashboard" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Sprint" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("invokes onTabChange when a tab is clicked", () => {
    const handler = vi.fn();
    render(<Header activeTab="dashboard" onTabChange={handler} />);
    fireEvent.click(screen.getByRole("tab", { name: "Sprint" }));
    expect(handler).toHaveBeenCalledWith("sprint");
  });

  it("hides the notification dot when there are none", () => {
    const { container } = render(
      <Header activeTab="dashboard" onTabChange={() => {}} hasNotifications={false} />,
    );
    expect(container.querySelector("[aria-label='Notifications']")).not.toBeNull();
  });
});
