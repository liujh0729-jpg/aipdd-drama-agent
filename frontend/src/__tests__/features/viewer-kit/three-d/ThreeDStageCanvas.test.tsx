// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThreeDStageCanvas } from "@/features/viewer-kit/three-d/ThreeDStageCanvas";
import { createViewerApp } from "@/features/viewer-kit/three-d/engine/viewerApp";

vi.mock("@/features/viewer-kit/three-d/engine/viewerApp", () => ({
  createViewerApp: vi.fn(),
}));

const mockedCreateViewerApp = vi.mocked(createViewerApp);

function makeViewer() {
  return {
    destroy: vi.fn(),
    fly: { setInputEnabled: vi.fn() },
    loadSplat: vi.fn().mockResolvedValue(undefined),
    loadPano: vi.fn().mockResolvedValue(undefined),
    loadCollision: vi.fn().mockResolvedValue(undefined),
    onStatus: vi.fn(() => vi.fn()),
    selectAtScreen: vi.fn(() => true),
    clearSelection: vi.fn(),
  };
}

function Host() {
  const [ready, setReady] = useState(false);
  return (
    <>
      <div data-testid="ready">{ready ? "yes" : "no"}</div>
      <ThreeDStageCanvas
        splatUrl="/static/world.sog"
        collisionUrl={null}
        onReady={() => setReady(true)}
        onError={() => undefined}
        onStatus={() => undefined}
      />
    </>
  );
}

describe("ThreeDStageCanvas", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("keeps one PlayCanvas app across parent rerenders from inline callbacks", async () => {
    mockedCreateViewerApp.mockResolvedValue(makeViewer() as never);

    render(<Host />);

    await waitFor(() => expect(screen.getByTestId("ready")).toHaveTextContent("yes"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(mockedCreateViewerApp).toHaveBeenCalledTimes(1);
  });

  it("loads pano360 sources into the same PlayCanvas stage", async () => {
    const viewer = makeViewer();
    const onSourceReady = vi.fn();
    mockedCreateViewerApp.mockResolvedValue(viewer as never);

    const { rerender } = render(
      <ThreeDStageCanvas
        splatUrl={null}
        panoUrl="/static/world-pano.png"
        collisionUrl={null}
        onReady={() => undefined}
        onError={() => undefined}
        onStatus={() => undefined}
        onSourceReady={onSourceReady}
      />,
    );

    await waitFor(() => expect(viewer.loadPano).toHaveBeenCalledWith(
      "/static/world-pano.png",
      { sourceTransform: undefined },
    ));
    await waitFor(() => expect(onSourceReady).toHaveBeenCalledTimes(1));
    expect(viewer.loadSplat).not.toHaveBeenCalled();

    rerender(
      <ThreeDStageCanvas
        splatUrl="/static/world.sog"
        panoUrl={null}
        collisionUrl={null}
        onReady={() => undefined}
        onError={() => undefined}
        onStatus={() => undefined}
        onSourceReady={onSourceReady}
      />,
    );

    await waitFor(() => expect(viewer.loadSplat).toHaveBeenCalledWith(
      "/static/world.sog",
      { orientationMode: undefined, sourceTransform: undefined },
    ));
    await waitFor(() => expect(onSourceReady).toHaveBeenCalledTimes(2));
  });

  it("uses right click and Shift+left click as place requests", async () => {
    const viewer = makeViewer();
    const onPlaceRequest = vi.fn();
    mockedCreateViewerApp.mockResolvedValue(viewer as never);

    const { container } = render(
      <ThreeDStageCanvas
        splatUrl="/static/world.sog"
        collisionUrl={null}
        interactionActive
        onReady={() => undefined}
        onError={() => undefined}
        onStatus={() => undefined}
        onPlaceRequest={onPlaceRequest}
      />,
    );

    await waitFor(() => expect(mockedCreateViewerApp).toHaveBeenCalledTimes(1));
    const canvas = container.querySelector("canvas");
    expect(canvas).toBeTruthy();

    fireEvent.pointerDown(canvas!, { button: 2, clientX: 10, clientY: 12 });
    fireEvent.pointerUp(canvas!, { button: 2, clientX: 10, clientY: 12 });
    fireEvent.pointerDown(canvas!, { button: 0, clientX: 20, clientY: 22, shiftKey: true });
    fireEvent.pointerUp(canvas!, { button: 0, clientX: 20, clientY: 22 });

    expect(onPlaceRequest).toHaveBeenCalledTimes(2);
    expect(viewer.selectAtScreen).not.toHaveBeenCalled();
  });

  it("uses plain left click for selection and clears empty hits", async () => {
    const viewer = makeViewer();
    viewer.selectAtScreen.mockReturnValueOnce(false);
    mockedCreateViewerApp.mockResolvedValue(viewer as never);

    const { container } = render(
      <ThreeDStageCanvas
        splatUrl="/static/world.sog"
        collisionUrl={null}
        interactionActive
        onReady={() => undefined}
        onError={() => undefined}
        onStatus={() => undefined}
      />,
    );

    await waitFor(() => expect(mockedCreateViewerApp).toHaveBeenCalledTimes(1));
    const canvas = container.querySelector("canvas");
    expect(canvas).toBeTruthy();

    fireEvent.pointerDown(canvas!, { button: 0, clientX: 30, clientY: 32 });
    fireEvent.pointerUp(canvas!, { button: 0, clientX: 30, clientY: 32 });

    expect(viewer.selectAtScreen).toHaveBeenCalledWith(30, 32);
    expect(viewer.clearSelection).toHaveBeenCalledTimes(1);
  });
});
