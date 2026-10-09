// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFreezoneCanvases = vi.fn();
const deleteFreezoneCanvas = vi.fn();

vi.mock("@/api/canvas", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/canvas")>()),
  listFreezoneCanvases: (...args: unknown[]) => listFreezoneCanvases(...args),
  deleteFreezoneCanvas: (...args: unknown[]) => deleteFreezoneCanvas(...args),
}));

import { CanvasesTab } from "@/features/freezone/CanvasesTab";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("CanvasesTab queries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shares one canvas list request across matching tabs", async () => {
    listFreezoneCanvases.mockResolvedValue([]);

    render(
      <>
        <CanvasesTab project="demo" currentCanvasId="user_admin_demo" hasPresetLabel={false} />
        <CanvasesTab project="demo" currentCanvasId="user_admin_demo" hasPresetLabel={false} />
      </>,
      { wrapper },
    );

    await vi.waitFor(() => expect(listFreezoneCanvases).toHaveBeenCalledTimes(1));
  });
});
