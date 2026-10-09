// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具页通用模型下拉：原生 <select> 的 option 无法渲染彩色渠道
// 胶囊（画布 ProviderModelPicker 同款），这里用自定义 combobox 补齐 —— 触发器与
// 每个候选项都在模型名旁标注所属渠道（展示名/配色与画布同一真源）。
// [aipdd fork delta] 触发器 + 弹层共享内核（components/model-picker）；本文件保留
// 对外 props / 类型与 class 常量，只承担工具页的表单适配。
import { ModelPickerCore } from "@/components/model-picker/ModelPickerCore";

export interface ModelSelectOption {
  /** 提交/回传用的稳定值；"" = 空值语义（如「跟随系统默认」）。 */
  value: string;
  label: string;
  /** 展示用渠道；undefined 或空串 = 不标注（空值项等）。 */
  channel?: string;
}

export interface ModelSelectGroup {
  /** 分组标题（如「Seedance 系列」）；缺省渲染为无标题组。 */
  label?: string;
  options: ModelSelectOption[];
}

export interface ModelSelectProps {
  value: string;
  onChange: (value: string) => void;
  groups: ModelSelectGroup[];
  /** 无障碍名（触发器与候选项列表共用）；页面上另有可见 Label。 */
  ariaLabel: string;
  /** value 为空且候选里没有空值项时的触发器占位文案（如「请选择模型」）。 */
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

const MODEL_SELECT_TRIGGER_CLASS =
  "flex h-9 w-full items-center gap-2 rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none transition-colors focus:border-primary disabled:cursor-not-allowed disabled:opacity-50";

export function ModelSelect({
  value,
  onChange,
  groups,
  ariaLabel,
  placeholder,
  disabled,
  className,
}: ModelSelectProps) {
  const flatOptions = groups.flatMap((group) => group.options);
  // 值失配（如目录刷新导致所选下线）时回落首项，与 ModelPicker 的预选口径一致；
  // 但 value 为空且候选里没有空值项时保持「未选择」——显示 placeholder 而不是
  // 误显首项（调用方没有默认项语义时，触发器显示必须与提交值一致）。
  const selected =
    flatOptions.find((option) => option.value === value) ??
    (value ? flatOptions[0] ?? null : null);

  return (
    <ModelPickerCore
      className={className}
      groups={groups}
      selectedValue={selected?.value ?? null}
      onSelect={onChange}
      triggerLabel={selected?.label ?? placeholder ?? ""}
      triggerClassName={MODEL_SELECT_TRIGGER_CLASS}
      triggerChannel={selected?.channel || undefined}
      withListboxRole
      ariaLabel={ariaLabel}
      disabled={disabled}
      chevronClassName="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground/90 transition-transform"
      chevronRotateOnOpen
      popoverClassName="absolute left-0 top-full z-50 mt-1 max-h-[280px] w-full overflow-y-auto rounded-lg border border-[var(--ui-border-strong)] bg-[#282828]/96 p-1 shadow-[0_14px_34px_rgba(0,0,0,0.42)] backdrop-blur-md"
      optionTone="tools"
    />
  );
}