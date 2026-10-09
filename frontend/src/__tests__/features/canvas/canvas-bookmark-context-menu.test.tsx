// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CanvasBookmarkContextMenu } from "@/features/canvas/ui/CanvasBookmarkContextMenu";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "canvas.bookmarks.setCurrent": "设置当前定位（覆盖）",
        "canvas.bookmarks.setNew": "设置当前定位",
        "canvas.bookmarks.deleteCurrent": "删除当前定位",
        "canvas.bookmarks.clearAll": "清除所有定位",
      }[key] ?? key),
  }),
}));

describe("CanvasBookmarkContextMenu", () => {
  const baseProps = {
    index: 0,
    filled: true,
    position: { x: 10, y: 10 },
    onSetCurrent: vi.fn(),
    onDelete: vi.fn(),
    onClearAll: vi.fn(),
    onClose: vi.fn(),
  };

  it("renders the three actions", () => {
    render(<CanvasBookmarkContextMenu {...baseProps} />);
    expect(screen.getByText("设置当前定位（覆盖）")).toBeInTheDocument();
    expect(screen.getByText("删除当前定位")).toBeInTheDocument();
    expect(screen.getByText("清除所有定位")).toBeInTheDocument();
  });

  it("invokes the set handler then closes", async () => {
    const onSetCurrent = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <CanvasBookmarkContextMenu {...baseProps} onSetCurrent={onSetCurrent} onClose={onClose} />,
    );
    await user.click(screen.getByText("设置当前定位（覆盖）"));
    expect(onSetCurrent).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("hides delete and drops the overwrite hint when the slot is empty", () => {
    render(<CanvasBookmarkContextMenu {...baseProps} filled={false} />);
    // Empty slot: no delete row, and the set label has no （覆盖） suffix.
    expect(screen.queryByText("删除当前定位")).not.toBeInTheDocument();
    expect(screen.getByText("设置当前定位")).toBeInTheDocument();
    expect(screen.queryByText("设置当前定位（覆盖）")).not.toBeInTheDocument();
    // Clear-all stays available regardless of slot state.
    expect(screen.getByText("清除所有定位")).toBeInTheDocument();
  });
});
