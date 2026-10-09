// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { type PointerEvent as ReactPointerEvent, type Ref } from "react";

import {
  TOPDOWN_PALETTE,
  type TopDownItem,
  type TopDownPoint,
  type TopDownScene,
} from "./sceneModel";

/** 把屏幕坐标换算到场景设计坐标（svg 以 100% 铺满、viewBox 为 0 0 size size）。 */
export function clientToScenePoint(
  svg: SVGSVGElement,
  clientX: number,
  clientY: number,
  size: number,
): TopDownPoint {
  const rect = svg.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
  return {
    x: ((clientX - rect.left) / rect.width) * size,
    y: ((clientY - rect.top) / rect.height) * size,
  };
}

const PROP_FILL = "#eef1f5";
const PROP_STROKE = "#9aa4b2";
// [aipdd fork delta] 家具符号的细节描边／沙发坐垫色：让门/窗/楼梯/沙发等在俯视图
// 上一眼可辨（需求：俯视图沙发门窗没有文字标记／分不清），与底框灰调同族。
const PROP_INK = "#6b7688";
const PROP_SOFT = "#c9d2de";

/** [aipdd fork delta] 按 shape 在矩形内叠画符号。坐标全部基于 item 的 x/y/w/h，
 * 可随拖拽／缩放变化。rect 无附加符号（就是灰方块）。 */
function PropGlyph({ x, y, w, h, shape }: { x: number; y: number; w: number; h: number; shape: string }) {
  const cy = y + h / 2;
  switch (shape) {
    case "sofa":
      return (
        <g fill={PROP_SOFT} stroke="none">
          <rect x={x + 10} y={y + 8} width={Math.max(0, w - 20)} height={16} rx={5} />
          <rect x={x + 8} y={y + 8} width={14} height={Math.max(0, h - 16)} rx={5} />
          <rect x={x + w - 22} y={y + 8} width={14} height={Math.max(0, h - 16)} rx={5} />
        </g>
      );
    case "table":
      return (
        <rect x={x + 14} y={y + 14} width={Math.max(0, w - 28)} height={Math.max(0, h - 28)} rx={8} fill="none" stroke={PROP_INK} strokeWidth={3} />
      );
    case "tv":
      return <rect x={x + 8} y={y + 8} width={Math.max(0, w - 16)} height={Math.max(0, h * 0.5)} rx={3} fill={PROP_INK} stroke="none" />;
    case "door": {
      const r = Math.min(w, h);
      return (
        <g fill="none" stroke={PROP_INK} strokeWidth={3}>
          <line x1={x} y1={y} x2={x} y2={y + r} />
          <path d={`M ${x} ${y + r} A ${r} ${r} 0 0 1 ${x + r} ${y}`} />
        </g>
      );
    }
    case "window":
      return (
        <g fill="none" stroke={PROP_INK} strokeWidth={3}>
          <line x1={x + 8} y1={cy} x2={x + w - 8} y2={cy} />
          <line x1={x + 8} y1={y + h * 0.28} x2={x + w - 8} y2={y + h * 0.28} />
          <line x1={x + 8} y1={y + h * 0.72} x2={x + w - 8} y2={y + h * 0.72} />
        </g>
      );
    case "stairs": {
      const n = 5;
      return (
        <g fill="none" stroke={PROP_INK} strokeWidth={2.5}>
          {Array.from({ length: n - 1 }, (_, i) => {
            const yy = y + ((i + 1) * h) / n;
            return <line key={i} x1={x + 6} y1={yy} x2={x + w - 6} y2={yy} />;
          })}
        </g>
      );
    }
    default:
      return null;
  }
}

function ItemShape({ item }: { item: TopDownItem }) {
  switch (item.kind) {
    case "person":
      return (
        <g>
          <circle cx={item.x} cy={item.y} r={26} fill="#fff" stroke={item.color} strokeWidth={4} />
          {item.label ? (
            <text x={item.x + 34} y={item.y + 8} fill={item.color} fontSize={30} fontWeight={700}>
              {item.label}
            </text>
          ) : null}
        </g>
      );
    case "camera": {
      const rad = (item.angleDeg * Math.PI) / 180;
      const a1 = rad - 0.42;
      const a2 = rad + 0.42;
      const wedge = `${item.x},${item.y} ${item.x + Math.cos(a1) * 60},${item.y + Math.sin(a1) * 60} ${item.x + Math.cos(a2) * 60},${item.y + Math.sin(a2) * 60}`;
      return (
        <g>
          <polygon points={wedge} fill="#dbeafe" stroke="#2f6fd0" strokeWidth={3} />
          <circle cx={item.x} cy={item.y} r={16} fill="#2f6fd0" />
          {item.label ? (
            <text x={item.x + 30} y={item.y - 24} fill="#1f2937" fontSize={26} fontWeight={700}>
              {item.label}
            </text>
          ) : null}
        </g>
      );
    }
    case "prop":
      return (
        <g data-shape={item.shape}>
          <rect x={item.x} y={item.y} width={item.w} height={item.h} rx={10} fill={PROP_FILL} stroke={PROP_STROKE} strokeWidth={3} />
          <PropGlyph x={item.x} y={item.y} w={item.w} h={item.h} shape={item.shape} />
          {item.label ? (
            <text x={item.x + item.w / 2} y={item.y + item.h / 2 + 10} textAnchor="middle" fill="#374151" fontSize={26} fontWeight={700}>
              {item.label}
            </text>
          ) : null}
        </g>
      );
    case "arrow": {
      const pts = item.points.map((p) => `${p.x},${p.y}`).join(" ");
      const last = item.points[item.points.length - 1];
      const prev = item.points[item.points.length - 2];
      const angle = Math.atan2(last.y - prev.y, last.x - prev.x);
      const head = `${last.x},${last.y} ${last.x - Math.cos(angle - 0.5) * 24},${last.y - Math.sin(angle - 0.5) * 24} ${last.x - Math.cos(angle + 0.5) * 24},${last.y - Math.sin(angle + 0.5) * 24}`;
      return (
        <g>
          <polyline points={pts} fill="none" stroke={item.color} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" />
          <polygon points={head} fill={item.color} />
          {item.label ? (
            <text x={item.points[0].x} y={item.points[0].y - 10} fill={item.color} fontSize={24}>
              {item.label}
            </text>
          ) : null}
        </g>
      );
    }
    case "text":
      return (
        <text x={item.x} y={item.y} fill={item.color} fontSize={34} fontWeight={700}>
          {item.text}
        </text>
      );
    default:
      return null;
  }
}

interface TopDownCanvasProps {
  scene: TopDownScene;
  selectedId: string | null;
  /** 正在绘制中的箭头顶点序列（未提交）。 */
  draftArrow: TopDownPoint[];
  svgRef: Ref<SVGSVGElement>;
  onPointerDownBackground: (e: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerDownItem: (id: string, e: ReactPointerEvent) => void;
  onPointerDownRotate: (id: string, e: ReactPointerEvent) => void;
  onDoubleClickBackground: (e: ReactPointerEvent<SVGSVGElement>) => void;
}

export function TopDownCanvas({
  scene,
  selectedId,
  draftArrow,
  svgRef,
  onPointerDownBackground,
  onPointerDownItem,
  onPointerDownRotate,
  onDoubleClickBackground,
}: TopDownCanvasProps) {
  const size = scene.size;
  const selected = scene.items.find((it) => it.id === selectedId) ?? null;

  return (
    <div className="relative aspect-square w-full max-h-full overflow-hidden rounded-xl border border-white/10 bg-[#fbfaf5]">
      {scene.background.kind === "image" ? (
        <img
          src={scene.background.url}
          alt=""
          draggable={false}
          className="pointer-events-none absolute inset-0 h-full w-full object-fill"
          style={{ opacity: scene.background.opacity ?? 1 }}
        />
      ) : null}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${size} ${size}`}
        className="absolute inset-0 h-full w-full touch-none select-none"
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) onPointerDownBackground(e);
        }}
        onDoubleClick={onDoubleClickBackground}
      >
        {scene.items.map((item) => (
          <g
            key={item.id}
            className="cursor-move"
            onPointerDown={(e) => {
              e.stopPropagation();
              onPointerDownItem(item.id, e);
            }}
          >
            <ItemShape item={item} />
            {selectedId === item.id ? (
              <g pointerEvents="none">
                {item.kind === "arrow" ? null : (
                  <circle
                    cx={item.kind === "prop" ? item.x + item.w / 2 : item.x}
                    cy={item.kind === "prop" ? item.y + item.h / 2 : item.y}
                    r={item.kind === "prop" ? Math.max(item.w, item.h) / 2 + 12 : 40}
                    fill="none"
                    stroke="#6366f1"
                    strokeWidth={2}
                    strokeDasharray="6 6"
                  />
                )}
              </g>
            ) : null}
          </g>
        ))}

        {/* 机位朝向旋转手柄 */}
        {selected && selected.kind === "camera" ? (
          (() => {
            const rad = (selected.angleDeg * Math.PI) / 180;
            const hx = selected.x + Math.cos(rad) * 60;
            const hy = selected.y + Math.sin(rad) * 60;
            return (
              <circle
                cx={hx}
                cy={hy}
                r={12}
                fill="#2f6fd0"
                stroke="#fff"
                strokeWidth={3}
                className="cursor-grab"
                onPointerDown={(e) => {
                  e.stopPropagation();
                  onPointerDownRotate(selected.id, e);
                }}
              />
            );
          })()
        ) : null}

        {/* 正在绘制的箭头 */}
        {draftArrow.length > 0 ? (
          <g pointerEvents="none">
            <polyline
              points={draftArrow.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="none"
              stroke={TOPDOWN_PALETTE[0]}
              strokeWidth={6}
              strokeDasharray="10 8"
              strokeLinecap="round"
            />
            {draftArrow.map((p, i) => (
              <circle key={i} cx={p.x} cy={p.y} r={7} fill={TOPDOWN_PALETTE[0]} />
            ))}
          </g>
        ) : null}
      </svg>
    </div>
  );
}
