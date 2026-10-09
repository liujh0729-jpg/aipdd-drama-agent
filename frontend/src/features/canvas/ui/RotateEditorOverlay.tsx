// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { Position, NodeToolbar as ReactFlowNodeToolbar } from '@xyflow/react';
import {
  Check,
  FlipHorizontal,
  FlipVertical,
  Loader2,
  RotateCw,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import {
  DEFAULT_NODE_WIDTH,
  type CanvasNode,
} from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { uploadFreezoneImage } from '@/api/ops';
import { loadImageElement } from '@/features/canvas/application/imageData';
import { readUrl } from '@/lib/url-params';
import { NODE_TOOLBAR_CLASS } from './nodeToolbarConfig';
import { CANVAS_NODE_TOOLBAR_PILL_CLASS } from './nodeFrameStyles';

interface RotateEditorOverlayProps {
  node: CanvasNode;
  imageSource: string;
  /**
   * 关闭旋转编辑器。`committed` 表示是否真正提交了一次旋转（开始写回节点）：
   *   - `false`：用户退出 / 按 Esc / 无任何变换直接关闭 —— 调用方应把进入旋转时
   *     预创建的「旋转结果」节点删掉，避免凭空多出一个节点。
   *   - `true` ：已开始把旋转结果写回该节点，调用方保留它。
   */
  onClose: (committed: boolean) => void;
}

// 旋转锚点：用户每次点击"顺时针 90°"都从角度滑块的当前值上加 90°，
// 而镜像则是布尔切换（再次按下会取消），与 libtv 行为一致。
function normalizeAngle(angle: number): number {
  const n = angle % 360;
  return n < 0 ? n + 360 : n;
}

export const RotateEditorOverlay = memo(
  ({ node, imageSource, onClose }: RotateEditorOverlayProps) => {
    const { t } = useTranslation();
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);

    const [angle, setAngle] = useState(0);
    const [mirrorH, setMirrorH] = useState(false);
    const [mirrorV, setMirrorV] = useState(false);
    const [isSaving, setIsSaving] = useState(false);

    const nodeWidth =
      typeof node.measured?.width === 'number'
        ? node.measured.width
        : typeof node.width === 'number'
          ? node.width
          : DEFAULT_NODE_WIDTH;
    const nodeHeight =
      typeof node.measured?.height === 'number'
        ? node.measured.height
        : typeof node.height === 'number'
          ? node.height
          : nodeWidth;

    const transform = useMemo(() => {
      const sx = mirrorH ? -1 : 1;
      const sy = mirrorV ? -1 : 1;
      return `rotate(${angle}deg) scale(${sx}, ${sy})`;
    }, [angle, mirrorH, mirrorV]);

    const handleRotate90 = useCallback(() => {
      setAngle((prev) => normalizeAngle(prev + 90));
    }, []);

    const handleAngleChange = useCallback((value: number) => {
      if (Number.isFinite(value)) {
        setAngle(normalizeAngle(value));
      }
    }, []);

    const handleSave = useCallback(async () => {
      if (isSaving) return;
      // 没有任何变换时直接关闭，不必上传重写。视作「未提交」，让调用方把预创建
      // 的结果节点删掉（等同退出）。
      if (angle === 0 && !mirrorH && !mirrorV) {
        onClose(false);
        return;
      }
      const project = readUrl().project;
      if (!project) {
        console.error('[rotate] no project in URL — cannot persist result');
        return;
      }

      setIsSaving(true);
      updateNodeData(node.id, {
        isGenerating: true,
        generationStartedAt: Date.now(),
        generationError: null,
        generationErrorDetails: null,
      });
      // 已开始写回旋转结果到该节点 —— 标记为已提交，调用方保留节点。
      onClose(true);

      try {
        const image = await loadImageElement(imageSource);
        const sw = image.naturalWidth;
        const sh = image.naturalHeight;

        // 旋转后的画布需要包含图片所有四角（任意角度）。
        const rad = (angle * Math.PI) / 180;
        const cos = Math.abs(Math.cos(rad));
        const sin = Math.abs(Math.sin(rad));
        const dw = Math.max(1, Math.round(sw * cos + sh * sin));
        const dh = Math.max(1, Math.round(sw * sin + sh * cos));

        const canvas = document.createElement('canvas');
        canvas.width = dw;
        canvas.height = dh;
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('2d context unavailable');

        ctx.translate(dw / 2, dh / 2);
        ctx.rotate(rad);
        ctx.scale(mirrorH ? -1 : 1, mirrorV ? -1 : 1);
        ctx.drawImage(image, -sw / 2, -sh / 2);

        const blob: Blob = await new Promise((resolve, reject) => {
          canvas.toBlob(
            (b) => (b ? resolve(b) : reject(new Error('toBlob returned null'))),
            'image/png',
          );
        });

        const filename = `rotate-${node.id}-${Date.now()}.png`;
        const uploaded = await uploadFreezoneImage(project, blob, filename);

        const newAspectRatio = `${dw}:${dh}`;
        updateNodeData(node.id, {
          imageUrl: uploaded.url,
          previewImageUrl: uploaded.url,
          aspectRatio: newAspectRatio,
          isGenerating: false,
          generationStartedAt: null,
          generationError: null,
          generationErrorDetails: null,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error('[rotate] save failed', err);
        updateNodeData(node.id, {
          isGenerating: false,
          generationStartedAt: null,
          generationError: message,
          generationErrorDetails: message,
        });
      } finally {
        setIsSaving(false);
      }
    }, [
      angle,
      imageSource,
      isSaving,
      mirrorH,
      mirrorV,
      node.id,
      onClose,
      updateNodeData,
    ]);

    useEffect(() => {
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape' && !isSaving) {
          onClose(false);
        }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [isSaving, onClose]);

    return (
      <>
        {/* 不透明遮罩 + 实时变换的预览图，盖住原节点图。 */}
        <ReactFlowNodeToolbar
          nodeId={node.id}
          isVisible
          position={Position.Top}
          align="center"
          offset={0}
          className={`${NODE_TOOLBAR_CLASS} pointer-events-none`}
        >
          <div className="relative" style={{ width: 0, height: 0 }}>
            <div
              className="pointer-events-none absolute overflow-hidden rounded-md bg-bg-dark"
              style={{
                width: nodeWidth,
                height: nodeHeight,
                left: '50%',
                top: nodeHeight / 2,
                transform: 'translate(-50%, -50%)',
              }}
            >
              <img
                src={imageSource}
                alt=""
                draggable={false}
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'contain',
                  transform,
                  transition: 'transform 120ms ease-out',
                }}
              />
            </div>
          </div>
        </ReactFlowNodeToolbar>

        {/* 控制条：浮动在节点上方。 */}
        <ReactFlowNodeToolbar
          nodeId={node.id}
          isVisible
          position={Position.Top}
          align="center"
          offset={25}
          className={NODE_TOOLBAR_CLASS}
        >
          <div
            className={`flex items-center gap-1 ${CANVAS_NODE_TOOLBAR_PILL_CLASS}`}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-bg-dark/70 text-text-muted transition-colors hover:bg-bg-dark hover:text-text-dark disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onClose(false)}
              title={t('rotateEditor.exit')}
              disabled={isSaving}
            >
              <X className="h-4 w-4" />
            </button>

            <div
              className="flex items-center gap-2 px-2"
              title={t('rotateEditor.angleLabel')}
            >
              <span className="text-[11px] uppercase tracking-wide text-text-dark/90">
                {t('rotateEditor.angleLabel')}
              </span>
              <div className="relative">
                <input
                  type="number"
                  min={0}
                  max={360}
                  step={1}
                  value={Math.round(angle)}
                  disabled={isSaving}
                  onChange={(event) => handleAngleChange(Number(event.target.value))}
                  className="h-7 w-16 rounded-md border border-[rgba(255,255,255,0.14)] bg-bg-dark/60 px-1.5 pr-5 text-center text-xs text-text-dark outline-none focus:border-accent disabled:opacity-50"
                />
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-text-muted">
                  {t('rotateEditor.angleSuffix')}
                </span>
              </div>
            </div>

            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-dark transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              onClick={handleRotate90}
              title={t('rotateEditor.rotate90')}
              disabled={isSaving}
            >
              <RotateCw className="h-4 w-4" />
            </button>

            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-dark transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => setMirrorH((prev) => !prev)}
              title={t('rotateEditor.mirrorH')}
              disabled={isSaving}
            >
              <FlipHorizontal className="h-4 w-4" />
            </button>

            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-dark transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => setMirrorV((prev) => !prev)}
              title={t('rotateEditor.mirrorV')}
              disabled={isSaving}
            >
              <FlipVertical className="h-4 w-4" />
            </button>

            <button
              type="button"
              onClick={() => {
                void handleSave();
              }}
              disabled={isSaving}
              className="flex h-8 items-center gap-1.5 rounded-full bg-white px-3 text-xs font-medium text-bg-dark transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
              title={t('rotateEditor.save')}
            >
              {isSaving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Check className="h-4 w-4" />
              )}
              {isSaving ? t('rotateEditor.saving') : t('rotateEditor.save')}
            </button>
          </div>
        </ReactFlowNodeToolbar>
      </>
    );
  },
);

RotateEditorOverlay.displayName = 'RotateEditorOverlay';
