// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ensureAuthenticatedForAppRoute, isLocalAuthMode } from "@/lib/auth-mode";
import { useAuthStore } from "@/stores/auth-store";

const runtimeState = vi.hoisted(() => ({ authRequired: true }));

vi.mock("@/lib/runtime-config", () => ({
  authRequired: () => runtimeState.authRequired,
}));

beforeEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  runtimeState.authRequired = true;
  useAuthStore.getState().reset();
  localStorage.clear();
});

describe("auth mode", () => {
  it("defaults to cookie mode", () => {
    vi.stubEnv("VITE_AUTH_MODE", "cookie");

    expect(isLocalAuthMode()).toBe(false);
  });

  it("does not authenticate an empty auth-required runtime session", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "cookie");
    runtimeState.authRequired = true;

    expect(await ensureAuthenticatedForAppRoute()).toBe(false);
  });

  it("uses /auth/me to establish a no-auth runtime session without showing login", async () => {
    vi.stubEnv("VITE_AUTH_MODE", "cookie");
    runtimeState.authRequired = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          ok: true,
          data: { username: "local", role: "owner", credit_balance: 0 },
        }),
      })),
    );

    await expect(ensureAuthenticatedForAppRoute()).resolves.toBe(true);
    expect(useAuthStore.getState().username).toBe("local");
    expect(useAuthStore.getState().role).toBe("owner");
  });
});
