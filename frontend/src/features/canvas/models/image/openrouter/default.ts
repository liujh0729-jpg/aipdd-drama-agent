// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ImageModelDefinition } from '../../types';

// `openrouter/default` is a placeholder model id; the second segment "default"
// is treated by `freezoneAiGateway.splitProviderModel` as null → backend falls
// back to NANOBANANA_MODEL env. We could enumerate concrete models here later
// (e.g. openrouter/google/gemini-3-pro-image) but that needs SuperTale config
// to expose its NANOBANANA_MODEL value to the frontend, out of scope for v1.2.
export const OPENROUTER_DEFAULT_MODEL_ID = 'openrouter/default';

const ASPECT_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '21:9'] as const;

export const imageModel: ImageModelDefinition = {
  id: OPENROUTER_DEFAULT_MODEL_ID,
  mediaType: 'image',
  displayName: 'OpenRouter (默认)',
  providerId: 'openrouter',
  description: 'OpenRouter 代理 (Gemini 系)，性价比首选；用 NANOBANANA_MODEL env 决定具体模型',
  eta: '30s',
  expectedDurationMs: 30000,
  defaultAspectRatio: '1:1',
  defaultResolution: '2K',
  aspectRatios: ASPECT_RATIOS.map((v) => ({ value: v, label: v })),
  resolutions: [
    { value: '1K', label: '1K' },
    { value: '2K', label: '2K' },
    { value: '4K', label: '4K' },
  ],
  resolveRequest: ({ referenceImageCount }) => ({
    requestModel: OPENROUTER_DEFAULT_MODEL_ID,
    modeLabel: referenceImageCount > 0 ? '编辑' : '生成',
  }),
};
