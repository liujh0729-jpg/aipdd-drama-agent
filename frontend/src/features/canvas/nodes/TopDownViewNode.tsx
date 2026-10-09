// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import { Map as MapIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useCanvasStore } from "@/stores/canvasStore";
import {
  CANVAS_NODE_TYPES,
  type TopDownViewNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { localizeNodeDisplayName } from "@/features/canvas/domain/nodeDisplay";
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from "@/features/canvas/ui/NodeHeader";
import {
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  canvasNodeFrameClass,
} from "@/features/canvas/ui/nodeFrameStyles";
import { readUrl } from "@/lib/url-params";
import { TopDownViewModal } from "@/features/canvas/topdown/TopDownViewModal";

type TopDownViewNodeProps = NodeProps & {
  id: string;
  data: TopDownViewNodeData;
  selected?: boolean;
};

const NODE_WIDTH = 240;
const NODE_HEIGHT = 168;

export const TopDownViewNode = memo(
  ({ id, data, selected }: TopDownViewNodeProps) => {
    const { t } = useTranslation();
    const updateNodeInternals = useUpdateNodeInternals();
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const [isEditorOpen, setEditorOpen] = useState(false);

    const resolvedTitle = useMemo(
      () => localizeNodeDisplayName(CANVAS_NODE_TYPES.topDownView, data, t),
      [data, t],
    );
    const cardToneClass = canvasNodeFrameClass({ selected });
    const resultUrl = data.imageUrl || data.previewImageUrl || null;

    const project = readUrl().project;

    useEffect(() => {
      updateNodeInternals(id);
    }, [id, updateNodeInternals]);

    const handleOpen = useCallback(() => {
      if (!project) return;
      setEditorOpen(true);
    }, [project]);

    return (
      <div
        className="group relative h-full w-full overflow-visible"
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
        onClick={() => setSelectedNode(id)}
      >
        <Handle
          type="target"
          position={Position.Left}
          id="target"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />
        <Handle
          type="source"
          position={Position.Right}
          id="source"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />

        <NodeHeader
          className={NODE_HEADER_FLOATING_POSITION_CLASS}
          icon={<MapIcon className="h-4 w-4" />}
          titleText={resolvedTitle}
          editable
          onTitleChange={(next) => updateNodeData(id, { displayName: next })}
        />

        <div
          className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} transition-colors ${cardToneClass}`}
        >
          {resultUrl ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                handleOpen();
              }}
              className="flex h-full w-full items-center justify-center overflow-hidden"
            >
              <img
                src={resultUrl}
                alt={resolvedTitle}
                draggable={false}
                className="h-full w-full object-contain"
              />
            </button>
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-5 py-4 text-center">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  handleOpen();
                }}
                className="flex h-10 w-full items-center justify-center rounded-[12px] border border-white/15 bg-white/[0.04] px-4 text-center text-[13px] text-text-dark transition-colors hover:bg-white/[0.08]"
              >
                {t("topDown.node.open")}
              </button>
              <span className="text-[12px] text-text-muted/90">
                {t("topDown.node.hint")}
              </span>
            </div>
          )}
        </div>

        {isEditorOpen && project && (
          <TopDownViewModal
            project={project}
            initialScene={data.draftScene}
            baseImageUrl={data.baseImageUrl}
            onPersistDraft={(scene) => updateNodeData(id, { draftScene: scene })}
            onClose={() => setEditorOpen(false)}
            onExported={(url) => {
              // 手动导出：渲染图回写本节点，按图片参与下游。
              updateNodeData(id, { imageUrl: url, previewImageUrl: url });
            }}
          />
        )}
      </div>
    );
  },
);

TopDownViewNode.displayName = "TopDownViewNode";
