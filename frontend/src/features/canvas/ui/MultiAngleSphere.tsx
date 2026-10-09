// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useMemo, useRef } from 'react';

import './multi-angle-sphere.css';

export interface MultiAngleSphereProps {
  /** 0..360，0 正面 / 90 右 / 180 背面 / 270 左。 */
  horizontalDeg: number;
  /** -90..90，正＝俯视 / 负＝仰视。 */
  verticalDeg: number;
  /** 中心参考图相对尺寸（景别越近越大）。 */
  imageScale: number;
  imageSource: string;
  onAngleChange: (next: { horizontalDeg: number; verticalDeg: number }) => void;
}

// 球面半径（px）；场景按 ~200px 见方设计，给方向键留出四周空间。
const RADIUS = 78;
// 经线：每 15° 一条竖直大圆。
const MERIDIAN_STEP = 15;
const MERIDIAN_COUNT = 180 / MERIDIAN_STEP;
// 纬线：南北各 4 圈。
const PARALLEL_LATITUDES = [15, 30, 45, 60];
// 拖动灵敏度（px → 度）。
const DRAG_SENSITIVITY = 0.6;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function ChevronUp() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M8 14L11.65 10.35a.5.5 0 0 1 .7 0L16 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function ChevronDownIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M8 10L11.65 13.65a.5.5 0 0 0 .7 0L16 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M13.8 16L10.15 12.35a.5.5 0 0 1 0-.7L13.8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M10 16L13.65 12.35a.5.5 0 0 0 0-.7L10 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * libtv 风格的 3D 线框球角度调节区:CSS 3D 拼出的经纬线地球 + 沿球面环绕的立体小
 * 相机 + 中心朝向观察者的参考图 + 四向微调按钮。拖动改变方位/俯仰角。
 *
 * verticalDeg → 球的 rotateX（俯仰），horizontalDeg → rotateY（方位）。相机挂在同一
 * 支点里、沿 +Z 推到球面 `translateZ(RADIUS)`，因此跟着支点一起绕中心环绕。
 */
export function MultiAngleSphere({
  horizontalDeg,
  verticalDeg,
  imageScale,
  imageSource,
  onAngleChange,
}: MultiAngleSphereProps) {
  // 中心参考图尺寸随景别缩放。
  const cardW = clamp(56 + 64 * imageScale, 64, 104);
  const cardH = clamp(38 + 44 * imageScale, 46, 74);

  const meridians = useMemo(
    () => Array.from({ length: MERIDIAN_COUNT }, (_, i) => i * MERIDIAN_STEP),
    [],
  );
  const parallels = useMemo(
    () =>
      PARALLEL_LATITUDES.flatMap((lat) => [lat, -lat]).map((lat) => {
        const rad = (lat * Math.PI) / 180;
        const ringRadius = RADIUS * Math.cos(rad);
        const offsetY = RADIUS * Math.sin(rad);
        return { lat, ringRadius, offsetY };
      }),
    [],
  );

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      // 方向键自己处理点击，别把拖动逻辑也触发了。
      if ((event.target as HTMLElement).closest('.ma-dir-btn')) return;
      event.preventDefault();
      event.stopPropagation();
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
      let lastX = event.clientX;
      let lastY = event.clientY;
      let yaw = horizontalDeg;
      let pitch = verticalDeg;

      const handleMove = (ev: PointerEvent) => {
        ev.preventDefault();
        const dx = ev.clientX - lastX;
        const dy = ev.clientY - lastY;
        lastX = ev.clientX;
        lastY = ev.clientY;
        yaw = (((yaw + dx * DRAG_SENSITIVITY) % 360) + 360) % 360;
        pitch = clamp(pitch - dy * DRAG_SENSITIVITY, -90, 90);
        onAngleChange({ horizontalDeg: yaw, verticalDeg: pitch });
      };
      const handleUp = (ev: PointerEvent) => {
        target.releasePointerCapture(ev.pointerId);
        target.removeEventListener('pointermove', handleMove);
        target.removeEventListener('pointerup', handleUp);
      };
      target.addEventListener('pointermove', handleMove);
      target.addEventListener('pointerup', handleUp);
    },
    [horizontalDeg, verticalDeg, onAngleChange],
  );

  const nudge = useCallback(
    (dh: number, dv: number) => {
      onAngleChange({
        horizontalDeg: (((horizontalDeg + dh) % 360) + 360) % 360,
        verticalDeg: clamp(verticalDeg + dv, -90, 90),
      });
    },
    [horizontalDeg, verticalDeg, onAngleChange],
  );

  // horizontalDeg 是规范化到 0..360 的展示值（驱动滑块/数字框/后端）。直接喂给
  // rotateY 会在 359°↔0° 边界让 CSS transition 倒着绕一整圈。这里维护一个「连续」
  // 的 yaw 累加值：每次都按最短路径（落在 ±180° 内）逼近目标角，使球始终就近转动。
  const continuousYawRef = useRef(horizontalDeg);
  const shortestDelta = (((horizontalDeg - continuousYawRef.current) % 360) + 540) % 360 - 180;
  const continuousYaw = continuousYawRef.current + shortestDelta;
  continuousYawRef.current = continuousYaw;

  // 球的旋转：俯仰用 rotateX（取负号让「俯视=正」朝下看），方位用 rotateY。
  const pivotStyle = {
    ['--ma-pitch' as string]: `${-verticalDeg}deg`,
    ['--ma-yaw' as string]: `${continuousYaw}deg`,
  } as React.CSSProperties;

  return (
    <div className="ma-scene" onPointerDown={handlePointerDown}>
      {/* 共享 3D 世界：旋转的球/相机 + 球心参考图，统一按 Z 深度排序 */}
      <div className="ma-world">
      {/* 旋转支点：线框球 + 环绕相机 */}
      <div className="ma-pivot" style={pivotStyle}>
        {/* 经线（竖直大圆） */}
        {meridians.map((deg) => (
          <div
            key={`mer-${deg}`}
            className="ma-ring ma-meridian"
            style={{
              width: RADIUS * 2,
              height: RADIUS * 2,
              marginLeft: -RADIUS,
              marginTop: -RADIUS,
              transform: `rotateY(${deg}deg)`,
            }}
          />
        ))}
        {/* 赤道（水平大圆） */}
        <div
          className="ma-ring ma-meridian"
          style={{
            width: RADIUS * 2,
            height: RADIUS * 2,
            marginLeft: -RADIUS,
            marginTop: -RADIUS,
            transform: 'rotateX(90deg)',
          }}
        />
        {/* 纬线（水平圈，按纬度上下平移） */}
        {parallels.map(({ lat, ringRadius, offsetY }) => (
          <div
            key={`par-${lat}`}
            className="ma-ring ma-parallel"
            style={{
              width: ringRadius * 2,
              height: ringRadius * 2,
              marginLeft: -ringRadius,
              marginTop: -ringRadius,
              transform: `translateY(${-offsetY}px) rotateX(90deg)`,
            }}
          />
        ))}

        {/* 环绕相机：推到球面前点，随支点旋转即绕中心环绕 */}
        <div className="ma-camera" style={{ transform: `translateZ(${RADIUS}px)` }}>
          {/* 相机 → 球心连杆 */}
          <div className="ma-camera-line" style={{ height: RADIUS }} />
          <div className="ma-camera-body">
            <div className="ma-camera-face ma-camera-face-front">
              <span className="ma-camera-lens" />
            </div>
            <div
              className="ma-camera-face ma-camera-face-back"
              style={{ backgroundImage: `url("${imageSource}")` }}
            />
            <div className="ma-camera-face ma-camera-face-right" />
            <div className="ma-camera-face ma-camera-face-left" />
            <div className="ma-camera-face ma-camera-face-top" />
            <div className="ma-camera-face ma-camera-face-bottom" />
          </div>
        </div>
      </div>

      {/* 中心参考图：球心、始终正对观察者；与相机同处 3D 世界，按 Z 正确遮挡 */}
      <div className="ma-center-image" style={{ width: cardW, height: cardH }}>
        <img src={imageSource} alt="" draggable={false} />
      </div>
      </div>

      {/* 四向微调 */}
      <button type="button" className="ma-dir-btn ma-dir-up" onClick={() => nudge(0, 15)} aria-label="up">
        <ChevronUp />
      </button>
      <button type="button" className="ma-dir-btn ma-dir-down" onClick={() => nudge(0, -15)} aria-label="down">
        <ChevronDownIcon />
      </button>
      <button type="button" className="ma-dir-btn ma-dir-left" onClick={() => nudge(-15, 0)} aria-label="left">
        <ChevronLeftIcon />
      </button>
      <button type="button" className="ma-dir-btn ma-dir-right" onClick={() => nudge(15, 0)} aria-label="right">
        <ChevronRightIcon />
      </button>
    </div>
  );
}
