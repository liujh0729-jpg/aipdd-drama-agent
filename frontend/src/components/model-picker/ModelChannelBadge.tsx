// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 渠道彩色胶囊：模型名旁的渠道归属标注（淡色底 + 同色文字）。
// 展示名 / 配色 / 胶囊体样式都取自 @/lib/provider-labels 的唯一真源；未登记的
// 渠道回落中性样式，空串 = 明确不标注（不渲染）。画布与工具页共用。
import { cn } from "@/lib/utils";
import {
  MODEL_CHANNEL_BADGE_CLASS,
  providerChannelBadgeClass,
  providerChannelLabel,
} from "@/lib/provider-labels";

export function ModelChannelBadge({
  channel,
  className,
}: {
  channel: string;
  className?: string;
}) {
  if (!channel) return null;
  return (
    <span
      data-channel={channel}
      className={cn(MODEL_CHANNEL_BADGE_CLASS, providerChannelBadgeClass(channel), className)}
    >
      {providerChannelLabel(channel)}
    </span>
  );
}