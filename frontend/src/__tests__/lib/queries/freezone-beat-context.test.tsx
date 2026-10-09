// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const listFreezoneBeatContext = vi.fn();

vi.mock("@/api/projects", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/projects")>()),
  listFreezoneBeatContext: (...args: unknown[]) => listFreezoneBeatContext(...args),
}));

import { useFreezoneBeatContext } from "@/lib/queries/freezone";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useFreezoneBeatContext", () => {
  it("shares one request for matching project and beat context scope", async () => {
    listFreezoneBeatContext.mockResolvedValue({
      scope: { episode: 1, beat: 2 },
      episodes: [],
      assets: [],
    });

    renderHook(
      () => [
        useFreezoneBeatContext("demo", { episode: 1, beat: 2 }),
        useFreezoneBeatContext("demo", { episode: 1, beat: 2 }),
      ],
      { wrapper },
    );

    await vi.waitFor(() => expect(listFreezoneBeatContext).toHaveBeenCalledTimes(1));
    expect(listFreezoneBeatContext).toHaveBeenCalledWith("demo", {
      episode: 1,
      beat: 2,
      signal: expect.any(AbortSignal),
    });
  });
});
