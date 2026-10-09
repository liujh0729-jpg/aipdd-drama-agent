// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ExtraParamDefinition, ImageModelDefinition } from '../../types';

export const OPENAI_GPT_IMAGE_2_MODEL_ID = 'openai/gpt-image-2';

const ASPECT_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3'] as const;

const QUALITY_PARAM: ExtraParamDefinition = {
  key: 'quality',
  label: '画质',
  type: 'enum',
  description: 'gpt-image-2 quality preset',
  defaultValue: 'medium',
  options: [
    { value: 'low', label: 'low (省钱)' },
    { value: 'medium', label: 'medium' },
    { value: 'high', label: 'high (慢)' },
    { value: 'auto', label: 'auto' },
  ],
};

export const imageModel: ImageModelDefinition = {
  id: OPENAI_GPT_IMAGE_2_MODEL_ID,
  mediaType: 'image',
  displayName: 'GPT-Image-2 (OpenAI)',
  providerId: 'openai',
  description: '原生 mask edit 最强；最贵；OPENAI_API_KEY 必填',
  eta: '60s',
  expectedDurationMs: 60000,
  defaultAspectRatio: '1:1',
  defaultResolution: '2K',
  aspectRatios: ASPECT_RATIOS.map((v) => ({ value: v, label: v })),
  resolutions: [
    { value: '1K', label: '1K' },
    { value: '2K', label: '2K' },
    { value: '4K', label: '4K' },
  ],
  extraParamsSchema: [QUALITY_PARAM],
  defaultExtraParams: { quality: 'medium' },
  resolveRequest: ({ referenceImageCount }) => ({
    requestModel: OPENAI_GPT_IMAGE_2_MODEL_ID,
    modeLabel: referenceImageCount > 0 ? '编辑' : '生成',
  }),
};
