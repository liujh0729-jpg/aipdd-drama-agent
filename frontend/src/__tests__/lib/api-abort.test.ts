// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { api } from "@/lib/api";
import { regionAbortController, resetRegionAbortController } from "@/lib/region-abort";
import { server } from "@/__mocks__/msw/server";

// The global MSW server is started by `src/__tests__/setup.ts`. Reusing it
// here keeps a single `setupServer` instance in-process, avoiding the
// dual-instance quirk described in that setup file.
beforeEach(() => {
  resetRegionAbortController();
});

describe("api abort integration", () => {
  it("aborts an in-flight ky request when regionAbortController fires", async () => {
    server.use(
      http.get("http://localhost/api/v1/slow", async () => {
        await new Promise((r) => setTimeout(r, 1000));
        return HttpResponse.json({ ok: true });
      }),
    );
    // Pass a URL (not a string): ky's `prefix: "/"` only mutates string
    // inputs, so wrapping in URL sidesteps the `/http://...` concatenation
    // while still giving MSW an absolute URL to match against.
    const promise = api.get(new URL("http://localhost/api/v1/slow")).json();
    queueMicrotask(() => regionAbortController().abort());
    await expect(promise).rejects.toThrow();
  });

  it("respects a caller-supplied signal alongside the region signal", async () => {
    server.use(
      http.get("http://localhost/api/v1/caller-slow", async () => {
        await new Promise((r) => setTimeout(r, 1000));
        return HttpResponse.json({ ok: true });
      }),
    );
    const caller = new AbortController();
    const promise = api
      .get(new URL("/api/v1/caller-slow", "http://localhost/"), { signal: caller.signal })
      .json();
    queueMicrotask(() => caller.abort());
    await expect(promise).rejects.toThrow();
  });
});
