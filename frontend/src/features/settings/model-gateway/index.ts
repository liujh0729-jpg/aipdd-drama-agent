// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：模型网关设置区统一入口（自 settings-dialog.tsx 拆分）。

export { ModelConfigSection } from "./model-config-section";
export { FieldRow } from "./field-row";
export {
  getRequestErrorMessage,
  officialSiteUrl,
  providersSupportingCapability,
} from "./shared";
