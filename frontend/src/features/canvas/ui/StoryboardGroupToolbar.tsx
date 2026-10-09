// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { NodeToolbar as ReactFlowNodeToolbar } from '@xyflow/react';
import { memo, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  Check,
  ChevronDown,
  Combine,
  Crop,
  Grid2x2,
  Hash,
  Layers,
  Unlink2,
} from 'lucide-react';

import { UiChipButton, UiPanel } from '@/components/ui';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/shadcn/dropdown-menu';
import { useCanvasStore } from '@/stores/canvasStore';
import type { CanvasNode, GroupNodeData } from '@/features/canvas/domain/canvasNodes';
import {
  DEFAULT_STORYBOARD_ASPECT,
  STORYBOARD_ASPECTS,
  resolveStoryboardCols,
} from '@/features/canvas/domain/storyboardGroup';
import {
  NODE_TOOLBAR_ALIGN,
  NODE_TOOLBAR_CLASS,
  NODE_TOOLBAR_OFFSET,
  NODE_TOOLBAR_POSITION,
} from '@/features/canvas/ui/nodeToolbarConfig';
import { ZoomScaledToolbar } from '@/features/canvas/ui/ZoomScaledToolbar';

const PANEL_CLASS =
  // 与 NodeActionToolbar 一致的入场动画：激活时从节点上沿淡入+轻微上滑浮现。
  'flex animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-2 items-center gap-1.5 rounded-[18px] !border-white/10 !bg-[#242426]/95 px-2 py-1.5 text-sm shadow-[0_10px_24px_rgba(0,0,0,0.28)] backdrop-blur-2xl duration-200 ease-out motion-reduce:animate-none [&_svg]:h-4 [&_svg]:w-4';
const CHIP_CLASS =
  'h-9 gap-1.5 rounded-[12px] !border-transparent !bg-transparent px-3 text-sm text-text-dark hover:!bg-[rgba(255,255,255,0.075)] focus:!border-transparent focus:!shadow-none focus-visible:!ring-0';
const MENU_CONTENT_CLASS =
  'z-[120] min-w-[120px] border-white/10 bg-[#242426]/95 text-text-dark shadow-none backdrop-blur-3xl';
const MENU_ITEM_CLASS =
  'gap-2 rounded-[10px] text-text-dark focus:bg-[rgba(255,255,255,0.075)] focus:text-text-dark';

// Max columns offered in the 宫格 picker — beyond this the cells get too small.
const MAX_GRID_COLS = 6;

interface StoryboardGroupToolbarProps {
  node: CanvasNode;
}

/**
 * Top toolbar shown when a single 分镜组 (storyboard group) node is selected.
 * Mirrors the libtv reference: cell aspect, grid columns, index toggle, stitch
 * (placeholder), convert-to-plain, and ungroup. Rendered in place of the generic
 * NodeActionToolbar (which early-returns to this for storyboard groups).
 */
export const StoryboardGroupToolbar = memo(({ node }: StoryboardGroupToolbarProps) => {
  const { t } = useTranslation();
  const data = node.data as GroupNodeData;
  const setStoryboardGroupConfig = useCanvasStore((state) => state.setStoryboardGroupConfig);
  const convertStoryboardGroupToPlain = useCanvasStore(
    (state) => state.convertStoryboardGroupToPlain
  );
  const ungroupNode = useCanvasStore((state) => state.ungroupNode);
  const childCount = useCanvasStore((state) =>
    state.nodes.reduce((acc, candidate) => (candidate.parentId === node.id ? acc + 1 : acc), 0)
  );

  const aspectKey = data.storyboardAspect ?? DEFAULT_STORYBOARD_ASPECT;
  const currentCols = resolveStoryboardCols(childCount, data.storyboardCols);
  const showIndex = data.storyboardShowIndex === true;

  const colOptions = useMemo(() => {
    const max = Math.max(1, Math.min(childCount, MAX_GRID_COLS));
    return Array.from({ length: max }, (_, index) => index + 1);
  }, [childCount]);

  return (
    <ReactFlowNodeToolbar
      nodeId={node.id}
      isVisible
      position={NODE_TOOLBAR_POSITION}
      align={NODE_TOOLBAR_ALIGN}
      offset={NODE_TOOLBAR_OFFSET}
      className={NODE_TOOLBAR_CLASS}
    >
      <ZoomScaledToolbar origin="bottom center">
      <UiPanel className={PANEL_CLASS} onClick={(event) => event.stopPropagation()}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <UiChipButton className={CHIP_CLASS}>
              <Crop className="h-4 w-4 text-text-muted" />
              <span>{t('canvas.storyboardGroup.aspect')}</span>
              <span className="text-text-muted">{aspectKey}</span>
              <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
            </UiChipButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_CONTENT_CLASS} align="start">
            {STORYBOARD_ASPECTS.map((option) => (
              <DropdownMenuItem
                key={option.key}
                className={MENU_ITEM_CLASS}
                onClick={() => setStoryboardGroupConfig(node.id, { aspectKey: option.key })}
              >
                {option.key === aspectKey ? (
                  <Check className="h-4 w-4 text-text-muted" />
                ) : (
                  <span className="h-4 w-4" />
                )}
                <span>{option.label}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <UiChipButton className={CHIP_CLASS}>
              <Grid2x2 className="h-4 w-4 text-text-muted" />
              <span>{t('canvas.storyboardGroup.cols')}</span>
              <span className="text-text-muted">
                {t('canvas.storyboardGroup.colsOption', { cols: currentCols })}
              </span>
              <ChevronDown className="h-3.5 w-3.5 text-text-muted" />
            </UiChipButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className={MENU_CONTENT_CLASS} align="start">
            {colOptions.map((cols) => (
              <DropdownMenuItem
                key={cols}
                className={MENU_ITEM_CLASS}
                onClick={() => setStoryboardGroupConfig(node.id, { cols })}
              >
                {cols === currentCols ? (
                  <Check className="h-4 w-4 text-text-muted" />
                ) : (
                  <span className="h-4 w-4" />
                )}
                <span>{t('canvas.storyboardGroup.colsOption', { cols })}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <UiChipButton
          className={`${CHIP_CLASS} ${showIndex ? '!text-cyan-200' : ''}`}
          onClick={() => setStoryboardGroupConfig(node.id, { showIndex: !showIndex })}
        >
          <Hash className="h-4 w-4" />
          <span>{t('canvas.storyboardGroup.index')}</span>
        </UiChipButton>

        <UiChipButton
          className={CHIP_CLASS}
          onClick={() => toast(t('canvas.storyboardGroup.stitchComingSoon'))}
        >
          <Combine className="h-4 w-4 text-text-muted" />
          <span>{t('canvas.storyboardGroup.stitch')}</span>
        </UiChipButton>

        <div className="mx-1 h-4 w-px shrink-0 bg-white/[0.14]" />

        <UiChipButton
          className={CHIP_CLASS}
          onClick={() => convertStoryboardGroupToPlain(node.id)}
        >
          <Layers className="h-4 w-4 text-text-muted" />
          <span>{t('canvas.storyboardGroup.convertToPlain')}</span>
        </UiChipButton>

        <UiChipButton
          className={`${CHIP_CLASS} hover:!text-amber-200`}
          onClick={() => ungroupNode(node.id)}
        >
          <Unlink2 className="h-4 w-4" />
          <span>{t('canvas.storyboardGroup.ungroup')}</span>
        </UiChipButton>
      </UiPanel>
      </ZoomScaledToolbar>
    </ReactFlowNodeToolbar>
  );
});

StoryboardGroupToolbar.displayName = 'StoryboardGroupToolbar';
