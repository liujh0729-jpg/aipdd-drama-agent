// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from "react";

const IMMERSIVE_BODY_CLASS = "st-viewer-immersive-active";

let activeImmersiveViewers = 0;

/**
 * 是否有沉浸式查看器(如 3GS 导演台全屏弹窗)正打开。打开时它独占键盘
 * (放置/删除 marker 等),画布的全局快捷键(Delete 删节点 / 复制粘贴 …)应让位,
 * 否则会出现「想删 marker 却把画布 3D 世界节点删了」这类串键。
 */
export function isImmersiveViewerActive(): boolean {
  return activeImmersiveViewers > 0;
}

export function useViewerImmersiveBody(active: boolean) {
  useEffect(() => {
    if (!active) return undefined;

    activeImmersiveViewers += 1;
    document.body.classList.add(IMMERSIVE_BODY_CLASS);

    return () => {
      activeImmersiveViewers = Math.max(0, activeImmersiveViewers - 1);
      if (activeImmersiveViewers === 0) {
        document.body.classList.remove(IMMERSIVE_BODY_CLASS);
      }
    };
  }, [active]);
}
