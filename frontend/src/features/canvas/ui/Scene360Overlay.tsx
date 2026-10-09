// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useMemo, useState } from 'react';
import { Position, NodeToolbar as ReactFlowNodeToolbar } from '@xyflow/react';
import { ArrowUp, Globe2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  CANVAS_NODE_TYPES,
  EXPORT_RESULT_NODE_DEFAULT_WIDTH,
  EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { resolveFixedFeatureModelRequest } from '@/features/canvas/domain/fixedFeatureModelRequest';
import { ProviderModelPicker } from '@/features/canvas/ui/ProviderModelPicker';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  isAuthoritativeEmptyCatalog,
  useFreezoneImageModels,
} from '@/features/canvas/hooks/useFreezoneImageModels';
import {
  fetchFreezoneJobResult,
  submitFreezoneScene360,
} from '@/api/ops';
import { awaitTaskCompletion, isTaskPollTimeoutError } from '@/api/tasks';
import { notifyTaskStillRunning } from '@/features/canvas/application/errorDialog';
import { generationTaskDescriptor } from '@/features/canvas/application/resumeGeneration';
import { readUrl } from '@/lib/url-params';
import { NODE_TOOLBAR_CLASS } from './nodeToolbarConfig';
import { CANVAS_NODE_TOOLBAR_PILL_CLASS } from './nodeFrameStyles';
import { ZoomScaledToolbar } from './ZoomScaledToolbar';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
} from './nodeControlStyles';

const PANO_VIEWER_LAYOUT_WIDTH = 720;
const PANO_VIEWER_LAYOUT_HEIGHT = 420;
const SCENE_360_OUTPUT_ASPECT_RATIO = '2:1' as const;

interface Scene360OverlayProps {
  node: CanvasNode;
  imageSource: string;
  onClose: () => void;
}

export const Scene360Overlay = memo(
  ({ node, imageSource, onClose }: Scene360OverlayProps) => {
    const { t } = useTranslation();
    const addNode = useCanvasStore((state) => state.addNode);
    const addEdge = useCanvasStore((state) => state.addEdge);
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const findNodePosition = useCanvasStore((state) => state.findNodePosition);
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const imageCatalog = useFreezoneImageModels();
    const imageModels = imageCatalog.models;
    // [aipdd fork delta] 360 全景模型基于「媒体模型」列表，默认预选面板首项
    // （即「默认生成模型」里的 image），用户可在选择器改选。报价与提交都来自
    // 选中模型的同一份解析，换模型后二者一起随动，不会再出现标价与执行走散。
    const [modelId, setModelId] = useState('');
    const selectedModel =
      imageModels.find((m) => m.id === modelId) ?? imageModels[0];
    const modelUnavailable =
      isAuthoritativeEmptyCatalog(imageCatalog) || selectedModel === undefined;
    // 全景没有尺寸/画质选择器，按后台对该模型配置的档位取默认值；模型没配
    // 画质就不下发 quality。报价与提交必须来自同一次解析，见
    // `resolveFixedFeatureModelRequest` 的注释。
    const modelRequest = useMemo(
      () => resolveFixedFeatureModelRequest(selectedModel),
      [selectedModel],
    );
    const submitDisabled = modelUnavailable;

    const handleSubmit = useCallback(async () => {
      // 按钮已经禁用，这里再挡一道：没有固定模型就绝不该发出请求。
      if (modelUnavailable) return;
      const project = readUrl().project;
      if (!project) {
        console.error('[scene-360] no project in URL — cannot submit');
        return;
      }

      const position = findNodePosition(
        node.id,
        EXPORT_RESULT_NODE_DEFAULT_WIDTH,
        EXPORT_RESULT_NODE_LAYOUT_HEIGHT,
      );
      const generationStartedAt = Date.now();
      const nextNodeId = addNode(
        CANVAS_NODE_TYPES.exportImage,
        position,
        {
          displayName: t('scene360.label'),
          imageUrl: null,
          previewImageUrl: null,
          aspectRatio: SCENE_360_OUTPUT_ASPECT_RATIO,
          resultKind: 'generic',
          output_role: 'scene_360_candidate',
          media_kind: 'pano360',
          isGenerating: true,
          generationStartedAt,
        },
      );
      addEdge(node.id, nextNodeId);
      setSelectedNode(nextNodeId);
      onClose();

      try {
        const ref = await submitFreezoneScene360(project, {
          referenceUrl: imageSource.split('?')[0],
          ...modelRequest.submit,
        });
        updateNodeData(nextNodeId, generationTaskDescriptor(ref));
        const completed = await awaitTaskCompletion(ref.task_key, project, { taskType: ref.task_type });
        const directUrl = completed.result?.['output_url'] as string | undefined;
        let url = directUrl;
        if (!url) {
          const fallback = await fetchFreezoneJobResult(project, ref.task_type, ref.job_id);
          url = fallback.url;
        }
        updateNodeData(nextNodeId, {
          imageUrl: url,
          previewImageUrl: url,
          aspectRatio: SCENE_360_OUTPUT_ASPECT_RATIO,
          output_role: 'scene_360_candidate',
          media_kind: 'pano360',
          isGenerating: false,
          generationStartedAt: null,
          generationError: null,
        });

        const viewerPosition = findNodePosition(
          nextNodeId,
          PANO_VIEWER_LAYOUT_WIDTH,
          PANO_VIEWER_LAYOUT_HEIGHT,
        );
        const viewerNodeId = addNode(CANVAS_NODE_TYPES.pano360Viewer, viewerPosition);
        addEdge(nextNodeId, viewerNodeId);
      } catch (err) {
        // 轮询超时 ≠ 生成失败：后端还在跑，节点上的任务句柄仍可续接。
        // 写错误横幅会把一个还活着的任务标成失败，并清掉句柄。
        if (isTaskPollTimeoutError(err)) {
          notifyTaskStillRunning(t);
          return;
        }
        const message = err instanceof Error ? err.message : String(err);
        console.error('[scene-360] generation failed', err);
        updateNodeData(nextNodeId, {
          isGenerating: false,
          generationStartedAt: null,
          generationError: message,
        });
      }
    }, [
      addEdge,
      addNode,
      findNodePosition,
      imageSource,
      modelRequest,
      modelUnavailable,
      node,
      onClose,
      setSelectedNode,
      t,
      updateNodeData,
    ]);

    return (
      <ReactFlowNodeToolbar
        nodeId={node.id}
        isVisible
        position={Position.Bottom}
        align="center"
        offset={12}
        className={NODE_TOOLBAR_CLASS}
      >
        {/* 操作区跟随画布缩放（align=center → 锚点顶边中点，贴节点底边）。 */}
        <ZoomScaledToolbar origin="top center">
        <div
          className={`flex min-w-[420px] items-center gap-2 ${CANVAS_NODE_TOOLBAR_PILL_CLASS}`}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-bg-dark/70 text-text-muted transition-colors hover:bg-bg-dark hover:text-text-dark"
            onClick={onClose}
            title={t('scene360.exit')}
          >
            <X className="h-4 w-4" />
          </button>

          <div className="flex min-w-0 flex-1 items-center gap-1.5 px-2 text-xs text-text-dark">
            <Globe2 className="h-3.5 w-3.5 shrink-0 text-text-muted" />
            <span className="truncate font-medium">{t('scene360.label')}</span>
          </div>

          <ProviderModelPicker
            selectedModelId={selectedModel?.id ?? ''}
            onChange={setModelId}
            popoverPlacement="top"
          />

          <button
            type="button"
            disabled={submitDisabled}
            className={`${NODE_GENERATE_BUTTON_BASE_CLASS} shrink-0 ${
              submitDisabled
                ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                : NODE_GENERATE_BUTTON_ENABLED_CLASS
            }`}
            onClick={handleSubmit}
            title={
              modelUnavailable
                ? t('modelParams.noModelsAvailable')
                : t('scene360.submit')
            }
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        </div>
        </ZoomScaledToolbar>
      </ReactFlowNodeToolbar>
    );
  },
);

Scene360Overlay.displayName = 'Scene360Overlay';
