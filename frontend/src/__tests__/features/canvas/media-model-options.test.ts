// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

import { describe, expect, it } from 'vitest';

import {
  formatResolutionLabel,
  pickAllowedOption,
  resolveModelAspectOptions,
  resolveModelQualityOptions,
  resolveModelSizeOptions,
} from '@/features/canvas/domain/mediaModelOptions';

describe('媒体模型公共选项归一化', () => {
  it('把历史 adaptive 比例收敛为唯一的 auto 选项', () => {
    expect(
      resolveModelAspectOptions({ ratioOptions: ['adaptive', 'Auto', '16:9'] }),
    ).toEqual(['auto', '16:9']);
  });

  it('保留后台配置的分辨率档位顺序', () => {
    expect(resolveModelSizeOptions({ resolutionOptions: ['720p', '1080p'] })).toEqual([
      '720p',
      '1080p',
    ]);
  });

  it('分辨率只在显示层统一成小写', () => {
    expect(formatResolutionLabel('4K')).toBe('4k');
    expect(formatResolutionLabel('1080P')).toBe('1080p');
  });

  // [aipdd fork delta] 档位的事实来源只有模型自己的声明一处：前端不再对未声明的
  // 模型套通用兜底档位，否则界面会列出一排目录没说过的能力。
  it('模型没声明分辨率 / 比例时不再套通用兜底档位', () => {
    expect(resolveModelSizeOptions({})).toEqual([]);
    expect(resolveModelSizeOptions(null)).toEqual([]);
    expect(resolveModelAspectOptions({ resolutionOptions: ['1K'] })).toEqual([]);
    expect(resolveModelQualityOptions({})).toEqual([]);
  });

  it('没有可收敛范围时保留已选值，不退成空串发给后端', () => {
    expect(pickAllowedOption('2K', [])).toBe('2K');
    expect(pickAllowedOption('2K', [], '1K')).toBe('2K');
    expect(pickAllowedOption('4K', ['1K', '2K'])).toBe('1K');
    expect(pickAllowedOption('', ['1K'])).toBe('1K');
  });
});
