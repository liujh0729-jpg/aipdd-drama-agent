// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import type { MyBuddyAction } from "@/features/companion/mybuddy-actions";
import {
  PETDEX_FRAME_MS,
  PETDEX_GRID_COLS,
  PETDEX_GRID_ROWS,
  PETDEX_STATES,
  petdexStateForAction,
  type PetdexCatalogEntry,
  type PetdexState,
} from "@/features/companion/petdex/petdex-pets";
import "./petdex-pet.css";

const FIGURE_HEIGHT_PX = 72; // 比 Piko 略大，挂在顶栏下沿（lane 不裁剪溢出）
const FRAME_ASPECT = 192 / 208; // 单帧宽高比，保证不拉伸

type SpritePetCompanionProps = {
  pet: PetdexCatalogEntry;
  action: MyBuddyAction;
  /** 手动覆盖当前状态（来自「状态模拟」下拉 / 点击宠物循环）；不传则按 action 映射。 */
  stateOverride?: PetdexState | null;
  className?: string;
  style?: CSSProperties;
};

/**
 * 用精灵图渲染 petdex 宠物：一个单帧大小的视口，背景图按网格放大，纵向位置选「状态
 * 行」，横向逐帧步进播放该行。状态由同一套 controller 的 action 映射而来。
 */
export function SpritePetCompanion({
  pet,
  action,
  stateOverride,
  className,
  style,
}: SpritePetCompanionProps) {
  const reducedMotion = useReducedMotion();
  const spriteRef = useRef<HTMLDivElement | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const cols = pet.cols ?? PETDEX_GRID_COLS;
  const rows = pet.rows ?? PETDEX_GRID_ROWS;
  // 动作与任务强挂钩：任务进行中/成功/失败时（taskState 非 idle）任务优先，无视手动覆盖；
  // 仅常规(idle)态才用「状态模拟 / 点击宠物」的手动覆盖做预览。
  const taskState = petdexStateForAction(action);
  const { row, frames } =
    taskState !== PETDEX_STATES.idle ? taskState : (stateOverride ?? PETDEX_STATES.idle);
  const playFrames = Math.min(frames, cols);

  // 逐帧步进：直接改 ref 的 backgroundPositionX，避免每帧触发 React 重渲染。背景按
  // cols 放大，定位分母用 (cols-1)；只在该状态的前 playFrames 帧内循环（跳过空格）。
  // 状态变化时重置回首帧；reduced-motion 下定格首帧。
  useEffect(() => {
    const el = spriteRef.current;
    if (!el) return;
    let frame = 0;
    const apply = () => {
      el.style.backgroundPositionX = cols > 1 ? `${(frame / (cols - 1)) * 100}%` : "0%";
    };
    apply();
    if (reducedMotion || playFrames <= 1) return;
    const timer = window.setInterval(() => {
      frame = (frame + 1) % playFrames;
      apply();
    }, PETDEX_FRAME_MS);
    return () => window.clearInterval(timer);
  }, [cols, playFrames, row, reducedMotion, pet.spritesheetUrl]);

  // 预校验精灵图能否加载（CDN 拦截 / 离线时不渲染，避免裂图占位）。
  useEffect(() => {
    setLoadFailed(false);
    const img = new Image();
    img.onerror = () => setLoadFailed(true);
    img.src = pet.spritesheetUrl;
  }, [pet.spritesheetUrl]);

  const spriteStyle = useMemo<CSSProperties>(
    () => ({
      backgroundImage: `url("${pet.spritesheetUrl}")`,
      backgroundSize: `${cols * 100}% ${rows * 100}%`,
      backgroundPositionY: rows > 1 ? `${(row / (rows - 1)) * 100}%` : "0%",
      height: FIGURE_HEIGHT_PX,
      width: Math.round(FIGURE_HEIGHT_PX * FRAME_ASPECT),
    }),
    [pet.spritesheetUrl, cols, rows, row],
  );

  if (loadFailed) return null;

  return (
    <div
      className={["petdex-pet-companion", className].filter(Boolean).join(" ")}
      style={style}
      title={pet.displayName}
      aria-hidden
    >
      <div ref={spriteRef} className="petdex-pet-sprite" style={spriteStyle} />
    </div>
  );
}
