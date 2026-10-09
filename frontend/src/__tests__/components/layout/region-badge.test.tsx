// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRegionStore } from "@/stores/region-store";

vi.mock("@/lib/cluster-config", () => ({
  clusterConfig: {
    mode: "multi-region",
    regions: [
      { id: "cn-1", displayName: "华东一区" },
      { id: "us-1", displayName: "美西一区" },
    ],
  },
}));

vi.mock("react-i18next", async () => {
  const actual = await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return { ...actual, useTranslation: () => ({ t: (k: string) => k, i18n: { language: "zh" } }) };
});

const switchSpy = vi.fn();
vi.mock("@/lib/region-switch", () => ({
  switchRegion: (...args: unknown[]) => switchSpy(...args),
}));

import { RegionBadge } from "@/components/layout/region-badge";

function renderBadge() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <RegionBadge />
    </QueryClientProvider>,
  );
}

describe("RegionBadge", () => {
  beforeEach(() => {
    useRegionStore.setState({ selectedRegionId: "cn-1" });
    switchSpy.mockReset();
  });

  it("renders the current region name", () => {
    renderBadge();
    expect(screen.getByText(/华东一区/)).toBeInTheDocument();
  });

  it("click opens confirm dialog", async () => {
    const user = userEvent.setup();
    renderBadge();
    await user.click(screen.getByLabelText("region.badge.label"));
    expect(screen.getByText("region.switch.confirm.title")).toBeInTheDocument();
  });

  it("stops propagation so the status bar parent click does not fire", async () => {
    const user = userEvent.setup();
    const onParentClick = vi.fn();
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <div onClick={onParentClick}>
          <RegionBadge />
        </div>
      </QueryClientProvider>,
    );
    await user.click(screen.getByLabelText("region.badge.label"));
    expect(onParentClick).not.toHaveBeenCalled();
  });
});

describe("RegionBadge — mode:none", () => {
  it("renders nothing in mode:none", async () => {
    vi.resetModules();
    vi.doMock("@/lib/cluster-config", () => ({
      clusterConfig: { mode: "none", regions: [] },
    }));
    const { RegionBadge } = await import("@/components/layout/region-badge");
    const { container } = render(<RegionBadge />);
    expect(container.firstChild).toBeNull();
  });
});
