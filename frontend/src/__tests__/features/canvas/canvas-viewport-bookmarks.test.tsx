// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CanvasViewportBookmarks } from "@/features/canvas/ui/CanvasViewportBookmarks";
import { createEmptyBookmarks } from "@/features/canvas/domain/viewportBookmarks";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function makeProps(overrides = {}) {
  return {
    bookmarks: createEmptyBookmarks(),
    onJump: vi.fn(),
    onSetCurrent: vi.fn(),
    onDelete: vi.fn(),
    onClearAll: vi.fn(),
    ...overrides,
  };
}

describe("CanvasViewportBookmarks", () => {
  it("renders 10 digit buttons labelled 1..9,0", () => {
    render(<CanvasViewportBookmarks {...makeProps()} />);
    for (const digit of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]) {
      expect(screen.getByRole("button", { name: digit })).toBeInTheDocument();
    }
  });

  it("jumps on left-click of a filled slot", async () => {
    const onJump = vi.fn();
    const bookmarks = createEmptyBookmarks();
    bookmarks[0] = { x: 0, y: 0, zoom: 1 };
    const user = userEvent.setup();
    render(<CanvasViewportBookmarks {...makeProps({ bookmarks, onJump })} />);
    await user.click(screen.getByRole("button", { name: "1" }));
    expect(onJump).toHaveBeenCalledWith(0);
  });

  it("saves the current viewport on left-click of an empty slot", async () => {
    const onJump = vi.fn();
    const onSetCurrent = vi.fn();
    const user = userEvent.setup();
    render(<CanvasViewportBookmarks {...makeProps({ onJump, onSetCurrent })} />);
    await user.click(screen.getByRole("button", { name: "5" }));
    expect(onSetCurrent).toHaveBeenCalledWith(4);
    expect(onJump).not.toHaveBeenCalled();
  });

  it("opens the context menu on right-click", () => {
    render(<CanvasViewportBookmarks {...makeProps()} />);
    fireEvent.contextMenu(screen.getByRole("button", { name: "3" }));
    // Empty slot menu shows the "set new" label and clear-all (always present).
    expect(screen.getByText("canvas.bookmarks.setNew")).toBeInTheDocument();
    expect(screen.getByText("canvas.bookmarks.clearAll")).toBeInTheDocument();
  });
});
