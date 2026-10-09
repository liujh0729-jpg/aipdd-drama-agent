// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * 「没有模型选择器」的图片能力面板（360 全景、宫格动作）如何解析模型。
 *
 * 这类面板不让用户挑模型 / 尺寸 / 画质，由调用方按功能规则选定目录模型
 * （[aipdd fork delta] 360 与宫格都取目录默认模型，即「默认生成模型」里设定的
 * 默认 image）。之前一个时期报价和提交各写各的：报价按选定模型 + 收敛后的
 * size/quality 算，提交却一个都不带，后端于是用自己的
 * `FREEZONE_DEFAULT_IMAGE_MODEL` 和固定 2K 去跑。结果是「目录首模型 ≠ 后端默认
 * 模型」或「该模型不配 2K」时，界面上显示的能力档位与真正执行的完全是两回事，
 * 后台的媒体模型配置在这两个面板等于没生效。
 *
 * [aipdd fork delta] WP-D: 报价随额度体系一起拆掉了，这里保留的还是同一个价值——
 * 提交字段由模型自己的档位声明解析出来，不再和界面显示的能力走散。
 */

import {
  pickAllowedOption,
  resolveModelQualityOptions,
  resolveModelSizeOptions,
  type MediaModelCapabilities,
} from '@/features/canvas/domain/mediaModelOptions';

/** 解析所需的目录条目字段（`FreezoneImageModelInfo` 与兜底 `ModelOption` 都满足）。 */
export interface FixedFeatureModel extends MediaModelCapabilities {
  catalogId?: string;
  apiModel?: string;
}

/** 提交给 `/freezone/scene-360`、`/freezone/template-edit` 的模型相关字段。 */
export interface FixedFeatureSubmitFields {
  /**
   * 裸 `apiModel`，不带 provider 前缀。
   *
   * 这两条老路由各自推断 provider 的方式不同（scene-360 恒定按 newapi 走，
   * template-edit 才认 `provider/model`），带前缀在 scene-360 那边会被整串当成
   * 模型名。统一送裸名，provider 交给后端按各自的老规则定。
   */
  model?: string;
  /** 目录身份。带上后端才会认出这是目录里的哪个条目。 */
  catalogId?: string;
  imageSize: string;
  /** 目录声明了 qualityOptions 才有；没声明就不下发。 */
  quality?: string;
}

/** 面板不提供选择器时想要的默认档位（仍需落在模型实际支持的档位里）。 */
const PREFERRED_SIZE = '2K';
const PREFERRED_QUALITY = 'medium';

export function resolveFixedFeatureModelRequest(
  model: FixedFeatureModel | null | undefined,
): { submit: FixedFeatureSubmitFields } {
  // [aipdd fork delta] 档位取自模型自己的声明（resolveModelSizeOptions 已不再
  // 套通用兜底）；这类面板没有选择器，模型真没声明时退而求其次用常用默认档位，
  // 而不是把空串发给后端。
  const imageSize = pickAllowedOption(
    PREFERRED_SIZE,
    resolveModelSizeOptions(model),
    PREFERRED_SIZE,
  );
  const qualityOptions = resolveModelQualityOptions(model);
  const quality =
    qualityOptions.length > 0
      ? pickAllowedOption(PREFERRED_QUALITY, qualityOptions)
      : undefined;
  const apiModel = String(model?.apiModel ?? '').trim();
  const catalogId = String(model?.catalogId ?? '').trim();

  return {
    submit: {
      ...(apiModel ? { model: apiModel } : {}),
      ...(catalogId ? { catalogId } : {}),
      imageSize,
      ...(quality ? { quality } : {}),
    },
  };
}
