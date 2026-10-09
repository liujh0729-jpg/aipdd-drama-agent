// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  isPresetManagedNode,
  isPresetManagedEdge,
  isSystemManagedNodeData,
} from "@/features/canvas/domain/mainlineNodeFlags";

describe("isSystemManagedNodeData", () => {
  it("treats preset or projection nodes as system-managed", () => {
    expect(isSystemManagedNodeData({ preset_managed: true })).toBe(true);
    expect(isSystemManagedNodeData({ projection_key: "beat:1:4" })).toBe(true);
  });

  it("lets user_spawned override stale projection flags", () => {
    expect(
      isSystemManagedNodeData({
        user_spawned: true,
        preset_managed: true,
        projection_key: "beat:1:4",
      }),
    ).toBe(false);
  });

  it("does not grant system ownership to ordinary nodes", () => {
    expect(isSystemManagedNodeData({})).toBe(false);
    expect(isSystemManagedNodeData({ projection_key: "" })).toBe(false);
  });
});

describe("isPresetManagedNode", () => {
  it("does not lock no-reference sentinel nodes", () => {
    expect(
      isPresetManagedNode({
        id: "no_prop",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          label: "__NO_PROP__",
          preset_managed: true,
          projection_key: "beat:1:4:prop:__NO_PROP__",
        },
      }),
    ).toBe(false);
    expect(
      isPresetManagedNode({
        id: "no_character",
        type: "uploadNode",
        position: { x: 0, y: 0 },
        data: {
          label: "__NO_CHARACTER__",
          preset_managed: true,
        },
      }),
    ).toBe(false);
  });
});

describe("isPresetManagedEdge", () => {
  it("treats preset or projection edges as system-managed", () => {
    expect(
      isPresetManagedEdge({
        id: "e1",
        source: "a",
        target: "b",
        data: { preset_managed: true },
      }),
    ).toBe(true);
    expect(
      isPresetManagedEdge({
        id: "e2",
        source: "a",
        target: "b",
        data: { projection_key: "beat:1:4" },
      }),
    ).toBe(true);
  });

  it("lets user_spawned override stale projection edge flags", () => {
    expect(
      isPresetManagedEdge({
        id: "e1",
        source: "a",
        target: "b",
        data: {
          user_spawned: true,
          preset_managed: true,
          projection_key: "beat:1:4",
        },
      }),
    ).toBe(false);
  });

  it("does not lock no-reference sentinel edges", () => {
    expect(
      isPresetManagedEdge({
        id: "e-no-prop",
        source: "no_prop",
        target: "skill",
        targetHandle: "prop:__NO_PROP__",
        data: {
          preset_managed: true,
          role: "prop",
          reference_target: { kind: "prop", prop_id: "__NO_PROP__" },
        },
      }),
    ).toBe(false);
    expect(
      isPresetManagedEdge({
        id: "e-no-character",
        source: "no_character",
        target: "skill",
        targetHandle: "identity:__NO_CHARACTER__",
        data: {
          projection_key: "beat:1:4:identity:__NO_CHARACTER__",
          role: "identity",
          reference_target: { kind: "identity", identity_id: "__NO_CHARACTER__" },
        },
      }),
    ).toBe(false);
  });
});
