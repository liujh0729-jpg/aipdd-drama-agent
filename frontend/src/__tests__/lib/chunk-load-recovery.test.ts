// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  installChunkLoadRecovery,
  isChunkLoadError,
  requestChunkLoadRecovery,
  resetChunkLoadRecoveryForTests,
} from "@/lib/chunk-load-recovery";

describe("chunk-load-recovery", () => {
  beforeEach(() => {
    resetChunkLoadRecoveryForTests();
  });

  it("asks the user to refresh for dynamic import failures without auto-reloading", () => {
    const result = requestChunkLoadRecovery(
      new TypeError("Failed to fetch dynamically imported module: /assets/freezone.lazy-old.js"),
    );

    expect(result).toBe("needs-user-reload");
  });

  it("keeps showing the refresh prompt for repeated dynamic import failures", () => {
    const result = requestChunkLoadRecovery(
      new TypeError("error loading dynamically imported module"),
    );

    expect(result).toBe("needs-user-reload");
  });

  it("ignores non chunk-load errors", () => {
    const result = requestChunkLoadRecovery(
      new Error("ordinary render failure"),
    );

    expect(result).toBe("ignored");
  });

  it("recognizes common browser chunk failure messages", () => {
    expect(isChunkLoadError(new Error("ChunkLoadError: Loading chunk 123 failed."))).toBe(true);
    expect(isChunkLoadError("Importing a module script failed.")).toBe(true);
    expect(isChunkLoadError(new Error("Failed to fetch dynamically imported module"))).toBe(true);
    expect(isChunkLoadError(new Error("network failed while saving canvas"))).toBe(false);
  });

  it("prevents Vite preload chunk failures from bubbling into a retry loop", () => {
    const cleanup = installChunkLoadRecovery();
    const event = new Event("vite:preloadError", { cancelable: true }) as Event & { payload?: unknown };
    event.payload = new TypeError("Failed to fetch dynamically imported module: /assets/freezone.lazy-old.js");

    const dispatched = window.dispatchEvent(event);

    expect(dispatched).toBe(false);
    cleanup();
  });

  it("keeps the app shell mounted while showing the user-refresh prompt", () => {
    const mainSource = readFileSync(resolve(process.cwd(), "src/main.tsx"), "utf8");
    const updatePromptSource = readFileSync(
      resolve(process.cwd(), "src/components/app-update-required.tsx"),
      "utf8",
    );

    expect(mainSource).not.toContain("if (updateRequired) {");
    expect(mainSource).toContain("<RouterProvider router={router} />");
    expect(mainSource).toContain("updateRequired ? <AppUpdateRequired /> : <AppUpdateAvailable />");
    expect(updatePromptSource).toContain("fixed inset-0");
  });
});
