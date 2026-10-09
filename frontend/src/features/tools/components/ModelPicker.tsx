// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具页模型选择器：只吃真实目录（video / image / audio / music 四类），
// 不合成写死列表、不设「默认引擎」占位。目录按 sortOrder 排序（「设置 → 默认生成
// 模型」里设的默认模型排首），就绪后预选首项、下拉即显示默认模型名；目录为空时
// 提示先去网关配置。下拉本体是 ModelSelect（候选旁渲染渠道彩色胶囊）。
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useFreezoneAudioModels } from "@/features/canvas/hooks/useFreezoneAudioModels";
import { useFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";
// [aipdd fork delta req46] 音乐创作读 music 目录（/freezone/music/models）。
import { useFreezoneMusicModels } from "@/features/canvas/hooks/useFreezoneMusicModels";
import { useFreezoneVideoModels } from "@/features/canvas/hooks/useFreezoneVideoModels";
import { displayModelName } from "@/lib/provider-labels";
import {
  channelForDisplay,
  type ModelOption,
} from "@/features/canvas/ui/ProviderModelPicker";
import { ModelSelect } from "@/features/tools/components/ModelSelect";

// [aipdd fork delta req46] music = 后台「音乐」类型目录：音乐创作不经网关，
// 候选只能来自设置页 mediaType=music 的条目。
export type ModelPickerKind = "video" | "image" | "audio" | "music";

export interface ModelPickerProps {
  project: string;
  /** 读取哪个模型目录：视频 / 图像 / 音频 / 音乐。 */
  kind: ModelPickerKind;
  /** 选中条目的目录 id；null = 未选（目录就绪后会预选首项，即后台设置的默认模型）。 */
  value: string | null;
  /** 选中回调：整条目录条目（含 apiModel / providerId / catalogId）；未选回传 null。 */
  onChange: (picked: ModelOption | null) => void;
  /** 只保留满足该谓词的模型（例如全能参考 omni 模型）。默认展示全部。 */
  filter?: (model: ModelOption) => boolean;
  /** 无障碍名（触发器与候选项列表）；缺省回落通用的「模型」。 */
  ariaLabel?: string;
}

export function ModelPicker({
  project,
  kind,
  value,
  onChange,
  filter,
  ariaLabel,
}: ModelPickerProps) {
  const { t } = useTranslation();
  // 四个目录 hook 都调用，但只让对应 kind 的目录真正拉取（其余传 null 关闭）。
  const video = useFreezoneVideoModels(kind === "video" ? project : null);
  const image = useFreezoneImageModels(kind === "image" ? project : null);
  const audio = useFreezoneAudioModels(kind === "audio" ? project : null);
  const music = useFreezoneMusicModels(kind === "music" ? project : null);
  const { models, isLoading } =
    kind === "video"
      ? video
      : kind === "image"
        ? image
        : kind === "music"
          ? music
          : audio;
  // filter 由调用方以稳定引用传入（模块级常量 / useMemo），models 来自共享 store。
  const options = useMemo(
    () => (filter ? models.filter(filter) : models),
    [filter, models],
  );

  // 目录就绪后把父组件的选择与当前显示对齐，避免「下拉里显示着某模型、
  // 提交时父组件却是 null」的静默不一致：未选或所选已下线 → 预选目录首项
  // （后台按 sortOrder 排序，「设置 → 默认生成模型」里设的默认模型排首）。
  useEffect(() => {
    if (isLoading) return;
    const pickedValid =
      value != null && value !== "" && options.some((m) => m.id === value);
    if (pickedValid) return;
    if (options.length === 0) {
      if (value != null) onChange(null);
      return;
    }
    onChange(options[0]);
  }, [isLoading, onChange, options, value]);

  if (isLoading) {
    return <div className="text-xs leading-relaxed text-muted-foreground">{t("tools.common.modelLoading")}</div>;
  }
  if (options.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-white/[0.02] px-3 py-2 text-xs leading-relaxed text-muted-foreground">
        {t("tools.common.modelEmpty")}
      </div>
    );
  }

  const selectedId =
    value && options.some((m) => m.id === value) ? value : (options[0]?.id ?? "");

  // 目录条目 -> 下拉候选（渠道胶囊取 channel 缺省回退 providerId 的画布同口径）。
  return (
    <ModelSelect
      value={selectedId}
      onChange={(next) => {
        onChange(options.find((m) => m.id === next) ?? null);
      }}
      groups={[
        {
          options: options.map((m) => ({
            value: m.id,
            // [aipdd fork delta] 展示去渠道前缀（displayModelName 唯一真源）；
            // 媒体模型目录 label 本就干净，这里是保险统一，无副作用。
            label: displayModelName(m.label, channelForDisplay(m)),
            channel: channelForDisplay(m),
          })),
        },
      ]}
      ariaLabel={ariaLabel ?? t("tools.common.model")}
    />
  );
}
