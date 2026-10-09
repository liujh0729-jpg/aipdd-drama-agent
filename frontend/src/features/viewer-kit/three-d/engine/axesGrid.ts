// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import * as pc from 'playcanvas';

export interface AxesGridDrawerOptions {
  app: pc.AppBase;
  camera: pc.Entity;
}

export interface AxesGridDrawer {
  setVisible: (visible: boolean) => void;
  isVisible: () => boolean;
  tick: () => void;
}

const GRID_LINE_COLOR = new pc.Color(0.45, 0.45, 0.5, 0.45);
const GRID_MAJOR_LINE_COLOR = new pc.Color(0.7, 0.7, 0.8, 0.7);
const AXIS_X = new pc.Color(1, 0.32, 0.32);
const AXIS_X_DIM = new pc.Color(0.55, 0.18, 0.18);
const AXIS_Y = new pc.Color(0.35, 0.95, 0.45);
const AXIS_Y_DIM = new pc.Color(0.18, 0.5, 0.22);
const AXIS_Z = new pc.Color(0.38, 0.62, 1);
const AXIS_Z_DIM = new pc.Color(0.18, 0.3, 0.55);

export interface AxesGridWindowInput {
  cameraPosition: { x: number; y: number; z: number };
  cameraFarClip?: number | null;
}

export interface AxesGridWindow {
  centerX: number;
  centerZ: number;
  extent: number;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

function roundToStep(value: number, step: number): number {
  return Math.round(value / step) * step;
}

export function computeAxesGridWindow(input: AxesGridWindowInput): AxesGridWindow {
  const farClip = Number.isFinite(input.cameraFarClip ?? NaN)
    ? Math.max(40, Number(input.cameraFarClip))
    : 1000;
  const cameraHeight = Math.abs(input.cameraPosition.y);
  const extent = Math.ceil(Math.max(32, Math.min(farClip * 0.35, cameraHeight * 3 + 40)));
  const centerX = roundToStep(input.cameraPosition.x, 5);
  const centerZ = roundToStep(input.cameraPosition.z, 5);
  return {
    centerX,
    centerZ,
    extent,
    minX: centerX - extent,
    maxX: centerX + extent,
    minZ: centerZ - extent,
    maxZ: centerZ + extent,
  };
}

export function createAxesGridDrawer(options: AxesGridDrawerOptions): AxesGridDrawer {
  const { app, camera } = options;
  let visible = true;

  function tick() {
    if (!visible) return;
    const y = 0;
    const cameraPosition = camera.getPosition();
    const cameraComponent = camera.camera as { farClip?: number } | undefined;
    const grid = computeAxesGridWindow({
      cameraPosition,
      cameraFarClip: cameraComponent?.farClip,
    });
    const extent = grid.extent;
    const axisLength = Math.max(2, Math.min(12, extent * 0.65));
    const origin = new pc.Vec3(0, y, 0);

    for (let x = grid.minX; x <= grid.maxX; x += 1) {
      const isMajor = x === 0 || x % 5 === 0;
      const color = isMajor ? GRID_MAJOR_LINE_COLOR : GRID_LINE_COLOR;
      app.drawLine(new pc.Vec3(x, y, grid.minZ), new pc.Vec3(x, y, grid.maxZ), color, false);
    }
    for (let z = grid.minZ; z <= grid.maxZ; z += 1) {
      const isMajor = z === 0 || z % 5 === 0;
      const color = isMajor ? GRID_MAJOR_LINE_COLOR : GRID_LINE_COLOR;
      app.drawLine(new pc.Vec3(grid.minX, y, z), new pc.Vec3(grid.maxX, y, z), color, false);
    }

    app.drawLine(origin, new pc.Vec3(axisLength, y, 0), AXIS_X, false);
    app.drawLine(origin, new pc.Vec3(-axisLength * 0.35, y, 0), AXIS_X_DIM, false);
    app.drawLine(origin, new pc.Vec3(0, y + axisLength * 0.75, 0), AXIS_Y, false);
    app.drawLine(origin, new pc.Vec3(0, y - axisLength * 0.25, 0), AXIS_Y_DIM, false);
    app.drawLine(origin, new pc.Vec3(0, y, axisLength), AXIS_Z, false);
    app.drawLine(origin, new pc.Vec3(0, y, -axisLength * 0.35), AXIS_Z_DIM, false);
  }

  return {
    setVisible(next: boolean) {
      visible = Boolean(next);
    },
    isVisible() {
      return visible;
    },
    tick,
  };
}
