// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Maximize2, Minimize2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { CANVAS_NODE_INPUT_SURFACE_CLASS } from '@/features/canvas/ui/nodeFrameStyles';

interface PanelExpandButtonProps {
  expanded: boolean;
  onToggle: () => void;
  /** Positioning / layout classes from the caller (e.g. `absolute right-2 top-2`). */
  className?: string;
}

/**
 * 操作区「放大 / 收起」开关。各节点的浮动操作面板（ImageGen / Video / Audio …）
 * 共用这一颗按钮：展开时把面板放大成更舒适的编辑尺寸，收起时还原。位置由调用方
 * 通过 `className` 决定（一般是面板右上角 absolute）。
 */
export function PanelExpandButton({ expanded, onToggle, className }: PanelExpandButtonProps) {
  const { t } = useTranslation();
  const label = expanded
    ? t('node.operationPanel.collapse')
    : t('node.operationPanel.expand');
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      className={`nodrag flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${CANVAS_NODE_INPUT_SURFACE_CLASS} text-text-muted transition-colors hover:bg-white/[0.1] hover:text-text-dark ${className ?? ''}`}
    >
      {expanded ? (
        <Minimize2 className="h-3.5 w-3.5" />
      ) : (
        <Maximize2 className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
