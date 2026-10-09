// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { PanoCaptureDialog } from "@/features/viewer-kit/pano/PanoCaptureDialog";
import type { PanoViewerManifest } from "@/features/viewer-kit/pano/panoManifest";

const { surfaceProps } = vi.hoisted(() => ({
  surfaceProps: {
    current: null as null | { onClose?: () => void },
  },
}));

vi.mock("@/features/viewer-kit/pano/PanoCaptureSurface", () => ({
  PanoCaptureSurface: (props: { onClose?: () => void }) => {
    surfaceProps.current = props;
    return (
      <button type="button" data-testid="pano-capture-surface" onClick={props.onClose}>
        关闭
      </button>
    );
  },
}));

const manifest: PanoViewerManifest = {
  viewer_kind: "pano360",
  mode: "beat",
  project: "demo",
  scene_id: "地下室",
  display_name: "地下室 360",
  source: {
    slot_kind: "scene_director_pano_360",
    url: "/static/demo/pano.jpg",
  },
  correction: {
    front_yaw_deg: 0,
    sphere_correction_deg: {
      roll: 0,
      pitch: 0,
      yaw: 0,
    },
  },
  allowed_destinations: ["view", "download", "beat_selected_background"],
};

describe("PanoCaptureDialog", () => {
  function renderDialog(ui: ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  }

  it("uses the same fullscreen viewer shell optimizations as the 3GS dialog", () => {
    const { unmount } = renderDialog(
      <PanoCaptureDialog
        open
        onOpenChange={() => undefined}
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveClass("h-dvh");
    expect(dialog).toHaveClass("w-dvw");
    expect(dialog).toHaveClass("sm:max-w-none");
    expect(document.querySelector('[data-slot="dialog-overlay"]')).toHaveClass(
      "supports-backdrop-filter:backdrop-blur-none",
    );
    expect(document.body).toHaveClass("st-viewer-immersive-active");

    unmount();
    expect(document.body).not.toHaveClass("st-viewer-immersive-active");
  });

  it("keeps the fullscreen close action inside the viewer toolbar", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    renderDialog(
      <PanoCaptureDialog
        open
        onOpenChange={onOpenChange}
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
    expect(surfaceProps.current?.onClose).toEqual(expect.any(Function));

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
