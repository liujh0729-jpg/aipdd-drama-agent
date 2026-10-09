// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NodeResizeHandle } from "@/features/canvas/ui/NodeResizeHandle";

const resizeControlProps = vi.fn();

vi.mock("@xyflow/react", () => ({
  NodeResizeControl: (props: Record<string, unknown>) => {
    resizeControlProps(props);
    return <div data-testid="resize-control">{props.children as React.ReactNode}</div>;
  },
}));

describe("NodeResizeHandle", () => {
  it("forwards keepAspectRatio to NodeResizeControl so aspect-locked nodes stay free of letterbox bars", () => {
    render(<NodeResizeHandle keepAspectRatio />);
    expect(resizeControlProps).toHaveBeenCalledWith(
      expect.objectContaining({ keepAspectRatio: true }),
    );
  });

  it("leaves keepAspectRatio undefined by default", () => {
    resizeControlProps.mockClear();
    render(<NodeResizeHandle />);
    expect(resizeControlProps).toHaveBeenCalledWith(
      expect.objectContaining({ keepAspectRatio: undefined }),
    );
  });
});
