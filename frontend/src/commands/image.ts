// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface MergeStoryboardImagesPayload {
  frameSources: string[];
  rows: number;
  cols: number;
  cellGap: number;
  outerPadding: number;
  noteHeight: number;
  fontSize: number;
  backgroundColor: string;
  maxDimension: number;
  showFrameIndex?: boolean;
  showFrameNote?: boolean;
  notePlacement?: 'overlay' | 'bottom';
  imageFit?: 'cover' | 'contain';
  frameIndexPrefix?: string;
  textColor?: string;
  frameNotes?: string[];
}

export interface StoryboardImageMetadata {
  gridRows: number;
  gridCols: number;
  frameNotes: string[];
}

export interface PrepareNodeImageSourceResult {
  imagePath: string;
  previewImagePath: string;
  aspectRatio: string;
}

export interface CropImageSourcePayload {
  source: string;
  aspectRatio?: string;
  cropX?: number;
  cropY?: number;
  cropWidth?: number;
  cropHeight?: number;
}

export interface MergeStoryboardImagesResult {
  imagePath: string;
  canvasWidth: number;
  canvasHeight: number;
  cellWidth: number;
  cellHeight: number;
  gap: number;
  padding: number;
  noteHeight: number;
  fontSize: number;
  textOverlayApplied: boolean;
}

function loadImageElement(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`failed to load image: ${source}`));
    image.src = source;
  });
}

function canvasToPng(canvas: HTMLCanvasElement): string {
  return canvas.toDataURL('image/png');
}

// 大画布(如 4096px 的合并宫格)编码 PNG 时,同步的 `toDataURL` 会把主线程卡死几
// 百 ms~数秒,并产出多 MB 的 base64 串(后续进 state / 上传解码 / 渲染都重复背
// 这串)。改用异步 `toBlob`:编码不阻塞主线程,且返回轻量 `blob:` 对象 URL。
// 下游 `loadImageElement` / `uploadLocalImageToBackend`(fetch 分支)/ 预览渲染都
// 兼容 blob: URL。toBlob 不可用或返回空时退回同步 toDataURL。
async function canvasToPngObjectUrl(canvas: HTMLCanvasElement): Promise<string> {
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob((result) => resolve(result), 'image/png');
  });
  if (!blob) {
    return canvas.toDataURL('image/png');
  }
  return URL.createObjectURL(blob);
}

function drawImageCover(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  x: number,
  y: number,
  width: number,
  height: number,
): void {
  const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
  const sw = width / scale;
  const sh = height / scale;
  const sx = (image.naturalWidth - sw) / 2;
  const sy = (image.naturalHeight - sh) / 2;
  ctx.drawImage(image, sx, sy, sw, sh, x, y, width, height);
}

function drawImageContain(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  x: number,
  y: number,
  width: number,
  height: number,
): void {
  const scale = Math.min(width / image.naturalWidth, height / image.naturalHeight);
  const dw = image.naturalWidth * scale;
  const dh = image.naturalHeight * scale;
  ctx.drawImage(image, x + (width - dw) / 2, y + (height - dh) / 2, dw, dh);
}

function downloadDataUrl(dataUrl: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = dataUrl;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

function bytesToDataUrl(bytes: Uint8Array, mime = 'image/png'): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('failed to read image bytes'));
    reader.readAsDataURL(new Blob([bytes], { type: mime }));
  });
}

export async function splitImage(
  imageBase64: string,
  rows: number,
  cols: number,
  lineThickness = 0,
): Promise<string[]> {
  return splitImageSource(imageBase64, rows, cols, lineThickness);
}

export async function splitImageSource(
  source: string,
  rows: number,
  cols: number,
  _lineThickness = 0,
): Promise<string[]> {
  const image = await loadImageElement(source);
  const cellWidth = Math.floor(image.naturalWidth / cols);
  const cellHeight = Math.floor(image.naturalHeight / rows);
  const outputs: string[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const canvas = document.createElement('canvas');
      canvas.width = cellWidth;
      canvas.height = cellHeight;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('canvas is unavailable');
      ctx.drawImage(
        image,
        col * cellWidth,
        row * cellHeight,
        cellWidth,
        cellHeight,
        0,
        0,
        cellWidth,
        cellHeight,
      );
      outputs.push(canvasToPng(canvas));
    }
  }
  return outputs;
}

export async function mergeStoryboardImages(
  payload: MergeStoryboardImagesPayload,
): Promise<MergeStoryboardImagesResult> {
  const images = await Promise.all(payload.frameSources.map(loadImageElement));
  const rows = Math.max(1, payload.rows);
  const cols = Math.max(1, payload.cols);
  const gap = Math.max(0, payload.cellGap);
  const padding = Math.max(0, payload.outerPadding);
  const first = images[0];
  const cellRatio = first ? first.naturalWidth / first.naturalHeight : 16 / 9;
  const rawCellWidth = Math.max(64, Math.floor((payload.maxDimension - padding * 2 - gap * (cols - 1)) / cols));
  const cellWidth = rawCellWidth;
  const cellHeight = Math.max(64, Math.round(cellWidth / cellRatio));
  const noteHeight = payload.showFrameNote ? Math.max(0, payload.noteHeight) : 0;
  const canvasWidth = padding * 2 + cols * cellWidth + (cols - 1) * gap;
  const canvasHeight = padding * 2 + rows * (cellHeight + noteHeight) + (rows - 1) * gap;
  const canvas = document.createElement('canvas');
  canvas.width = canvasWidth;
  canvas.height = canvasHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas is unavailable');
  ctx.fillStyle = payload.backgroundColor || '#000';
  ctx.fillRect(0, 0, canvasWidth, canvasHeight);
  ctx.fillStyle = payload.textColor || '#fff';
  ctx.font = `${Math.max(10, payload.fontSize)}px sans-serif`;
  images.forEach((image, index) => {
    const row = Math.floor(index / cols);
    const col = index % cols;
    const x = padding + col * (cellWidth + gap);
    const y = padding + row * (cellHeight + noteHeight + gap);
    if (payload.imageFit === 'contain') {
      drawImageContain(ctx, image, x, y, cellWidth, cellHeight);
    } else {
      drawImageCover(ctx, image, x, y, cellWidth, cellHeight);
    }
    if (payload.showFrameIndex) {
      ctx.fillText(`${payload.frameIndexPrefix ?? ''}${index + 1}`, x + 8, y + 18);
    }
    if (payload.showFrameNote && noteHeight > 0) {
      ctx.fillText(payload.frameNotes?.[index] ?? '', x + 8, y + cellHeight + Math.min(noteHeight - 4, payload.fontSize + 4));
    }
  });
  return {
    imagePath: await canvasToPngObjectUrl(canvas),
    canvasWidth,
    canvasHeight,
    cellWidth,
    cellHeight,
    gap,
    padding,
    noteHeight,
    fontSize: payload.fontSize,
    textOverlayApplied: Boolean(payload.showFrameIndex || payload.showFrameNote),
  };
}

export async function readStoryboardImageMetadata(
  _source: string,
): Promise<StoryboardImageMetadata | null> {
  return null;
}

export async function embedStoryboardImageMetadata(
  source: string,
  _metadata: StoryboardImageMetadata,
): Promise<string> {
  return source;
}

export async function prepareNodeImageSource(
  source: string,
  maxPreviewDimension = 512,
): Promise<PrepareNodeImageSourceResult> {
  const image = await loadImageElement(source);
  const aspectRatio = `${image.naturalWidth}:${image.naturalHeight}`;
  if (Math.max(image.naturalWidth, image.naturalHeight) <= maxPreviewDimension) {
    return { imagePath: source, previewImagePath: source, aspectRatio };
  }
  const scale = maxPreviewDimension / Math.max(image.naturalWidth, image.naturalHeight);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(image.naturalWidth * scale);
  canvas.height = Math.round(image.naturalHeight * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas is unavailable');
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  return { imagePath: source, previewImagePath: canvasToPng(canvas), aspectRatio };
}

export async function prepareNodeImageBinary(
  bytes: Uint8Array,
  extension = 'png',
  maxPreviewDimension = 512,
): Promise<PrepareNodeImageSourceResult> {
  const mime = extension.toLowerCase().includes('jpg') || extension.toLowerCase().includes('jpeg')
    ? 'image/jpeg'
    : 'image/png';
  const dataUrl = await bytesToDataUrl(bytes, mime);
  return prepareNodeImageSource(dataUrl, maxPreviewDimension);
}

export async function cropImageSource(payload: CropImageSourcePayload): Promise<string> {
  const image = await loadImageElement(payload.source);
  const sx = payload.cropX ?? 0;
  const sy = payload.cropY ?? 0;
  const sw = payload.cropWidth ?? image.naturalWidth;
  const sh = payload.cropHeight ?? image.naturalHeight;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(sw));
  canvas.height = Math.max(1, Math.round(sh));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas is unavailable');
  ctx.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return canvasToPng(canvas);
}

export async function loadImage(filePath: string): Promise<string> {
  return filePath;
}

export async function persistImageSource(source: string): Promise<string> {
  return source;
}

export async function persistImageBinary(
  bytes: Uint8Array,
  extension = 'png',
): Promise<string> {
  const mime = extension.toLowerCase().includes('jpg') || extension.toLowerCase().includes('jpeg')
    ? 'image/jpeg'
    : 'image/png';
  return bytesToDataUrl(bytes, mime);
}

export async function saveImageSourceToDownloads(
  source: string,
  suggestedFileName = 'freezone-image.png',
): Promise<string> {
  downloadDataUrl(source, suggestedFileName);
  return source;
}

export async function saveImageSourceToPath(
  source: string,
  targetPath: string,
): Promise<string> {
  downloadDataUrl(source, targetPath.split('/').pop() || 'freezone-image.png');
  return targetPath;
}

export async function saveImageSourceToDirectory(
  source: string,
  targetDir: string,
  suggestedFileName = 'freezone-image',
): Promise<string> {
  const filename = suggestedFileName.includes('.') ? suggestedFileName : `${suggestedFileName}.png`;
  downloadDataUrl(source, filename);
  return `${targetDir}/${filename}`;
}

export async function saveImageSourceToAppDebugDir(
  source: string,
  _category = 'grid',
  suggestedFileName = 'freezone-image.png',
): Promise<string> {
  return saveImageSourceToDownloads(source, suggestedFileName);
}

export async function copyImageSourceToClipboard(source: string): Promise<void> {
  if ('ClipboardItem' in window && source.startsWith('data:')) {
    const response = await fetch(source);
    const blob = await response.blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    return;
  }
  await navigator.clipboard.writeText(source);
}
