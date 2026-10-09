// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import * as pc from 'playcanvas';

// 飞行 + 轨道相机：左键拖拽看向（lookCamera），右键拖拽平移（panCamera），
// 滚轮缩放，WASD/QE 移动。模型沿用 BuilderGPT 9024 viewer 的 SuperSplat 控制。

const SUPER_SPLAT_CONTROLS = {
  minZoom: 1e-6,
  maxZoom: 10,
  orbitSensitivity: 0.3,
  zoomSensitivity: 0.4,
};

const DIRECTOR_STAGE_DEFAULT_FLY_SPEED = 0.04;
const DIRECTOR_STAGE_UNITS_PER_SECOND = DIRECTOR_STAGE_DEFAULT_FLY_SPEED * 60;
const PLAYCANVAS_3GS_RADIUS_SPEED_FACTOR = 0.03 * 2.0;

export interface FlyCameraControllerOptions {
  app: pc.AppBase;
  camera: pc.Entity;
  canvas: HTMLCanvasElement;
  initialAzim: number;
  initialElev: number;
  initialZoom: number;
}

export interface FlyCameraState {
  azim: number;
  elev: number;
  distance: number;
  focalPoint: [number, number, number];
}

export interface FlyCameraController {
  setSceneRadius: (radius: number) => void;
  setSpeedScale: (scale: number) => void;
  resetToInitial: () => void;
  // 把 orbit focalPoint 平滑插值到 target；durationMs 默认 300ms（ease-out cubic）。
  // 用户拖镜头 / 按 WASD/QE 会立即取消。
  lookAtTarget: (target: pc.Vec3, durationMs?: number) => void;
  // Camera preset 用：把 focalPoint = target，相机摆到 target + offset，自动计算
  // azim/elev/distance 并 sync。对应 BuilderGPT 的 setCameraByOffset。
  setCameraByOffset: (target: pc.Vec3, offset: pc.Vec3) => void;
  // 保存/恢复：azim/elev/distance + focalPoint，序列化进 scene snapshot。
  getCameraState: () => FlyCameraState;
  setCameraState: (state: unknown) => void;
  setInputEnabled: (enabled: boolean) => void;
  tick: () => void;
  destroy: () => void;
}

function finiteNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function tuple3(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length < 3) return null;
  const next = value.slice(0, 3).map((item) => Number(item));
  return next.every((item) => Number.isFinite(item))
    ? [next[0], next[1], next[2]]
    : null;
}

export function normalizeFlyCameraState(
  state: unknown,
  fallback: FlyCameraState,
): FlyCameraState {
  const record = state && typeof state === 'object'
    ? state as Record<string, unknown>
    : {};
  return {
    azim: finiteNumber(record.azim, fallback.azim),
    elev: finiteNumber(record.elev, fallback.elev),
    distance: finiteNumber(record.distance, fallback.distance),
    focalPoint: tuple3(record.focalPoint) ?? fallback.focalPoint,
  };
}

export function isPointerButtonPressed(buttons: number, button: number): boolean {
  const mask = button === 0 ? 1 : button === 1 ? 4 : button === 2 ? 2 : 0;
  return mask !== 0 && (buttons & mask) !== 0;
}

export function createFlyCameraController(options: FlyCameraControllerOptions): FlyCameraController {
  const { app, camera, canvas, initialAzim, initialElev, initialZoom } = options;

  let azim = initialAzim;
  let elev = initialElev;
  let distance = initialZoom;
  let sceneRadius = 1;
  let speedScale = 1;
  let lastFrameMs = performance.now();

  const focalPoint = new pc.Vec3(0, 0.5, 0);
  const tmpForward = new pc.Vec3();
  const tmpPos = new pc.Vec3();
  const tmpA = new pc.Vec3();
  const tmpB = new pc.Vec3();
  const tmpC = new pc.Vec3();
  const worldA = new pc.Vec3();
  const worldB = new pc.Vec3();

  const keysDown = new Set<string>();
  let inputEnabled = true;
  let dragButton = -1;
  let dragging = false;
  let lastClientX = 0;
  let lastClientY = 0;
  let activePointerId: number | null = null;
  let cursorBeforeLeftDrag: string | null = null;

  let lookAtAnim: { from: pc.Vec3; to: pc.Vec3; t0: number; durationMs: number } | null = null;
  function cancelLookAtAnim() {
    lookAtAnim = null;
  }

  function modDegrees(value: number): number {
    return ((value % 360) + 360) % 360;
  }

  function fovFactor(): number {
    return Math.sin((camera.camera?.fov ?? 75) * pc.math.DEG_TO_RAD * 0.5);
  }

  function actualDistance(): number {
    return distance * sceneRadius / Math.max(1e-6, fovFactor());
  }

  function calcForwardVec(out: pc.Vec3): pc.Vec3 {
    const ex = elev * pc.math.DEG_TO_RAD;
    const ey = azim * pc.math.DEG_TO_RAD;
    const s1 = Math.sin(-ex);
    const c1 = Math.cos(-ex);
    const s2 = Math.sin(-ey);
    const c2 = Math.cos(-ey);
    return out.set(-c1 * s2, s1, c1 * c2);
  }

  function sync() {
    calcForwardVec(tmpForward);
    tmpPos.copy(tmpForward).mulScalar(actualDistance()).add(focalPoint);
    camera.setPosition(tmpPos);
    camera.setEulerAngles(elev, azim, 0);
  }
  sync();

  function lookCamera(dx: number, dy: number) {
    const cameraPosition = camera.getPosition().clone();
    const dist = actualDistance();
    azim = modDegrees(azim - dx * SUPER_SPLAT_CONTROLS.orbitSensitivity);
    elev = pc.math.clamp(elev - dy * SUPER_SPLAT_CONTROLS.orbitSensitivity, -90, 90);
    calcForwardVec(tmpForward);
    focalPoint.copy(cameraPosition).sub(tmpForward.mulScalar(dist));
    sync();
  }

  function panCamera(clientX: number, clientY: number, dx: number, dy: number) {
    if (!camera.camera) return;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const dist = actualDistance();
    camera.camera.screenToWorld(x, y, dist, worldA);
    camera.camera.screenToWorld(x - dx, y - dy, dist, worldB);
    tmpA.sub2(worldB, worldA);
    focalPoint.add(tmpA);
    sync();
  }

  function zoomCamera(amount: number) {
    const scaled = (distance * 0.999 + 0.001) * amount * SUPER_SPLAT_CONTROLS.zoomSensitivity;
    distance = pc.math.clamp(
      distance - scaled,
      SUPER_SPLAT_CONTROLS.minZoom,
      SUPER_SPLAT_CONTROLS.maxZoom,
    );
    sync();
  }

  function cameraAxes(): { forward: pc.Vec3; right: pc.Vec3; up: pc.Vec3 } {
    return {
      forward: tmpA.copy(camera.forward),
      right: tmpB.copy(camera.right),
      up: tmpC.copy(camera.up),
    };
  }

  function moveByKeys(dtSeconds: number) {
    const { forward, right, up } = cameraAxes();
    const speedMod = keysDown.has('shift') ? 4 : keysDown.has('alt') ? 0.1 : 1;
    const radiusScaledSpeed = Math.max(1, sceneRadius) * PLAYCANVAS_3GS_RADIUS_SPEED_FACTOR;
    const ups = Math.max(DIRECTOR_STAGE_UNITS_PER_SECOND, radiusScaledSpeed);
    const speed = ups * speedScale * speedMod * dtSeconds;
    const delta = new pc.Vec3();
    forward.y = 0;
    if (forward.lengthSq() > 0) forward.normalize();
    if (keysDown.has('w')) delta.add(forward);
    if (keysDown.has('s')) delta.sub(forward);
    if (keysDown.has('d')) delta.add(right);
    if (keysDown.has('a')) delta.sub(right);
    if (keysDown.has('q')) delta.sub(up);
    if (keysDown.has('e')) delta.add(up);
    if (delta.lengthSq() <= 0) return;
    cancelLookAtAnim();
    focalPoint.add(delta.normalize().mulScalar(speed));
    sync();
  }

  function updateLookAtAnim() {
    if (!lookAtAnim) return;
    const now = performance.now();
    const t = Math.min(1, (now - lookAtAnim.t0) / lookAtAnim.durationMs);
    const e = 1 - Math.pow(1 - t, 3); // ease-out cubic
    focalPoint.lerp(lookAtAnim.from, lookAtAnim.to, e);
    sync();
    if (t >= 1) lookAtAnim = null;
  }

  const restoreLeftDragCursor = () => {
    if (cursorBeforeLeftDrag === null) return;
    canvas.style.cursor = cursorBeforeLeftDrag;
    cursorBeforeLeftDrag = null;
  };

  // Mouse handlers — listen on canvas for drag start, document for move/up.
  const onPointerDown = (event: PointerEvent) => {
    if (!inputEnabled) return;
    if (event.target !== canvas) return;
    canvas.focus();
    dragging = true;
    dragButton = event.button;
    activePointerId = event.pointerId;
    lastClientX = event.clientX;
    lastClientY = event.clientY;
    if (event.button === 0) {
      cursorBeforeLeftDrag = canvas.style.cursor;
      canvas.style.cursor = 'none';
    }
    canvas.setPointerCapture(event.pointerId);
    cancelLookAtAnim();
  };

  const endDrag = (pointerId?: number) => {
    restoreLeftDragCursor();
    dragging = false;
    dragButton = -1;
    activePointerId = null;
    if (pointerId !== undefined) {
      try { canvas.releasePointerCapture(pointerId); } catch { /* ignore */ }
    }
  };

  const onPointerMove = (event: PointerEvent) => {
    if (!inputEnabled) return;
    if (!dragging) return;
    if (activePointerId !== null && event.pointerId !== activePointerId) return;
    if (!isPointerButtonPressed(event.buttons, dragButton)) {
      endDrag(event.pointerId);
      return;
    }
    const dx = event.clientX - lastClientX;
    const dy = event.clientY - lastClientY;
    lastClientX = event.clientX;
    lastClientY = event.clientY;
    if (dragButton === 0) {
      lookCamera(dx, dy);
    } else if (dragButton === 1) {
      zoomCamera(dy * -0.02);
    } else if (dragButton === 2) {
      if (event.altKey || event.metaKey) zoomCamera(dy * -0.02);
      else if (event.shiftKey || event.ctrlKey) lookCamera(dx, dy);
      else panCamera(event.clientX, event.clientY, dx, dy);
    }
  };

  const onPointerUp = (event: PointerEvent) => {
    if (!dragging) return;
    endDrag(event.pointerId);
  };

  const onPointerCancel = (event: PointerEvent) => {
    if (!dragging) return;
    endDrag(event.pointerId);
  };

  const onLostPointerCapture = () => {
    if (!dragging) return;
    endDrag();
  };

  const onContextMenu = (event: MouseEvent) => {
    if (event.target === canvas) event.preventDefault();
  };

  const onWheel = (event: WheelEvent) => {
    if (!inputEnabled) return;
    if (event.target !== canvas) return;
    event.preventDefault();
    zoomCamera(event.deltaY * -0.0015);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (!inputEnabled) return;
    if (document.activeElement !== canvas) return;
    const movementKey = {
      KeyW: 'w',
      KeyA: 'a',
      KeyS: 's',
      KeyD: 'd',
      KeyQ: 'q',
      KeyE: 'e',
      ShiftLeft: 'shift',
      ShiftRight: 'shift',
      AltLeft: 'alt',
      AltRight: 'alt',
    }[event.code];
    if (movementKey) keysDown.add(movementKey);
  };
  const onKeyUp = (event: KeyboardEvent) => {
    if (!inputEnabled) return;
    if (document.activeElement !== canvas) return;
    const movementKey = {
      KeyW: 'w',
      KeyA: 'a',
      KeyS: 's',
      KeyD: 'd',
      KeyQ: 'q',
      KeyE: 'e',
      ShiftLeft: 'shift',
      ShiftRight: 'shift',
      AltLeft: 'alt',
      AltRight: 'alt',
    }[event.code];
    if (movementKey) keysDown.delete(movementKey);
  };
  const onBlur = () => keysDown.clear();

  canvas.addEventListener('pointerdown', onPointerDown);
  document.addEventListener('pointermove', onPointerMove);
  document.addEventListener('pointerup', onPointerUp);
  document.addEventListener('pointercancel', onPointerCancel);
  canvas.addEventListener('lostpointercapture', onLostPointerCapture);
  canvas.addEventListener('contextmenu', onContextMenu);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onBlur);

  function tick() {
    const now = performance.now();
    const dt = Math.min(0.1, (now - lastFrameMs) / 1000);
    lastFrameMs = now;
    if (inputEnabled) moveByKeys(dt);
    updateLookAtAnim();
  }

  return {
    setSceneRadius(radius: number) {
      sceneRadius = Math.max(1e-3, radius);
      sync();
    },
    setSpeedScale(scale: number) {
      speedScale = Math.max(0.01, scale);
    },
    resetToInitial() {
      azim = initialAzim;
      elev = initialElev;
      distance = initialZoom;
      focalPoint.set(0, 0.5, 0);
      cancelLookAtAnim();
      sync();
    },
    lookAtTarget(target: pc.Vec3, durationMs = 300) {
      lookAtAnim = {
        from: focalPoint.clone(),
        to: target.clone(),
        t0: performance.now(),
        durationMs: Math.max(1, durationMs),
      };
    },
    setCameraByOffset(target: pc.Vec3, offset: pc.Vec3) {
      cancelLookAtAnim();
      focalPoint.copy(target);
      const length = Math.hypot(offset.x, offset.y, offset.z);
      if (length < 1e-6) {
        // offset = 0 没意义，保持现有 azim/elev/distance，只移焦点。
        sync();
        return;
      }
      // 反推 azim/elev/distance：camera = focalPoint + forward * actualDistance
      // forward = (cos(ex)*sin(ey), -sin(ex), cos(ex)*cos(ey))
      const dirY = offset.y / length;
      const elevRad = -Math.asin(Math.max(-1, Math.min(1, dirY)));
      const azimRad = Math.atan2(offset.x, offset.z);
      azim = modDegrees(azimRad * pc.math.RAD_TO_DEG);
      elev = pc.math.clamp(elevRad * pc.math.RAD_TO_DEG, -90, 90);
      // actualDistance = distance * sceneRadius / fovFactor，所以 distance = length * fovFactor / sceneRadius
      const dist = length * Math.max(1e-6, fovFactor()) / Math.max(1e-6, sceneRadius);
      distance = pc.math.clamp(dist, SUPER_SPLAT_CONTROLS.minZoom, SUPER_SPLAT_CONTROLS.maxZoom);
      sync();
    },
    getCameraState() {
      return {
        azim,
        elev,
        distance,
        focalPoint: [focalPoint.x, focalPoint.y, focalPoint.z] as [number, number, number],
      };
    },
    setCameraState(state) {
      cancelLookAtAnim();
      const normalized = normalizeFlyCameraState(state, {
        azim,
        elev,
        distance,
        focalPoint: [focalPoint.x, focalPoint.y, focalPoint.z],
      });
      azim = normalized.azim;
      elev = pc.math.clamp(normalized.elev, -90, 90);
      distance = pc.math.clamp(normalized.distance, SUPER_SPLAT_CONTROLS.minZoom, SUPER_SPLAT_CONTROLS.maxZoom);
      focalPoint.set(normalized.focalPoint[0], normalized.focalPoint[1], normalized.focalPoint[2]);
      sync();
    },
    setInputEnabled(enabled: boolean) {
      inputEnabled = enabled;
      if (enabled) return;
      keysDown.clear();
      cancelLookAtAnim();
      endDrag(activePointerId ?? undefined);
    },
    tick,
    destroy() {
      restoreLeftDragCursor();
      canvas.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      document.removeEventListener('pointercancel', onPointerCancel);
      canvas.removeEventListener('lostpointercapture', onLostPointerCapture);
      canvas.removeEventListener('contextmenu', onContextMenu);
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
      void app;
    },
  };
}
