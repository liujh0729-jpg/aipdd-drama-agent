// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { withImageCacheBust } from "@/features/canvas/application/imageData";
import { CanvasNodeImage } from "@/features/canvas/ui/CanvasNodeImage";

describe("CanvasNodeImage wake refresh", () => {
  it("does not cache-bust static image src when the page wakes", () => {
    render(<CanvasNodeImage alt="preview" src="/static/admin/demo/image.png" />);

    const image = screen.getByAltText("preview") as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("/static/admin/demo/image.png");

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(image.getAttribute("src")).toBe("/static/admin/demo/image.png");
  });

  it("uses file version tokens instead of stacking frontend cache-bust tokens", () => {
    expect(withImageCacheBust("/static/admin/demo/image.png?v=file-version&st_v=old", "new")).toBe(
      "/static/admin/demo/image.png?v=file-version",
    );
  });

  it("adds frontend cache-bust tokens when no file version token exists", () => {
    expect(withImageCacheBust("/static/admin/demo/image.png?size=large", "new")).toBe(
      "/static/admin/demo/image.png?size=large&st_v=new",
    );
  });
});
