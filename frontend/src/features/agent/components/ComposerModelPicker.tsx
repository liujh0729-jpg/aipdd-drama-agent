// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 输入区模型选择：对话类型（文字/图片/视频）+ 对应模型下拉。
// 候选来自公共网关目录（model-plaza），每个候选标注模型来源（渠道/提供商）。
// 选择即保存到 Agent 设置（text_model / image_model / video_model）。
//
// [aipdd fork delta] 两条本文件存在的理由（用户实测反馈）：
// 1. 「跟随系统默认」项必须与同名候选项字面可区分。默认模型本身就在候选列表里
//    （现网实测：视频默认 ap-minimax-h3、图片默认 ap-image-2.0 都是候选），两行渲染成一模
//    一样的「模型名 · 渠道」时，用户看到的就是「两个 MiniMax H3」，分不清该选哪个。
// 2. 图片/视频回合同样必须有文本模型（后端每个回合都由它理解与发起生成），所以解析不到时
//    就地给一个文本模型下拉，而不是把用户丢到设置页——那是一条在当前页面走不通的死路。
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Cpu, Image as ImageIcon, Type, Video } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useAgentSettings,
  useUpdateAgentSettings,
  type AgentDialogMode,
} from "@/lib/queries/agent";
import {
  useModelPlaza,
  type PlazaCapability,
  type PlazaModel,
} from "@/lib/queries/model-plaza";
import {
  displayModelName,
  localizedBrandName,
} from "@/lib/provider-labels";
import { cn } from "@/lib/utils";

import { agentErrorMessage } from "../core";

/** 「跟随系统默认」选项的哨兵值（下拉不允许空串选项，提交时再转回空串）。 */
const DEFAULT_OPTION = "__default__";

type SettingsField = "text_model" | "image_model" | "video_model";
type DefaultField = "default_text_model" | "default_image_model" | "default_video_model";

/** 每个对话类型对应的设置字段与模型目录能力。 */
const MODE_CONFIG: Record<
  AgentDialogMode,
  { field: SettingsField; defaultField: DefaultField; capability: PlazaCapability }
> = {
  text: { field: "text_model", defaultField: "default_text_model", capability: "text" },
  image: { field: "image_model", defaultField: "default_image_model", capability: "image" },
  video: { field: "video_model", defaultField: "default_video_model", capability: "video" },
};

const MODE_ICONS: Record<AgentDialogMode, LucideIcon> = {
  text: Type,
  image: ImageIcon,
  video: Video,
};

/** 模型来源：优先渠道名，其次提供商 / 连接器。 */
function modelSource(model: PlazaModel): string {
  // [aipdd fork delta] 后端渠道名里写死的品牌中文按界面语言覆盖（en・vi 不冒
  // 中文），其余渠道仍用后端下发的渠道名。
  return (
    localizedBrandName(model.provider) ||
    str(model.channelName) ||
    str(model.provider) ||
    str(model.connector)
  );
}

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** 关闭态 / 候选行共用的展示文案：模型名 + 来源（有来源时）。
 *  模型名去渠道前缀收敛到唯一真源（@/lib/provider-labels 的 displayModelName）：
 *  复合调用名（"aipdd::ap-glm-5.2"）只显示模型真名——来源已有单独一栏，把
 *  "aipdd::" 暴露给用户只会徒增噪音；value 仍保留完整复合名，保证后端可调用。 */
function optionLabel(name: string, source: string): string {
  const display = displayModelName(name, source);
  if (!display) return "-";
  return source ? `${display} · ${source}` : display;
}

/** 触发器宽度：文本模型下拉只在「缺文本模型」时与产出模型下拉并列，那时装不下 340px。 */
const WIDE_TRIGGER = "h-7 w-[340px] min-w-0 max-w-[46vw] text-[11px]";
const NARROW_TRIGGER = "h-7 w-[190px] min-w-0 max-w-[26vw] text-[11px]";

/** 一种对话类型的模型下拉：候选来自网关目录，选择即写入对应的 Agent 设置字段。 */
function ModelSelect({
  dialogMode,
  triggerClass,
  badge,
  hint,
}: {
  dialogMode: AgentDialogMode;
  triggerClass: string;
  badge?: string;
  hint?: string;
}) {
  const { t } = useTranslation();
  const settingsQuery = useAgentSettings();
  const plazaQuery = useModelPlaza("gateway");
  const update = useUpdateAgentSettings();

  const settings = settingsQuery.data?.data;
  const config = MODE_CONFIG[dialogMode];
  const current = str(settings?.[config.field]);
  const defaultModel = str(settings?.[config.defaultField]);

  // 模型名 → 来源映射（同名多条目取第一个非空来源）。
  const sourceByModel = useMemo(() => {
    const map = new Map<string, string>();
    for (const model of plazaQuery.data?.data.models ?? []) {
      const name = str(model.modelName);
      const source = modelSource(model);
      if (name && source && !map.has(name)) map.set(name, source);
    }
    return map;
  }, [plazaQuery.data]);

  // 候选 = 目录里声明该能力的模型名（去重、排序）；当前值不在候选时补一条，
  // 避免存量自定义模型名在下拉里「消失」而选不回。
  const options = useMemo(() => {
    const models = plazaQuery.data?.data.models ?? [];
    const names = [
      ...new Set(
        models
          .filter((model) => model.capabilities.includes(config.capability))
          .map((model) => str(model.modelName))
          .filter(Boolean),
      ),
    ].sort((a, b) => a.localeCompare(b));
    if (current && !names.includes(current)) names.unshift(current);
    return names;
  }, [plazaQuery.data, current, config.capability]);

  // 「跟随系统默认」项的文案必须与同名候选项字面区分（本文件 fork delta 第 1 条）：
  // 默认模型本身就在候选里，两行都渲染成「模型名 · 渠道」时用户看到的就是「两个
  // MiniMax H3」。关闭态取 items 里的 label，所以哨兵项的 label 也带上标注。
  const defaultTag = t("agent.chat.model.followDefault");
  const noDefaultLabel = t("agent.chat.model.noDefault");
  const defaultLabel = defaultModel
    ? `${optionLabel(defaultModel, sourceByModel.get(defaultModel) ?? "")} · ${defaultTag}`
    : noDefaultLabel;

  // items 映射供 Select.Value 在关闭态也能显示选项的展示名，而不是内部哨兵值。
  const items = useMemo(
    () => [
      { value: DEFAULT_OPTION, label: defaultLabel },
      ...options.map((name) => ({
        value: name,
        label: optionLabel(name, sourceByModel.get(name) ?? ""),
      })),
    ],
    [defaultLabel, options, sourceByModel],
  );

  const handleChange = (value: string | null) => {
    if (value === null) return;
    const next = value === DEFAULT_OPTION ? "" : value;
    if (next === current) return;
    update.mutateAsync({ [config.field]: next }).catch(async (error) => {
      toast.error(await agentErrorMessage(error));
    });
  };

  return (
    <div className="flex min-w-0 items-center gap-1.5" title={hint}>
      {badge ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">{badge}</span>
      ) : null}
      <Cpu className="size-3.5 shrink-0 text-muted-foreground" />
      <Select
        items={items}
        value={current || DEFAULT_OPTION}
        onValueChange={handleChange}
        disabled={update.isPending}
      >
        <SelectTrigger
          size="sm"
          className={triggerClass}
          title={current ? optionLabel(current, sourceByModel.get(current) ?? "") : defaultLabel}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem
            value={DEFAULT_OPTION}
            className="text-xs"
            title={t("agent.chat.model.followDefaultHint")}
          >
            <span className="truncate">
              {displayModelName(defaultModel, sourceByModel.get(defaultModel)) || noDefaultLabel}
            </span>
            {sourceByModel.get(defaultModel) ? (
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {sourceByModel.get(defaultModel)}
              </span>
            ) : null}
            {/* 默认位空着时不加标注：「未设置默认模型」再挂一个「系统默认」只会自相矛盾。 */}
            {defaultModel ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {defaultTag}
              </span>
            ) : null}
          </SelectItem>
          {options.map((name) => (
            <SelectItem key={name} value={name} className="text-xs">
              <span className="truncate">
                {displayModelName(name, sourceByModel.get(name))}
              </span>
              {sourceByModel.get(name) ? (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {sourceByModel.get(name)}
                </span>
              ) : null}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function ComposerModelPicker({
  mode,
  onModeChange,
}: {
  mode: AgentDialogMode;
  onModeChange: (mode: AgentDialogMode) => void;
}) {
  const { t } = useTranslation();
  const settingsQuery = useAgentSettings();
  const settings = settingsQuery.data?.data;

  // 文本模型在三种回合里都是必需的：后端每个回合都由它理解需求并发起生成工具调用。
  // 解析不到时（功能位没绑、用户也没选）就地补一个文本模型下拉，而不是让用户去
  // 设置页——图片/视频模式下那条路在当前页面走不通（fork delta 第 2 条）。
  const textResolved = Boolean(
    str(settings?.text_model) || str(settings?.default_text_model),
  );
  const needsTextPicker = mode !== "text" && !textResolved;

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <div className="flex shrink-0 items-center gap-0.5 rounded-full border border-border bg-background/40 p-0.5">
        {(Object.keys(MODE_CONFIG) as AgentDialogMode[]).map((item) => {
          const Icon = MODE_ICONS[item];
          return (
            <button
              key={item}
              type="button"
              title={t(`agent.chat.mode.${item}`)}
              onClick={() => onModeChange(item)}
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-colors",
                mode === item
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-3" />
              {t(`agent.chat.mode.${item}`)}
            </button>
          );
        })}
      </div>
      <ModelSelect
        dialogMode={mode}
        triggerClass={needsTextPicker ? NARROW_TRIGGER : WIDE_TRIGGER}
      />
      {needsTextPicker ? (
        <ModelSelect
          dialogMode="text"
          triggerClass={NARROW_TRIGGER}
          badge={t("agent.chat.model.textModelLabel")}
          hint={t("agent.chat.model.textNeeded")}
        />
      ) : null}
    </div>
  );
}