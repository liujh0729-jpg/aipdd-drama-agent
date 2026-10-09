// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

const render = vi.fn();
const createRoot = vi.fn(() => ({ render }));

vi.mock("react-dom/client", () => ({
  createRoot,
}));

describe("getOrCreateReactRoot", () => {
  beforeEach(() => {
    createRoot.mockClear();
    render.mockClear();
    vi.resetModules();
  });

  it("reuses the React root already attached to a container", async () => {
    const { getOrCreateReactRoot } = await import("@/lib/react-root");
    const container = document.createElement("div");

    const first = getOrCreateReactRoot(container);
    const second = getOrCreateReactRoot(container);

    expect(first).toBe(second);
    expect(createRoot).toHaveBeenCalledTimes(1);
  });

  it("reuses the container root after the helper module is reloaded", async () => {
    const container = document.createElement("div");

    const firstModule = await import("@/lib/react-root");
    const first = firstModule.getOrCreateReactRoot(container);

    vi.resetModules();
    const secondModule = await import("@/lib/react-root");
    const second = secondModule.getOrCreateReactRoot(container);

    expect(first).toBe(second);
    expect(createRoot).toHaveBeenCalledTimes(1);
  });
});
