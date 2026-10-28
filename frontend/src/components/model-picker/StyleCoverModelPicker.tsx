// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「AI 生成封面」的图片模型选择器：画布风格图墙与设置→风格管理
// 共用。触发器与候选项都带渠道彩色胶囊（AIPDD / LocalAPI / VolcEngine ...），生成前就
// 能看到模型出处；清单来自 /style-gallery/image-models，与生成端同一解析口径——
// 能选到的模型就是生成时可解析执行的模型。
import { Box } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { FreezoneImageModelInfo } from '@/api/ops';
import { ModelPickerCore } from '@/components/model-picker/ModelPickerCore';
import { channelForDisplay } from '@/lib/provider-labels';

// portal 到 body 的深色浮层：样式与画布节点浮层同款，但去掉画布专属类
// （nodrag / canvas-node-transient-ui 只在画布缩放语义下有意义）。
const POPOVER_CLASS =
  'fixed z-[10000] max-h-[280px] w-[260px] overflow-y-auto rounded-[10px] border border-white/[0.12] bg-[#282828]/96 p-1 shadow-[0_14px_34px_rgba(0,0,0,0.42)] backdrop-blur-md';

const TRIGGER_TONES = {
  canvas:
    'inline-flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-[6px] bg-white/[0.08] px-2 text-[11px] font-medium text-text-dark transition-colors hover:bg-white/[0.14] disabled:cursor-not-allowed disabled:opacity-50',
  settings:
    'inline-flex h-8 max-w-full min-w-0 items-center gap-1.5 rounded-md border border-border bg-white/[0.02] px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50',
} as const;

export interface StyleCoverModelPickerProps {
  models: FreezoneImageModelInfo[];
  /** 当前所选模型 id；失效或为空时内部回落目录首项（与画布选择器同一约定）。 */
  selectedId: string | null;
  onSelect: (modelId: string) => void;
  isLoading?: boolean;
  disabled?: boolean;
  variant?: keyof typeof TRIGGER_TONES;
  ariaLabel: string;
  className?: string;
}

export function StyleCoverModelPicker({
  models,
  selectedId,
  onSelect,
  isLoading = false,
  disabled = false,
  variant = 'canvas',
  ariaLabel,
  className,
}: StyleCoverModelPickerProps) {
  const { t } = useTranslation();
  const selected = models.find((item) => item.id === selectedId) ?? models[0] ?? null;
  const isEmpty = !isLoading && models.length === 0;

  return (
    <ModelPickerCore
      className={className}
      rootClassName="min-w-0"
      groups={[
        {
          options: models.map((model) => ({
            value: model.id,
            label: model.label,
            channel: channelForDisplay(model),
          })),
        },
      ]}
      selectedValue={selected?.id ?? null}
      onSelect={onSelect}
      disabled={disabled || isEmpty}
      withListboxRole
      ariaLabel={ariaLabel}
      triggerLabel={
        selected?.label ??
        (isLoading
          ? t('common.loading')
          : isEmpty
            ? t('modelParams.noModelsAvailable')
            : '')
      }
      triggerClassName={TRIGGER_TONES[variant]}
      triggerLabelClassName="min-w-0 truncate font-medium"
      triggerIcon={<Box className="h-3.5 w-3.5 shrink-0 text-text-muted/90" />}
      triggerChannel={selected ? channelForDisplay(selected) : undefined}
      chevronClassName="h-3 w-3 shrink-0 text-text-muted/90"
      popoverClassName={POPOVER_CLASS}
      portal
      placement="top"
      popoverWidth={260}
      listClassName="flex flex-col gap-0.5"
      emptyContent={
        <span className="px-3 py-2 text-xs text-text-muted">
          {isLoading ? t('common.loading') : t('modelPicker.empty')}
        </span>
      }
      optionTone="canvas"
    />
  );
}

/**
 * 目录条目按「上次所选 → 目录首项」回落：两个管理入口共用同一条选择语义，
 * 目录加载完成前返回 null，调用方据此禁用生成按钮。
 */
export function resolveStyleCoverModel(
  models: FreezoneImageModelInfo[],
  modelId: string | null,
): FreezoneImageModelInfo | null {
  return models.find((item) => item.id === modelId) ?? models[0] ?? null;
}