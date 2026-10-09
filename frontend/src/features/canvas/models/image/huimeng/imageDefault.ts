// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ImageModelDefinition } from '../../types';

export const HUIMENG_DEFAULT_MODEL_ID = 'huimeng/default';

const ASPECT_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3'] as const;

export const imageModel: ImageModelDefinition = {
  id: HUIMENG_DEFAULT_MODEL_ID,
  mediaType: 'image',
  displayName: '惠盟 / HuiMeng (默认)',
  providerId: 'huimeng',
  description: '国内代理 (HUIMENGI_API_KEY)；具体模型由 HUIMENG_IMAGE_MODEL env 决定',
  eta: '40s',
  expectedDurationMs: 40000,
  defaultAspectRatio: '1:1',
  defaultResolution: '1K',
  aspectRatios: ASPECT_RATIOS.map((v) => ({ value: v, label: v })),
  resolutions: [
    { value: '1K', label: '1K' },
    { value: '2K', label: '2K' },
  ],
  resolveRequest: ({ referenceImageCount }) => ({
    requestModel: HUIMENG_DEFAULT_MODEL_ID,
    modeLabel: referenceImageCount > 0 ? '编辑' : '生成',
  }),
};
