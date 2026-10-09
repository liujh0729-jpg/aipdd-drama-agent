// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface DirectorWorldSourceTransform {
  xOffset: number;
  yOffset: number;
  zOffset: number;
  yawDeg: number;
  pitchDeg: number;
  rollDeg: number;
  scale: number;
}

export const DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM: DirectorWorldSourceTransform = {
  xOffset: 0,
  yOffset: 0,
  zOffset: 0,
  yawDeg: 0,
  pitchDeg: 0,
  rollDeg: 0,
  scale: 1,
};

export interface LegacyDirectorWorldSnapshot {
  splatYOffset?: unknown;
  activeSourceId?: unknown;
}

export interface MigratedSourceTransform {
  sourceId: string | null;
  transform: DirectorWorldSourceTransform;
}

function finiteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function normalizeDirectorWorldSourceTransform(
  value: Partial<DirectorWorldSourceTransform> | null | undefined,
): DirectorWorldSourceTransform {
  const raw = value ?? {};
  const scale = finiteNumber(raw.scale, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.scale);
  return {
    xOffset: finiteNumber(raw.xOffset, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.xOffset),
    yOffset: finiteNumber(raw.yOffset, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.yOffset),
    zOffset: finiteNumber(raw.zOffset, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.zOffset),
    yawDeg: finiteNumber(raw.yawDeg, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.yawDeg),
    pitchDeg: finiteNumber(raw.pitchDeg, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.pitchDeg),
    rollDeg: finiteNumber(raw.rollDeg, DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.rollDeg),
    scale: scale > 0 ? scale : DEFAULT_DIRECTOR_WORLD_SOURCE_TRANSFORM.scale,
  };
}

export function constrainSourceTransformForType(
  value: Partial<DirectorWorldSourceTransform> | null | undefined,
  _sourceType: "sog" | "pano360" | "mesh" | undefined,
): DirectorWorldSourceTransform {
  return normalizeDirectorWorldSourceTransform(value);
}

export function sourceTransformFromLegacyWorld(
  world: LegacyDirectorWorldSnapshot | null | undefined,
): MigratedSourceTransform | null {
  if (!world || typeof world.splatYOffset !== "number" || !Number.isFinite(world.splatYOffset)) {
    return null;
  }
  return {
    sourceId: typeof world.activeSourceId === "string" && world.activeSourceId.trim()
      ? world.activeSourceId
      : null,
    transform: normalizeDirectorWorldSourceTransform({
      yOffset: world.splatYOffset,
    }),
  };
}

export interface SogPivotTransformInput {
  sceneCenter: [number, number, number];
  transform: Partial<DirectorWorldSourceTransform> | null | undefined;
}

export interface SogPivotTransform {
  pivotPosition: [number, number, number];
  pivotEulerDeg: [number, number, number];
  pivotScale: number;
  splatLocalPosition: [number, number, number];
}

export function buildSogPivotTransform(input: SogPivotTransformInput): SogPivotTransform {
  const transform = normalizeDirectorWorldSourceTransform(input.transform);
  const [cx, cy, cz] = input.sceneCenter;
  return {
    pivotPosition: [
      cx + transform.xOffset,
      cy + transform.yOffset,
      cz + transform.zOffset,
    ],
    pivotEulerDeg: [transform.pitchDeg, transform.yawDeg, transform.rollDeg],
    pivotScale: transform.scale,
    splatLocalPosition: [-cx, -cy, -cz],
  };
}
