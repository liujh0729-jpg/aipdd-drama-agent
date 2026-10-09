// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { buildMentionRegex } from "@/lib/mention-markers";

/**
 * Seedance2 参考素材的最小形状：reference_label 是后端分配的「图片N / 音频N」（会随
 * 增删/重排重新编号），url/key 用作素材的稳定身份。
 */
export interface Seedance2ReferenceAssetLike {
  reference_label: string;
  url?: string;
  path?: string;
  key: string;
}

// 素材身份：用 URL（语义「提示词按素材 URL 引用」），回退 path / key 以防 URL 缺失。
// 删除 / 重排其它素材时，留下来的素材 URL 不变，故能稳定对号。
export function seedance2AssetIdentity(
  asset: Seedance2ReferenceAssetLike,
): string {
  return asset.url || asset.path || asset.key;
}

export interface Seedance2LabelIdentityMaps {
  /** 图片N → 素材身份(URL)。 */
  labelToIdentity: Map<string, string>;
  /** 素材身份(URL) → 图片N。 */
  identityToLabel: Map<string, string>;
  /** 全部 label，用于建匹配正则。 */
  labels: string[];
}

export function buildSeedance2LabelIdentityMaps(
  assets: Seedance2ReferenceAssetLike[],
): Seedance2LabelIdentityMaps {
  const labelToIdentity = new Map<string, string>();
  const identityToLabel = new Map<string, string>();
  const labels: string[] = [];
  for (const asset of assets) {
    const label = asset.reference_label;
    const identity = seedance2AssetIdentity(asset);
    if (!label || !identity) continue;
    if (!labelToIdentity.has(label)) {
      labelToIdentity.set(label, identity);
      labels.push(label);
    }
    if (!identityToLabel.has(identity)) {
      identityToLabel.set(identity, label);
    }
  }
  return { labelToIdentity, identityToLabel, labels };
}

export function sameSeedance2LabelIdentity(
  a: Seedance2LabelIdentityMaps,
  b: Seedance2LabelIdentityMaps,
): boolean {
  if (a.labelToIdentity.size !== b.labelToIdentity.size) return false;
  for (const [label, identity] of a.labelToIdentity) {
    if (b.labelToIdentity.get(label) !== identity) return false;
  }
  return true;
}

/**
 * 按素材身份(URL)把提示词里的 `@图片N / @音频N` 重新对号——让 mention 始终跟着它
 * 引用的素材走，而不是被写死的编号绑死：
 * - prev 能定位到身份、且该身份在 current 里仍在 → 改成它当前的 label（图片2→图片1）。
 * - 身份已不存在（素材被删） → 删掉该 mention 及其后随的一个空格。
 * - prev 里定位不到（手动输入的未知 label，或本就是当前 label） → 原样保留。
 *
 * 一次扫描原串，按 prev 身份独立映射，不会出现 2→1、3→2 之间的串号。后端拿到的仍是
 * 图片N（本函数只在前端把编号对齐到当前素材列表）。
 */
export function remapSeedance2Mentions(
  text: string,
  prev: Seedance2LabelIdentityMaps,
  current: Seedance2LabelIdentityMaps,
): string {
  // 用 prev + current 的 label 并集建正则，保证文本里的旧 label 能被匹配到。
  const knownLabels = Array.from(new Set([...prev.labels, ...current.labels]));
  const pattern = buildMentionRegex(knownLabels);
  if (!pattern) return text;

  let out = "";
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    const label = match[1];
    out += text.slice(lastIndex, start);

    const identity = prev.labelToIdentity.get(label);
    if (identity === undefined) {
      out += match[0];
      lastIndex = end;
      continue;
    }
    const nextLabel = current.identityToLabel.get(identity);
    if (nextLabel === undefined) {
      // 素材被删 → 连同后随一个空格一起去掉。
      lastIndex = text[end] === " " ? end + 1 : end;
      continue;
    }
    out += `@${nextLabel}`;
    lastIndex = end;
  }
  out += text.slice(lastIndex);
  return out;
}
