// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

// [aipdd fork delta] 记住用户上一次为图片节点选的模型，让「新建图片节点」继承
// 该选择。图片模型不像视频那样存在可写死的默认 id：画布上的图片模型清单来自后台
// 「媒体模型」配置经 `/freezone/image/models` 下发，前端写死任何一个 id 都迟早和
// 清单对不上（选中一个网关里根本不存在的模型）。所以新建节点只带「上次选的那个」，
// 没有记录时留空，由各节点用目录首模型顶上 —— 这也正是模型选择器展示的模型。
// 读到的 id 失效时同样由节点的模型对账逻辑回落，这里不做校验。

const STORAGE_KEY = 'canvas.lastImageModel';

/** 返回上次选择的图片模型 id；无记录或不可用时返回 null。 */
export function readLastImageModel(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value && value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

/** 记录本次选择的图片模型 id，供后续新建节点继承。 */
export function writeLastImageModel(modelId: string): void {
  if (typeof window === 'undefined') return;
  if (!modelId) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, modelId);
  } catch {
    // localStorage 写不进去就算了，新建节点从目录首模型开始。
  }
}
