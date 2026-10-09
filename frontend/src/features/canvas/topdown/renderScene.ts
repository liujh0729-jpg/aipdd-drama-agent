// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 俯视图场景 → 图片。
 *
 * 分两层：
 *  - {@link buildSceneSvgMarkup} 纯函数，把场景画成一段 SVG 标记（可单测、可作编辑器
 *    实时预览的 innerHTML 源）。
 *  - {@link renderSceneToPngDataUrl} / {@link exportSceneToUrl} 浏览器侧把 SVG 栅格化成
 *    PNG 并上传成静态地址（回写节点 `imageUrl`）。
 *
 * 栅格化时**背景图单独铺**：把带外链 `<image>` 的 SVG 塞进 `<img>` 再画到 canvas，
 * 多数浏览器出于安全不会加载外链，会得到空白底。所以这里先画白底 + 背景图，再把只含
 * 前景元素的 SVG 叠上去。
 */

import { uploadLocalImageToBackend } from '@/features/canvas/application/uploadToolOutput';
import {
  type TopDownItem,
  type TopDownScene,
} from './sceneModel';

const FONT_STACK =
  "'PingFang SC','Microsoft YaHei','Hiragino Sans GB','Noto Sans CJK SC',system-ui,sans-serif";

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

interface Vec {
  x: number;
  y: number;
}

/** 折线末端的实心箭头三角（比 SVG marker 更稳，栅格化各浏览器一致）。 */
function arrowHeadPoints(prev: Vec, end: Vec): string {
  const angle = Math.atan2(end.y - prev.y, end.x - prev.x);
  const len = 24;
  const halfWidth = 9;
  const ux = Math.cos(angle);
  const uy = Math.sin(angle);
  const bx = end.x - ux * len;
  const by = end.y - uy * len;
  const px = -uy;
  const py = ux;
  const left = `${bx + px * halfWidth},${by + py * halfWidth}`;
  const right = `${bx - px * halfWidth},${by - py * halfWidth}`;
  return `${end.x},${end.y} ${left} ${right}`;
}

function renderItem(item: TopDownItem): string {
  switch (item.kind) {
    case 'person': {
      const label = item.label
        ? `<text x="${item.x + 34}" y="${item.y + 8}" fill="${escapeXml(item.color)}" font-size="30" font-weight="700" font-family="${FONT_STACK}">${escapeXml(item.label)}</text>`
        : '';
      return (
        `<circle cx="${item.x}" cy="${item.y}" r="26" fill="#ffffff" stroke="${escapeXml(item.color)}" stroke-width="4" />` + label
      );
    }
    case 'camera': {
      // 机位：圆点 + 朝向楔形（随 angleDeg 旋转）+ 标签。
      const label = item.label
        ? `<text x="${item.x + 30}" y="${item.y - 24}" fill="#1f2937" font-size="26" font-weight="700" font-family="${FONT_STACK}">${escapeXml(item.label)}</text>`
        : '';
      return (
        `<g transform="rotate(${item.angleDeg} ${item.x} ${item.y})">` +
        `<path d="M ${item.x} ${item.y} L ${item.x + 60} ${item.y - 26} L ${item.x + 60} ${item.y + 26} Z" fill="#dbeafe" stroke="#2f6fd0" stroke-width="3" />` +
        `</g>` +
        `<circle cx="${item.x}" cy="${item.y}" r="16" fill="#2f6fd0" />` +
        label
      );
    }
    case 'prop': {
      const label = item.label
        ? `<text x="${item.x + item.w / 2}" y="${item.y + item.h / 2 + 10}" text-anchor="middle" fill="#374151" font-size="26" font-family="${FONT_STACK}">${escapeXml(item.label)}</text>`
        : '';
      return (
        `<rect x="${item.x}" y="${item.y}" width="${item.w}" height="${item.h}" rx="10" fill="#eef1f5" stroke="#9aa4b2" stroke-width="3" />` +
        label
      );
    }
    case 'arrow': {
      const pts = item.points;
      const polyline = pts.map((p) => `${p.x},${p.y}`).join(' ');
      const head = arrowHeadPoints(pts[pts.length - 2], pts[pts.length - 1]);
      const label = item.label
        ? `<text x="${pts[0].x}" y="${pts[0].y - 10}" fill="${escapeXml(item.color)}" font-size="24" font-family="${FONT_STACK}">${escapeXml(item.label)}</text>`
        : '';
      return (
        `<polyline points="${polyline}" fill="none" stroke="${escapeXml(item.color)}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" />` +
        `<polygon points="${head}" fill="${escapeXml(item.color)}" />` +
        label
      );
    }
    case 'text':
      return `<text x="${item.x}" y="${item.y}" fill="${escapeXml(item.color)}" font-size="34" font-weight="700" font-family="${FONT_STACK}">${escapeXml(item.text)}</text>`;
    default:
      return '';
  }
}

/**
 * 把场景画成一段 SVG 标记字符串。
 * @param opts.includeBackgroundImage 是否内联外链背景 `<image>`（栅格化时传 false，
 *   背景由调用方单独铺）。默认 true。
 */
export function buildSceneSvgMarkup(
  scene: TopDownScene,
  opts: { includeBackgroundImage?: boolean } = {},
): string {
  const { includeBackgroundImage = true } = opts;
  const size = scene.size;
  const bg =
    includeBackgroundImage && scene.background.kind === 'image'
      ? `<image href="${escapeXml(scene.background.url)}" x="0" y="0" width="${size}" height="${size}" preserveAspectRatio="none" opacity="${scene.background.opacity ?? 1}" />`
      : '';
  const body = scene.items.map(renderItem).join('');
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
    `<rect x="0" y="0" width="${size}" height="${size}" fill="#fbfaf5" />` +
    bg +
    body +
    `</svg>`
  );
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`image load failed: ${src}`));
    img.src = src;
  });
}

/** 浏览器侧：把场景栅格化成 PNG data URL（背景图单独铺，前景 SVG 叠加）。 */
export async function renderSceneToPngDataUrl(scene: TopDownScene): Promise<string> {
  const size = scene.size;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    throw new Error('canvas 2d context unavailable');
  }
  ctx.fillStyle = '#fbfaf5';
  ctx.fillRect(0, 0, size, size);

  if (scene.background.kind === 'image') {
    try {
      const bgImg = await loadImage(scene.background.url);
      ctx.globalAlpha = scene.background.opacity ?? 1;
      ctx.drawImage(bgImg, 0, 0, size, size);
      ctx.globalAlpha = 1;
    } catch {
      // 背景图加载失败不阻断导出：留白底继续画前景。
    }
  }

  const svg = buildSceneSvgMarkup(scene, { includeBackgroundImage: false });
  const svgUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  const fgImg = await loadImage(svgUrl);
  ctx.drawImage(fgImg, 0, 0, size, size);

  return canvas.toDataURL('image/png');
}

/** 渲染 + 上传，返回可持久化的静态图片地址（上传失败时回落 data URL，best-effort）。 */
export async function exportSceneToUrl(scene: TopDownScene, filename = 'topdown.png'): Promise<string> {
  const dataUrl = await renderSceneToPngDataUrl(scene);
  return uploadLocalImageToBackend(dataUrl, filename);
}
