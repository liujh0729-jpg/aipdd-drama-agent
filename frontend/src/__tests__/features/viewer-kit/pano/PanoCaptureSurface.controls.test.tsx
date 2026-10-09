// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PanoCaptureSurface } from "@/features/viewer-kit/pano/PanoCaptureSurface";
import { fovToZoom } from "@/features/viewer-kit/pano/panoCapture";
import type { PanoViewerManifest } from "@/features/viewer-kit/pano/panoManifest";

const DEG_TO_RAD = Math.PI / 180;

const { MockPanoViewer, viewerInstances } = vi.hoisted(() => {
  const instances: Array<{
    rotate: ReturnType<typeof vi.fn>;
    zoom: ReturnType<typeof vi.fn>;
    animate: ReturnType<typeof vi.fn>;
    setOption: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
    getPosition: ReturnType<typeof vi.fn>;
    getZoomLevel: ReturnType<typeof vi.fn>;
  }> = [];

  class MockViewer {
    rotate = vi.fn();
    zoom = vi.fn();
    animate = vi.fn();
    setOption = vi.fn();
    destroy = vi.fn();
    getPosition = vi.fn(() => ({ yaw: Math.PI / 2, pitch: Math.PI / 6 }));
    getZoomLevel = vi.fn(() => 60.6060606061);

    constructor(options?: { container?: HTMLElement }) {
      if (options?.container) {
        const canvas = document.createElement("canvas");
        canvas.width = 640;
        canvas.height = 360;
        canvas.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as unknown as HTMLCanvasElement["getContext"];
        canvas.toBlob = vi.fn((callback: BlobCallback) => {
          callback(new Blob(["shot"], { type: "image/png" }));
        }) as unknown as HTMLCanvasElement["toBlob"];
        options.container.appendChild(canvas);
      }
      instances.push(this);
    }
  }

  return { MockPanoViewer: MockViewer, viewerInstances: instances };
});

vi.mock("@photo-sphere-viewer/core", () => ({
  Viewer: MockPanoViewer,
  CONSTANTS: {
    ACTIONS: {
      ROTATE_UP: "ROTATE_UP",
      ROTATE_DOWN: "ROTATE_DOWN",
      ROTATE_LEFT: "ROTATE_LEFT",
      ROTATE_RIGHT: "ROTATE_RIGHT",
      ZOOM_IN: "ZOOM_IN",
      ZOOM_OUT: "ZOOM_OUT",
    },
  },
}));

const manifest: PanoViewerManifest = {
  viewer_kind: "pano360",
  mode: "beat",
  project: "demo",
  scene_id: "basement",
  display_name: "地下室",
  source: {
    slot_kind: "scene_director_pano_360",
    url: "/static/demo/pano.png",
  },
  correction: {
    front_yaw_deg: 15,
    sphere_correction_deg: {
      roll: 1,
      pitch: 2,
      yaw: 3,
    },
  },
  beat_context: {
    episode: 1,
    beat: 3,
    detected_identities: [],
    detected_props: [],
  },
  allowed_destinations: ["beat_selected_background"],
};

let writeClipboard: ReturnType<typeof vi.fn>;

describe("PanoCaptureSurface controls", () => {
  beforeEach(() => {
    viewerInstances.length = 0;
    writeClipboard = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: vi.fn(() => ({ drawImage: vi.fn() })),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
      configurable: true,
      value: vi.fn((callback: BlobCallback) => {
        callback(new Blob(["shot"], { type: "image/png" }));
      }),
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: writeClipboard,
      },
    });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:pano-shot"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("shows live camera metadata and exposes FOV presets", async () => {
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));

    expect(screen.getByText("project demo")).toBeInTheDocument();
    expect(screen.getByText("scene basement")).toBeInTheDocument();
    expect(screen.getByText("EP 1 / Beat 3")).toBeInTheDocument();
    expect(screen.getByText("canonical pano")).toBeInTheDocument();
    expect(screen.getByText("保存目标 selected_background")).toBeInTheDocument();
    expect(screen.getByText(/yaw 90\.0°/)).toBeInTheDocument();
    expect(screen.getByText(/pitch 30\.0°/)).toBeInTheDocument();
    expect(screen.getByText(/fov 70°/)).toBeInTheDocument();
    expect(screen.getByText(/26mm/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "标准 70°" })).toBeInTheDocument();
    expect(screen.getByLabelText("Front yaw value")).toHaveValue(15);
    expect(screen.getByLabelText("Correction roll")).toBeInTheDocument();
    expect(screen.getByLabelText("Correction roll value")).toHaveValue(1);
    expect(screen.getByLabelText("Correction pitch")).toBeInTheDocument();
    expect(screen.getByLabelText("Correction pitch value")).toHaveValue(2);
    expect(screen.getByLabelText("Correction yaw")).toBeInTheDocument();
    expect(screen.getByLabelText("Correction yaw value")).toHaveValue(3);
    expect(screen.getByRole("button", { name: "当前设为正面" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置校正" })).toBeInTheDocument();
  });

  it("applies FOV, direction, and correction actions to the viewer", async () => {
    const user = userEvent.setup();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    const viewer = viewerInstances[0];

    await user.click(screen.getByRole("button", { name: "广角 120°" }));
    expect(viewer.zoom).toHaveBeenCalledWith(fovToZoom(120));

    await user.click(screen.getByRole("button", { name: "Right" }));
    expect(viewer.rotate).toHaveBeenCalledWith({
      yaw: ((15 + 90) * Math.PI) / 180,
      pitch: 0,
    });

    await user.click(screen.getByRole("button", { name: "当前视角矫正" }));
    expect(viewer.setOption).toHaveBeenCalledWith("sphereCorrection", {
      roll: expect.closeTo((manifest.correction.sphere_correction_deg.roll * Math.PI) / 180, 12),
      tilt: expect.closeTo((32 * Math.PI) / 180, 12),
      pan: expect.closeTo((93 * Math.PI) / 180, 12),
    });
    expect(viewer.rotate).toHaveBeenCalledWith({
      yaw: 0,
      pitch: 0,
    });
  });

  it("enters and exits planet view while preserving the previous camera", async () => {
    const user = userEvent.setup();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    const viewer = viewerInstances[0];

    await user.click(screen.getByRole("button", { name: "小行星视角" }));
    expect(viewer.zoom).toHaveBeenLastCalledWith(fovToZoom(160));
    expect(viewer.rotate).toHaveBeenLastCalledWith({
      yaw: 0,
      pitch: -Math.PI / 2,
    });

    await user.click(screen.getByRole("button", { name: "退出小行星" }));
    expect(viewer.zoom).toHaveBeenLastCalledWith(expect.closeTo(fovToZoom(70), 10));
    expect(viewer.rotate).toHaveBeenLastCalledWith({
      yaw: Math.PI / 2,
      pitch: Math.PI / 6,
    });
  });

  it("applies front yaw and sphere correction controls to viewer actions", async () => {
    const user = userEvent.setup();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    const viewer = viewerInstances[0];

    fireEvent.change(screen.getByLabelText("Front yaw value"), {
      target: { value: "45" },
    });
    await user.click(screen.getByRole("button", { name: "Right" }));
    expect(viewer.rotate).toHaveBeenLastCalledWith({
      yaw: 135 * DEG_TO_RAD,
      pitch: 0,
    });

    await user.click(screen.getByRole("button", { name: "当前设为正面" }));
    expect(screen.getByLabelText("Front yaw value")).toHaveValue(90);

    fireEvent.change(screen.getByLabelText("Correction yaw value"), {
      target: { value: "20" },
    });
    expect(viewer.setOption).toHaveBeenLastCalledWith("sphereCorrection", {
      roll: expect.closeTo(1 * DEG_TO_RAD, 12),
      tilt: expect.closeTo(2 * DEG_TO_RAD, 12),
      pan: expect.closeTo(20 * DEG_TO_RAD, 12),
    });

    fireEvent.change(screen.getByLabelText("Correction pitch value"), {
      target: { value: "-5" },
    });
    expect(viewer.setOption).toHaveBeenLastCalledWith("sphereCorrection", {
      roll: expect.closeTo(1 * DEG_TO_RAD, 12),
      tilt: expect.closeTo(-5 * DEG_TO_RAD, 12),
      pan: expect.closeTo(20 * DEG_TO_RAD, 12),
    });

    await user.click(screen.getByRole("button", { name: "重置校正" }));
    expect(viewer.setOption).toHaveBeenLastCalledWith("sphereCorrection", {
      roll: 0,
      tilt: 0,
      pan: 0,
    });
  });

  it("uses a non-passive native wheel listener for crop-frame zoom", async () => {
    const addEventListenerSpy = vi.spyOn(HTMLElement.prototype, "addEventListener");

    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    expect(screen.getByTestId("pano-capture-frame")).toBeInTheDocument();
    expect(addEventListenerSpy).toHaveBeenCalledWith(
      "wheel",
      expect.any(Function),
      { passive: false },
    );

    addEventListenerSpy.mockRestore();
  });

  it("shows and toggles capture guide overlays", async () => {
    const user = userEvent.setup();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));

    expect(screen.getByTestId("pano-guide-horizon")).toBeInTheDocument();
    expect(screen.getByTestId("pano-guide-center")).toBeInTheDocument();
    expect(screen.getByTestId("pano-guide-thirds")).toBeInTheDocument();
    expect(screen.getByTestId("pano-capture-frame")).toBeInTheDocument();
    expect(screen.getByTestId("pano-capture-resize-handle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置截图框" })).toBeInTheDocument();

    fireEvent.wheel(screen.getByTestId("pano-capture-frame"), { deltaY: -120 });

    await user.click(screen.getByRole("button", { name: "隐藏辅助线" }));
    expect(screen.queryByTestId("pano-guide-horizon")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pano-guide-center")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pano-guide-thirds")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "显示辅助线" }));
    expect(screen.getByTestId("pano-guide-horizon")).toBeInTheDocument();
  });

  it("keeps a thumbnail result after each capture", async () => {
    const user = userEvent.setup();
    const onCapture = vi.fn();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={onCapture}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));

    await user.click(screen.getByRole("button", { name: "截图" }));

    await waitFor(() => expect(onCapture).toHaveBeenCalledTimes(1));
    expect(screen.getByText("截图 1")).toBeInTheDocument();
    expect(screen.getByText(/640 x 360/)).toBeInTheDocument();
    expect(screen.getByAltText("截图 1")).toHaveAttribute("src", "blob:pano-shot");
    expect(screen.getByRole("link", { name: "下载截图 1" })).toHaveAttribute(
      "download",
      "pano-capture-1.png",
    );
    await user.click(screen.getByRole("button", { name: "清空截图列表" }));
    expect(screen.queryByText("截图 1")).not.toBeInTheDocument();
  });

  it("shows saved anchor metadata returned by the capture handler", async () => {
    const user = userEvent.setup();
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => ({
          anchor_id: "selected_background",
          rel_path: "director_control_frames/ep001/beat_03/selected_background.png",
        })}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "截图" }));

    expect(await screen.findByText(/已保存 selected_background/)).toBeInTheDocument();
    expect(
      screen.getByText(/director_control_frames\/ep001\/beat_03\/selected_background\.png/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/640 x 360/).length).toBeGreaterThanOrEqual(1);
  });

  it("copies correction JSON with direction contract and camera metadata", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: writeClipboard,
      },
    });
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "复制参数 JSON" }));

    await waitFor(() => expect(writeClipboard).toHaveBeenCalledTimes(1));
    const payload = JSON.parse(writeClipboard.mock.calls[0][0]);
    expect(payload).toMatchObject({
      front_yaw_deg: 15,
      sphere_correction_deg: {
        roll: 1,
        pitch: 2,
        yaw: 3,
      },
      sphere_correction_rad: {
        roll: expect.any(Number),
        tilt: expect.any(Number),
        pan: expect.any(Number),
      },
      cubemap_contract: {
        front_yaw_deg: 15,
        right_yaw_deg: 105,
        back_yaw_deg: -165,
        left_yaw_deg: -75,
        seam_yaw_deg: -165,
      },
      fov_deg: 70,
      source: manifest.source,
      timestamp: expect.any(String),
    });
  });

  it("sends the current correction payload to the save handler", async () => {
    const user = userEvent.setup();
    const onSaveCorrection = vi.fn().mockResolvedValue(undefined);
    render(
      <PanoCaptureSurface
        manifest={manifest}
        onCapture={() => undefined}
        onSaveCorrection={onSaveCorrection}
      />,
    );

    await waitFor(() => expect(viewerInstances).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "保存校正" }));

    expect(onSaveCorrection).toHaveBeenCalledWith({
      front_yaw_deg: 15,
      sphere_correction_deg: {
        roll: 1,
        pitch: 2,
        yaw: 3,
      },
    });
  });
});
