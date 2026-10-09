// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it } from "vitest";

import {
  deriveSaveState,
  saveScopes,
  trackSave,
  useSaveStatusStore,
  writeSaveStatus,
} from "@/stores/save-status-store";

beforeEach(() => {
  useSaveStatusStore.setState({ scopes: {} });
});

describe("save-status-store (leaf state)", () => {
  it("setScopeStatus(saving) preserves prior lastSavedAt", () => {
    const when = Date.now() - 60_000;
    useSaveStatusStore.setState({
      scopes: { a: { status: "saved", lastSavedAt: when } },
    });
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    const s = useSaveStatusStore.getState().scopes["a"];
    expect(s.status).toBe("saving");
    expect(s.lastSavedAt).toBe(when);
  });

  it("setScopeStatus(saved) stamps a new lastSavedAt and clears error", () => {
    useSaveStatusStore.setState({
      scopes: { a: { status: "error", lastSavedAt: null, error: "boom" } },
    });
    const before = Date.now();
    useSaveStatusStore.getState().setScopeStatus("a", "saved");
    const s = useSaveStatusStore.getState().scopes["a"];
    expect(s.status).toBe("saved");
    expect(s.error).toBeUndefined();
    expect(s.lastSavedAt!).toBeGreaterThanOrEqual(before);
  });

  it("setScopeStatus(error) preserves prior lastSavedAt and records message", () => {
    const when = Date.now() - 60_000;
    useSaveStatusStore.setState({
      scopes: { a: { status: "saved", lastSavedAt: when } },
    });
    useSaveStatusStore.getState().setScopeStatus("a", "error", "nope");
    const s = useSaveStatusStore.getState().scopes["a"];
    expect(s.status).toBe("error");
    expect(s.lastSavedAt).toBe(when);
    expect(s.error).toBe("nope");
  });

  it("identity write is a no-op (does not create a new state object)", () => {
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    const before = useSaveStatusStore.getState().scopes;
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    expect(useSaveStatusStore.getState().scopes).toBe(before);
  });

  it("writeSaveStatus is an imperative alias that delegates to setScopeStatus", () => {
    writeSaveStatus("a", "saving");
    expect(useSaveStatusStore.getState().scopes["a"].status).toBe("saving");
  });

  it("reset removes the scope entry", () => {
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    useSaveStatusStore.getState().reset("a");
    expect(useSaveStatusStore.getState().scopes["a"]).toBeUndefined();
  });
});

describe("deriveSaveState (parent aggregation)", () => {
  it("returns idle when no own state and no children", () => {
    expect(deriveSaveState({}, "p").status).toBe("idle");
  });

  it("returns own leaf state when no children exist", () => {
    const state = { status: "saved" as const, lastSavedAt: 100 };
    expect(deriveSaveState({ p: state }, "p")).toEqual(state);
  });

  it("any child in error → parent is error (regression: sibling race)", () => {
    const scopes = {
      "p.a": { status: "error" as const, lastSavedAt: null, error: "boom" },
      "p.b": { status: "saved" as const, lastSavedAt: 200 },
    };
    const parent = deriveSaveState(scopes, "p");
    expect(parent.status).toBe("error");
    expect(parent.error).toBe("boom");
  });

  it("any child saving → parent is saving (when no errors)", () => {
    const scopes = {
      "p.a": { status: "saving" as const, lastSavedAt: null },
      "p.b": { status: "saved" as const, lastSavedAt: 200 },
    };
    expect(deriveSaveState(scopes, "p").status).toBe("saving");
  });

  it("all children saved → parent saved at max child timestamp", () => {
    const scopes = {
      "p.a": { status: "saved" as const, lastSavedAt: 100 },
      "p.b": { status: "saved" as const, lastSavedAt: 300 },
    };
    const parent = deriveSaveState(scopes, "p");
    expect(parent.status).toBe("saved");
    expect(parent.lastSavedAt).toBe(300);
  });

  it("sibling success does NOT clear another sibling's error", () => {
    // Critical regression: previously, event-push aggregation caused child B's
    // saved event to overwrite the parent's error, masking child A's failure.
    const scopes: Record<string, ReturnType<typeof deriveSaveState>> = {
      "p.a": { status: "error", lastSavedAt: null, error: "A failed" },
    };
    expect(deriveSaveState(scopes, "p").status).toBe("error");
    scopes["p.b"] = { status: "saved", lastSavedAt: Date.now() };
    expect(deriveSaveState(scopes, "p").status).toBe("error");
  });

  it("sibling success does NOT mask another sibling's in-flight save", () => {
    const scopes = {
      "p.a": { status: "saving" as const, lastSavedAt: null },
      "p.b": { status: "saved" as const, lastSavedAt: Date.now() },
    };
    expect(deriveSaveState(scopes, "p").status).toBe("saving");
  });

  it("prefix match is exact (does not absorb unrelated scopes)", () => {
    const scopes = {
      "p": { status: "saved" as const, lastSavedAt: 100 },
      "prod.a": { status: "error" as const, lastSavedAt: null, error: "x" },
    };
    expect(deriveSaveState(scopes, "p").status).toBe("saved");
  });
});

describe("trackSave", () => {
  it("transitions saving → saved when the operation resolves", async () => {
    let resolveOp!: (v: number) => void;
    const op = new Promise<number>((r) => {
      resolveOp = r;
    });
    const promise = trackSave("a", () => op);
    expect(useSaveStatusStore.getState().scopes["a"].status).toBe("saving");
    resolveOp(42);
    await expect(promise).resolves.toBe(42);
    expect(useSaveStatusStore.getState().scopes["a"].status).toBe("saved");
  });

  it("transitions saving → error and re-throws when the operation rejects", async () => {
    const err = new Error("boom");
    await expect(trackSave("a", () => Promise.reject(err))).rejects.toBe(err);
    const s = useSaveStatusStore.getState().scopes["a"];
    expect(s.status).toBe("error");
    expect(s.error).toBe("boom");
  });

  it("survives the caller going away — simulated by unrooted promise", async () => {
    // This models the unmount-flush path: trackSave's promise chain lives in
    // the JS engine, not in a React observer. A later resolution still
    // updates the store.
    let resolveOp!: () => void;
    const op = new Promise<void>((r) => {
      resolveOp = r;
    });
    // Fire and forget — no reference retained to the returned promise.
    void trackSave("a", () => op).catch(() => {});
    expect(useSaveStatusStore.getState().scopes["a"].status).toBe("saving");
    resolveOp();
    // Flush microtasks.
    await Promise.resolve();
    await Promise.resolve();
    expect(useSaveStatusStore.getState().scopes["a"].status).toBe("saved");
  });
});

describe("saveScopes helpers", () => {
  it("builds episode-page and nested scopes", () => {
    const page = saveScopes.episodePage("demo", 5);
    expect(saveScopes.episodeTitle("demo", 5)).toBe(`${page}.title`);
    expect(saveScopes.beatText("demo", 5, 3)).toBe(`${page}.beat.3.text`);
    expect(saveScopes.episodeSource("demo", 5)).toBe(`${page}.source`);
  });

  it("url-encodes path segments to avoid delimiter collisions", () => {
    // Character names / project ids with dots or spaces should not leak.
    const scope = saveScopes.characterDetails("proj", "陈.锋");
    expect(scope.endsWith(".details")).toBe(true);
    // The encoded name must not contain a raw "." — otherwise the prefix
    // match in deriveSaveState would treat the dotted halves as distinct
    // scope segments.
    const innerEncoded = scope.slice("characters.proj.c.".length).replace(".details", "");
    expect(innerEncoded).not.toContain(".");
  });

  it("dotted project ids do not cause prefix-match collisions", () => {
    // Regression: saveScopes.charactersPage("proj") must not be a prefix of
    // saveScopes.charactersPage("proj.a") after encoding.
    const a = saveScopes.charactersPage("proj");
    const b = saveScopes.charactersPage("proj.a");
    expect(b.startsWith(a + ".")).toBe(false);
  });
});
