// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent, { PointerEventsCheckLevel } from "@testing-library/user-event";
import { useRegionStore } from "@/stores/region-store";
import { getRegionCookie, clearRegionCookie } from "@/lib/region-cookie";

const runtimeState = vi.hoisted(() => ({ authRequired: true }));
const navigateMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/cluster-config", () => ({
  clusterConfig: {
    mode: "multi-region",
    regions: [
      { id: "cn-1", displayName: "华东一区" },
      { id: "us-1", displayName: "美西一区" },
    ],
  },
}));

vi.mock("@/lib/runtime-config", () => ({
  authRequired: () => runtimeState.authRequired,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
}));

// i18n mock: return the key so we can assert by key.
vi.mock("react-i18next", async () => {
  const actual = await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return { ...actual, useTranslation: () => ({ t: (k: string) => k, i18n: { language: "zh" } }) };
});

import { RegionSelector } from "@/components/login/region-selector";

describe("RegionSelector", () => {
  beforeEach(() => {
    useRegionStore.setState({ selectedRegionId: null });
    clearRegionCookie();
    runtimeState.authRequired = true;
    navigateMock.mockReset();
  });

  it("renders placeholder when no region is picked", () => {
    render(<RegionSelector />);
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("writes store + cookie when user picks a region", async () => {
    const user = userEvent.setup();
    render(<RegionSelector />);
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByText("华东一区"));
    expect(useRegionStore.getState().selectedRegionId).toBe("cn-1");
    expect(getRegionCookie()).toBe("cn-1");
  });

  it("navigates to the app after region selection when auth is not required", async () => {
    runtimeState.authRequired = false;
    const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never });
    render(<RegionSelector />);

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByText("华东一区"));

    expect(useRegionStore.getState().selectedRegionId).toBe("cn-1");
    expect(getRegionCookie()).toBe("cn-1");
    expect(navigateMock).toHaveBeenCalledWith({ to: "/", replace: true });
  });
});

describe("RegionSelector — single-region auto-select", () => {
  beforeEach(() => {
    vi.resetModules();
    clearRegionCookie();
  });

  it("auto-selects the only region and renders read-only", async () => {
    vi.doMock("@/lib/cluster-config", () => ({
      clusterConfig: { mode: "multi-region", regions: [{ id: "only-1", displayName: "唯一区" }] },
    }));
    // Reload the store fresh so the component and this assertion read the
    // same module instance after vi.resetModules().
    const { useRegionStore: freshStore } = await import("@/stores/region-store");
    freshStore.setState({ selectedRegionId: null });
    const { RegionSelector } = await import("@/components/login/region-selector");
    render(<RegionSelector />);
    expect(screen.getByText("唯一区")).toBeInTheDocument();
    expect(freshStore.getState().selectedRegionId).toBe("only-1");
    expect(getRegionCookie()).toBe("only-1");
  });
});

describe("RegionSelector — mode:none", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("renders nothing in mode:none", async () => {
    vi.doMock("@/lib/cluster-config", () => ({
      clusterConfig: { mode: "none", regions: [] },
    }));
    const { RegionSelector } = await import("@/components/login/region-selector");
    const { container } = render(<RegionSelector />);
    expect(container.firstChild).toBeNull();
  });
});

describe("RegionSelector — empty regions in multi-region", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  it("renders a retry affordance when regions is empty", async () => {
    vi.doMock("@/lib/cluster-config", () => ({
      clusterConfig: { mode: "multi-region", regions: [] },
    }));
    const { RegionSelector } = await import("@/components/login/region-selector");
    render(<RegionSelector />);
    expect(screen.getByRole("button", { name: /retry|重试|region\.empty/i })).toBeInTheDocument();
  });
});
