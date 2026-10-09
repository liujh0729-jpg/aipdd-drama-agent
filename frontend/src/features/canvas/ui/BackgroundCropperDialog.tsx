// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactCrop, { type Crop, type PixelCrop } from 'react-image-crop';
import 'react-image-crop/dist/ReactCrop.css';

import { UiButton, UiModal } from '@/components/ui/primitives';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';

/**
 * 截图选区对话框 — 用户从 source 图(master/reverse 等)选一块固定比例
 * 区域作为 beat 的 selected_background。
 *
 * 流程:
 *   1. 加载 source 图,显示 react-image-crop UI
 *   2. 用户拖出固定比例区域(锁定纵横比)
 *   3. 点 "用作背景源" → canvas.toBlob
 *   4. 父组件决定是走主线 Commit 还是 legacy background-anchor upload
 *
 * 区别于 Pano360 + 3GS 的 "截当前 viewer 视角" 路径 — 那两个 source 在浏览器
 * 实时渲染 (yaw/pitch/fov, PlayCanvas 相机),最终也是 toBlob → upload,但用的是
 * 各自 viewer 节点上的 capture 按钮,不走这个 dialog。这个 dialog 专门给
 * 平面 source (master / reverse) 用。
 */
export interface BackgroundCropperDialogProps {
  isOpen: boolean;
  onClose: () => void;
  sourceUrl: string;
  sourceLabel?: string;
  aspectOptions?: readonly string[];
  defaultAspectRatio?: string;
  onConfirmBlob: (blob: Blob, filename: string) => Promise<void>;
  onCandidateSuccess?: (message: string) => void;
  onError?: (message: string) => void;
}

const DEFAULT_ASPECT_OPTIONS = ['16:9'] as const;

function parseAspectRatioValue(value: string): number {
  const [rawWidth, rawHeight] = value.split(':');
  const width = Number(rawWidth);
  const height = Number(rawHeight);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 16 / 9;
  }
  return width / height;
}

export function centerInitialCrop(width: number, height: number, targetAspectRatio = 16 / 9): Crop {
  const sourceAspect = width / height;
  if (sourceAspect >= targetAspectRatio) {
    // source 更宽 → 用 source 全高,水平居中目标比例
    const cropWidthPct = (targetAspectRatio / sourceAspect) * 100;
    return {
      unit: '%',
      x: (100 - cropWidthPct) / 2,
      y: 0,
      width: cropWidthPct,
      height: 100,
    };
  }
  // source 更高 → 用 source 全宽,垂直居中目标比例
  const cropHeightPct = (sourceAspect / targetAspectRatio) * 100;
  return {
    unit: '%',
    x: 0,
    y: (100 - cropHeightPct) / 2,
    width: 100,
    height: cropHeightPct,
  };
}

export function pixelCropFromPercentCrop(
  cropValue: Crop,
  width: number,
  height: number,
): PixelCrop {
  if (cropValue.unit !== '%') {
    return {
      unit: 'px',
      x: cropValue.x,
      y: cropValue.y,
      width: cropValue.width,
      height: cropValue.height,
    };
  }
  return {
    unit: 'px',
    x: (cropValue.x / 100) * width,
    y: (cropValue.y / 100) * height,
    width: (cropValue.width / 100) * width,
    height: (cropValue.height / 100) * height,
  };
}

async function exportCroppedBlob(
  image: HTMLImageElement,
  pixelCrop: PixelCrop,
): Promise<Blob> {
  const canvas = document.createElement('canvas');
  // 用 source 原始分辨率 (image.naturalWidth/Height) 计算 scale,确保 crop
  // 输出是 source 真实像素的子集而不是 displayed 像素 (后者会被缩到 dialog 尺寸)。
  const scaleX = image.naturalWidth / image.width;
  const scaleY = image.naturalHeight / image.height;
  canvas.width = Math.round(pixelCrop.width * scaleX);
  canvas.height = Math.round(pixelCrop.height * scaleY);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas 2d context unavailable');
  ctx.drawImage(
    image,
    pixelCrop.x * scaleX,
    pixelCrop.y * scaleY,
    pixelCrop.width * scaleX,
    pixelCrop.height * scaleY,
    0,
    0,
    canvas.width,
    canvas.height,
  );
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error('canvas toBlob returned null'));
      },
      'image/png',
      0.95,
    );
  });
}

export function BackgroundCropperDialog({
  isOpen,
  onClose,
  sourceUrl,
  sourceLabel,
  aspectOptions = DEFAULT_ASPECT_OPTIONS,
  defaultAspectRatio,
  onConfirmBlob,
  onCandidateSuccess,
  onError,
}: BackgroundCropperDialogProps) {
  const { t } = useTranslation();
  const [crop, setCrop] = useState<Crop | undefined>(undefined);
  const [completedCrop, setCompletedCrop] = useState<PixelCrop | undefined>(undefined);
  const [selectedAspectRatio, setSelectedAspectRatio] = useState(
    defaultAspectRatio ?? aspectOptions[0] ?? '16:9',
  );
  const [uploading, setUploading] = useState(false);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const normalizedAspectOptions = useMemo(
    () => Array.from(new Set(aspectOptions.length > 0 ? aspectOptions : DEFAULT_ASPECT_OPTIONS)),
    [aspectOptions],
  );
  const targetAspectRatio = useMemo(
    () => parseAspectRatioValue(selectedAspectRatio),
    [selectedAspectRatio],
  );

  // Reset crop when source changes / dialog opens
  useEffect(() => {
    if (isOpen) {
      setSelectedAspectRatio(defaultAspectRatio ?? normalizedAspectOptions[0] ?? '16:9');
      setCrop(undefined);
      setCompletedCrop(undefined);
    }
  }, [defaultAspectRatio, isOpen, normalizedAspectOptions, sourceUrl]);

  useEffect(() => {
    if (!isOpen || !imgRef.current) return;
    const img = imgRef.current;
    const nextCrop = centerInitialCrop(img.width, img.height, targetAspectRatio);
    setCrop(nextCrop);
    setCompletedCrop(pixelCropFromPercentCrop(nextCrop, img.width, img.height));
  }, [isOpen, selectedAspectRatio, targetAspectRatio]);

  const handleImageLoad = useCallback((event: React.SyntheticEvent<HTMLImageElement>) => {
    const img = event.currentTarget;
    imgRef.current = img;
    const initialCrop = centerInitialCrop(img.width, img.height, targetAspectRatio);
    setCrop(initialCrop);
    setCompletedCrop(pixelCropFromPercentCrop(initialCrop, img.width, img.height));
  }, [targetAspectRatio]);

  const handleConfirm = useCallback(async () => {
    if (!imgRef.current || !completedCrop) return;
    if (completedCrop.width === 0 || completedCrop.height === 0) return;
    setUploading(true);
    try {
      const blob = await exportCroppedBlob(imgRef.current, completedCrop);
      const filename = `background_${sourceLabel ?? 'source'}_${Date.now()}.png`;
      await onConfirmBlob(blob, filename);
      onCandidateSuccess?.(t('viewer.threeD.backgroundCropperGenerated'));
      onClose();
    } catch (err) {
      console.error('[bg-cropper] upload failed', err);
      onError?.(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }, [completedCrop, sourceLabel, onConfirmBlob, onCandidateSuccess, onError, onClose, t]);

  const displaySrc = sourceUrl ? resolveImageDisplayUrl(sourceUrl) : '';

  return (
    <UiModal
      isOpen={isOpen}
      title={t('viewer.threeD.backgroundCropperTitle', {
        aspect: selectedAspectRatio,
        source: sourceLabel ? ` · ${sourceLabel}` : '',
      })}
      onClose={onClose}
      widthClassName="w-[720px] max-w-[92vw]"
      footer={
        <>
          <UiButton variant="ghost" onClick={onClose} disabled={uploading}>
            {t('common.cancel')}
          </UiButton>
          <UiButton
            onClick={() => void handleConfirm()}
            disabled={uploading || !completedCrop || completedCrop.width === 0}
          >
            {uploading ? t('common.processing') : t('viewer.threeD.useAsBackgroundSource')}
          </UiButton>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-xs text-text-muted">
          {t('viewer.threeD.backgroundCropperDescription', { aspect: selectedAspectRatio })}
        </p>
        {normalizedAspectOptions.length > 1 && (
          <div className="flex flex-wrap gap-2">
            {normalizedAspectOptions.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setSelectedAspectRatio(option)}
                className={`rounded-[8px] border px-3 py-1.5 text-xs font-medium transition ${
                  selectedAspectRatio === option
                    ? 'border-cyan-200 bg-cyan-200 text-slate-950'
                    : 'border-white/10 bg-white/[0.04] text-text-secondary hover:border-white/20 hover:bg-white/[0.08]'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        )}
        {displaySrc && (
          <div className="flex items-center justify-center rounded-md border border-[rgba(255,255,255,0.08)] bg-black/40 p-2">
            <ReactCrop
              className="[&_.ReactCrop__crop-selection]:!border-2 [&_.ReactCrop__crop-selection]:!border-cyan-200 [&_.ReactCrop__crop-selection]:shadow-[inset_0_0_0_1px_rgba(0,0,0,0.75),0_0_0_1px_rgba(0,0,0,0.75)]"
              crop={crop}
              onChange={(_, percentCrop) => setCrop(percentCrop)}
              onComplete={(c) => setCompletedCrop(c)}
              aspect={targetAspectRatio}
              keepSelection
            >
              <img
                src={displaySrc}
                alt={sourceLabel ?? 'source'}
                onLoad={handleImageLoad}
                className="max-h-[60vh] max-w-full"
                draggable={false}
              />
            </ReactCrop>
          </div>
        )}
      </div>
    </UiModal>
  );
}
