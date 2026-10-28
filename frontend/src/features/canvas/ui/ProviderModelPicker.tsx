// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Box } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { MediaModelRequestSchema } from '@/api/ops';
// [aipdd fork delta] 触发器 + 弹层共享内核（components/model-picker）；本文件保留
// 原导出面（含渠道胶囊 re-export），只做画布专属的领域适配。
import { ModelPickerCore } from '@/components/model-picker/ModelPickerCore';
// [aipdd fork delta] 渠道展示名与配色的唯一真源（与设置面板 / 模型广场同表）；
// 模型名去渠道前缀的 displayModelName 同表收敛。
import { channelForDisplay, displayModelName } from '@/lib/provider-labels';

import {
  isAuthoritativeEmptyCatalog,
  useFreezoneImageModels,
} from '@/features/canvas/hooks/useFreezoneImageModels';
// [aipdd fork delta] 打开选择器时强制重拉一次真实目录（refreshFreezoneVideoModels）。
import {
  refreshFreezoneVideoModels,
  useFreezoneVideoModels,
} from '@/features/canvas/hooks/useFreezoneVideoModels';
// [aipdd fork delta] 音频/文本节点的模型候选也走同一套 picker：
// audio → /freezone/audio/models，text → /model-gateway/text-models。
import { useFreezoneAudioModels } from '@/features/canvas/hooks/useFreezoneAudioModels';
import { useFreezoneTextModels } from '@/features/canvas/hooks/useFreezoneTextModels';
// [aipdd fork delta] 图片模型没有可写死的默认 id，新建节点靠「上次所选」继承。
import { writeLastImageModel } from '@/features/canvas/domain/lastImageModel';
import {
  NODE_FLOATING_PANEL_SURFACE_CLASS,
  NODE_TEXT_CONTROL_ICON_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';

const MODEL_PICKER_POPOVER_WIDTH = 260;
const MODEL_PICKER_POPOVER_CLASS =
  `nodrag nowheel fixed z-[10000] max-h-[280px] w-[260px] overflow-y-auto p-1 ${NODE_FLOATING_PANEL_SURFACE_CLASS}`;

// [aipdd fork delta] 渠道胶囊组件与 channelForDisplay 已下沉（前者到共享组件，后者到
// provider-labels 唯一真源）；这里原样 re-export，既有 import 点（ToolsPage /
// ModelPicker / ModelSelect 等）不用改地址。
export { ModelChannelBadge } from '@/components/model-picker/ModelChannelBadge';
export { channelForDisplay };

export type ProviderId =
  | 'newapi'
  | 'huimeng'
  | 'openrouter'
  | 'openai'
  | 'seedance'
  | 'minimax'
  | 'eleven'
  | 'mureka';

export interface ProviderOption {
  id: ProviderId;
  label: string;
}

export interface ModelOption {
  /** Opaque database identity used by new billing and task records. */
  catalogId?: string;
  id: string;
  providerId: ProviderId;
  apiModel: string;
  label: string;
  /**
   * [aipdd fork delta] 展示用渠道（aipdd / newapi / volcengine ...）：picker 在
   * 模型名旁渲染彩色渠道胶囊。undefined = 未声明，回退 providerId；空串 = 该
   * 条目明确不标注（如音频面板合成的「默认引擎」）。
   */
  channel?: string;
  /** [aipdd fork delta] 系统默认文本模型标记：工具页默认项回显「默认（模型名）」。 */
  isDefault?: boolean;
  resolutionOptions?: string[];
  qualityOptions?: string[];
  humanReview?: boolean;
  supportsGenerateAudio?: boolean;
  minDuration?: number | null;
  maxDuration?: number | null;
  sceneOptimizeOptions?: Array<'anime' | 'realistic'>;
  defaultSceneOptimize?: 'anime' | 'realistic' | null;
  ratioOptions?: string[];
  supportedModes?: string[];
  /**
   * [aipdd fork delta req44] 渠道在线绑定备好的提交模式（后端按 `/v1/capabilities`
   * 事实现算）。**只加不减的咨询证据**：只用来放开被「按模型名猜」藏掉的入口
   * （如 ap-minimax-h3 的首尾帧），空 / 缺失 = 没证据，不能当成能力上限。
   */
  bindingModes?: string[];
  referenceImageMax?: number | null;
  referenceVideoMax?: number | null;
  referenceAudioMax?: number | null;
  referenceFileMax?: number | null;
  referenceLinkMax?: number | null;
  referenceFileTypes?: string[];
  referenceAudioMinSeconds?: number | null;
  referenceAudioMaxSeconds?: number | null;
  referenceAudioTotalMinSeconds?: number | null;
  /** 参考音频总时长上限（秒，可为小数）；没配时前端按 15.2s 兜底。 */
  referenceAudioTotalMaxSeconds?: number | null;
  referenceVideoMinSeconds?: number | null;
  referenceVideoMaxSeconds?: number | null;
  referenceVideoTotalMinSeconds?: number | null;
  referenceVideoTotalMaxSeconds?: number | null;
  request?: MediaModelRequestSchema;
}

// 服务商品牌名，不跟界面语言走。
// i18n-exempt-start
export const SHARED_PROVIDERS: ProviderOption[] = [
  { id: 'newapi', label: 'LocalAPI' },
  { id: 'huimeng', label: '绘梦 / HuiMeng' },
// i18n-exempt-end
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'openai', label: 'OpenAI' },
];

// [aipdd fork delta] 以前这里有一份写死的 SHARED_MODELS 兜底模型列表与
// DEFAULT_SHARED_MODEL_ID 默认 id（含 huimeng / openai / openrouter 等官方渠道）。
// 现在图片模型只有一个来源：后台「媒体模型」目录经 /freezone/image/models 下发的
// 真实列表。管理员没配就是不配 —— 拉不到 / 没配时列表为空，由选择器显式展示
// 「加载中」或「无可用模型」，绝不拿前端写死的模型伪装可用（那正是画布图片节点
// 会闪出 OpenAI 等非约定渠道、过一会又被真实目录替换的根因）。

// Video generation models. `id` is the raw backend model id sent to
// /freezone/video/gen so we don't need a separate apiModel mapping.
export const VIDEO_PROVIDERS: ProviderOption[] = [
  { id: 'seedance', label: 'Seedance' },
  { id: 'huimeng', label: '绘梦 / HuiMeng' }, // i18n-exempt —— 服务商品牌名
];

// [aipdd fork delta] 以前这里有一份写死的 VIDEO_MODELS 兜底列表与
// DEFAULT_VIDEO_MODEL_ID。视频模型现在只有一个来源：后台「媒体模型」目录
// 经 /freezone/video/models 下发的真实列表，默认值跟随其中的默认项
// （设置里被标为默认的模型，目录按 sortOrder 升序，默认项排首）。

// [aipdd fork delta] 扩 audio/text 两个 domain：音频面板（语音/音乐共用 audio
// 目录）与脚本节点（文本模型）复用同一个 picker，不再各写一套下拉。
export type ProviderModelDomain = 'image' | 'video' | 'audio' | 'text';

interface ProviderModelPickerProps {
  selectedModelId: string;
  onChange: (modelId: string) => void;
  providers?: ProviderOption[];
  models?: ModelOption[];
  /**
   * Selects which freezone models endpoint backs the picker when no explicit
   * `models` prop is provided (`image` → /freezone/image/models, `video` →
   * /freezone/video/models). Defaults to `image` so existing image-node call
   * sites are unaffected.
   */
  domain?: ProviderModelDomain;
  className?: string;
  popoverPlacement?: 'top' | 'bottom';
  /**
   * Returns a disabled reason for a given model option, or null when the model
   * is selectable. When non-null, that option is rendered greyed-out and not
   * clickable, with the reason shown as a hover tooltip. Used by the video node
   * to block Seedance 1.0 models while reference media is attached.
   */
  getOptionDisabledReason?: (model: ModelOption) => string | null;
}

export function ProviderModelPicker({
  selectedModelId,
  onChange,
  providers: _providers = SHARED_PROVIDERS,
  models,
  domain = 'image',
  className,
  popoverPlacement = 'top',
  getOptionDisabledReason,
}: ProviderModelPickerProps) {
  const { t } = useTranslation();
  // When the caller supplies an explicit `models` prop we don't fire any API
  // request — pass `null` to both hooks so they no-op. Otherwise the active
  // hook is picked by `domain`, and the inactive one is fed `null` to stay
  // dormant. (React still calls both hooks unconditionally so the call order
  // is stable across renders.)
  const skipFetch = models ? null : undefined;
  // [aipdd fork delta] 四个 domain 各自只激活对应 hook，其余传 null 静默
  // （React 仍无条件调用全部 hook，保持顺序稳定）。
  const imageHook = useFreezoneImageModels(domain === 'image' ? skipFetch : null);
  const videoHook = useFreezoneVideoModels(domain === 'video' ? skipFetch : null);
  const audioHook = useFreezoneAudioModels(domain === 'audio' ? skipFetch : null);
  const textHook = useFreezoneTextModels(domain === 'text' ? skipFetch : null);
  const apiHook =
    domain === 'video'
      ? videoHook
      : domain === 'audio'
        ? audioHook
        : domain === 'text'
          ? textHook
          : imageHook;
  const effectiveModels = models ?? apiHook.models;
  // 后台**确实**一个模型都没配（接口成功返回空列表，不是拉取失败兜底、也不是
  // 还在加载）。此时触发器上原来会直接显示 `selectedModelId` —— 一个前端硬编码
  // 的默认 id，看着像有个模型可用，点开却是空列表。
  const catalogIsEmpty = !models && isAuthoritativeEmptyCatalog(apiHook);
  const selectedModel = effectiveModels.find((m) => m.id === selectedModelId) ?? effectiveModels[0];

  return (
    <ModelPickerCore
      className={className}
      rootClassName="min-w-0"
      groups={[
        {
          options: effectiveModels.map((model) => ({
            value: model.id,
            // [aipdd fork delta] 展示去渠道前缀（displayModelName 唯一真源）：
            // 只显示模型真名，来源由旁的渠道胶囊表达；选中值仍为完整 id。
            label: displayModelName(model.label, channelForDisplay(model)),
            channel: channelForDisplay(model),
            disabledReason: getOptionDisabledReason?.(model) ?? null,
          })),
        },
      ]}
      selectedValue={selectedModel?.id ?? null}
      onSelect={(modelId) => {
        onChange(modelId);
        // [aipdd fork delta] 图片 domain 记下这次选择，下一个新建的图片节点直接
        // 接着用 —— 图片模型没有可写死的默认 id，只能靠「用户自选 + 目录首模型」
        // 两个来源。
        if (domain === 'image') writeLastImageModel(modelId);
      }}
      triggerLabel={
        selectedModel
          ? displayModelName(selectedModel.label, channelForDisplay(selectedModel))
          : apiHook.isLoading
            ? t('common.loading')
            : catalogIsEmpty
              ? t('modelParams.noModelsAvailable')
              : selectedModelId
      }
      triggerClassName={`${NODE_TEXT_CONTROL_TRIGGER_CLASS} min-w-0 max-w-full`}
      triggerLabelClassName="min-w-0 truncate font-medium"
      triggerIcon={<Box className={`${NODE_TEXT_CONTROL_ICON_CLASS} shrink-0`} />}
      triggerChannel={selectedModel ? channelForDisplay(selectedModel) : undefined}
      stopPropagation
      onOpen={() => {
        // [aipdd fork delta] 点开视频模型选择器时强制重拉一次真实目录（后台改了
        // 默认 / 新增模型能立即反映）；refreshFreezoneVideoModels 会保留当前列表、
        // 拉到后原地替换，不会闪空。调用方显式喂了 models 时不触发，尊重受控用法。
        if (domain === 'video' && !models) refreshFreezoneVideoModels();
      }}
      chevronClassName="h-3 w-3 shrink-0 text-text-muted/90"
      popoverClassName={MODEL_PICKER_POPOVER_CLASS}
      portal
      placement={popoverPlacement}
      popoverWidth={MODEL_PICKER_POPOVER_WIDTH}
      listClassName="flex flex-col gap-0.5"
      emptyContent={
        <span className="px-3 py-2 text-xs text-text-muted">
          {apiHook.isLoading ? t('common.loading') : t('modelPicker.empty')}
        </span>
      }
      optionTone="canvas"
    />
  );
}
