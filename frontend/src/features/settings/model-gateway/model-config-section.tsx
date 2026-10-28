// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：模型配置页根组件（原 ModelConfigSection）。

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import {
  useModelGatewayConfig,
  useEnableOfficial,
  useEnableCustom,
  useEnableHybrid,
  type GatewayMode,
  type NewApiDatabaseConfigInput,
} from "@/lib/queries/model-gateway";
import {
  getRequestErrorMessage,
  getResponseErrorMessage,
  GATEWAY_MODES,
  HYBRID_COMFYUI_WORKFLOWS,
  MODEL_CONFIGURATION_GUIDE_URL,
} from "./shared";
import { ProviderConnectCards } from "./provider-connect-cards";
import {
  DefaultGenerationModelsPanel,
} from "./default-generation-models-panel";
import { FeatureModelsBlock } from "./feature-models-block";
import { ProviderCatalogBlockSection } from "./provider-catalog-block";

function ModelConfigSection({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const configQuery = useModelGatewayConfig(open);
  const config = configQuery.data?.data;
  const loading = configQuery.isLoading;
  const enableOfficialMode = useEnableOfficial();
  const enableCustomMode = useEnableCustom();
  const enableHybridMode = useEnableHybrid();
  const modelGatewayMissing = config ? !config.effective.configured : false;

  const [mode, setMode] = useState<GatewayMode>("custom");
  const modeChosenByUser = useRef(false);
  // 配置加载后，把激活的 tab 同步到服务端当前 mode。
  const serverMode = config?.mode;
  useEffect(() => {
    // [aipdd fork delta] 服务端返回 official/hybrid 时钳制到 custom，避免渲染官方表单。
    if (serverMode && !modeChosenByUser.current) {
      const next: GatewayMode = GATEWAY_MODES.includes(serverMode) ? serverMode : "custom";
      setMode((current) => (current === next ? current : next));
    }
  }, [serverMode]);

  // CE 运行环境提供本地 LocalAPI 网关管理地址；初始化与下方模型映射共用该地址。
  // [aipdd fork delta req41] 初值为空：本机地址不写在代码里，等 /config 回填真值。
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const seededCustomBaseUrl =
    config?.custom?.adminBaseUrl ||
    config?.provisioner?.adminBaseUrl ||
    config?.custom?.baseUrl ||
    "";
  useEffect(() => {
    if (seededCustomBaseUrl) {
      setCustomBaseUrl((current) =>
        current === seededCustomBaseUrl ? current : seededCustomBaseUrl,
      );
    }
  }, [seededCustomBaseUrl]);

  // CE owns one local SQLite-backed LocalAPI instance. Database paths and the
  // root username are deployment details, not user-editable model settings.
  const customDatabase: NewApiDatabaseConfigInput | undefined = undefined;
  const selectedModeConfigured =
    mode === "official"
      ? Boolean(config?.official?.configured)
      : mode === "custom"
        ? Boolean(config?.custom?.configured)
        : Boolean(config?.official?.configured && config?.custom?.configured);
  const activatingMode =
    enableOfficialMode.isPending ||
    enableCustomMode.isPending ||
    enableHybridMode.isPending;

  const handleActivateMode = async () => {
    const mutation =
      mode === "official"
        ? enableOfficialMode
        : mode === "custom"
          ? enableCustomMode
          : enableHybridMode;
    try {
      const response = await mutation.mutateAsync();
      if (!response.ok) {
        toast.error(
          getResponseErrorMessage(
            response,
            t("settings.modelConfig.requestFailed"),
          ),
        );
        return;
      }
      toast.success(
        t("settings.modelConfig.modeActivated", {
          mode: t(`settings.modelConfig.modes.${mode}`),
        }),
      );
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.modelConfig.requestFailed"),
        ),
      );
    }
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            modelGatewayMissing ? "bg-amber-400" : "bg-emerald-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.modelConfig.title")}
        </h3>
        {modelGatewayMissing ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.modelConfig.gatewayWarningIconLabel")}
          />
        ) : null}
        {/* [aipdd fork delta] 移除「当前生效：自定义」徽标——单渠道形态下它只会
            打印网关模式名「自定义」，属于本地网关概念，设置页不再出现。 */}
        <a
          href={MODEL_CONFIGURATION_GUIDE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          {t("settings.modelConfig.guide")}
          <ExternalLink className="size-3" aria-hidden />
        </a>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.description")}
      </p>
      {modelGatewayMissing ? (
        <div className="mt-3 flex gap-2 rounded-md border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          <AlertTriangle
            className="mt-0.5 size-3.5 shrink-0 text-amber-300"
            aria-hidden
          />
          <p>{t("settings.modelConfig.gatewayNotConfiguredImpact")}</p>
        </div>
      ) : null}

      {/* [aipdd fork delta] CE 只有本地渠道一种形态（GATEWAY_MODES=["custom"]），
          单 Tab 的模式切换栏只会露出「自定义」这个网关词，故仅在多模式时才渲染；
          现阶段隐藏整条栏（连带「启用该模式」按钮），主体恒按 custom 渲染。 */}
      {GATEWAY_MODES.length > 1 && (
      <Tabs
        className="mt-4"
        value={mode}
        onValueChange={(value) => {
          const nextMode = value as GatewayMode;
          modeChosenByUser.current = true;
          setMode(nextMode);
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList>
            {GATEWAY_MODES.map((m) => (
              <TabsTrigger key={m} value={m}>
                {t(`settings.modelConfig.modes.${m}`)}
              </TabsTrigger>
            ))}
          </TabsList>
          {mode !== serverMode ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleActivateMode}
              disabled={!selectedModeConfigured || activatingMode}
              title={
                selectedModeConfigured
                  ? t("settings.modelConfig.activateMode")
                  : t("settings.modelConfig.configureBeforeActivate")
              }
            >
              {activatingMode ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              {t("settings.modelConfig.activateMode")}
            </Button>
          ) : null}
        </div>
      </Tabs>
      )}

      {/* 功能模型映射仅在自定义渠道展示；官方渠道不需要。 */}
      {mode === "custom" || mode === "hybrid" ? (
        <>
          {mode === "custom" ? (
            <ProviderConnectCards
              config={config}
              loading={loading}
              newApiBaseUrl={customBaseUrl}
            />
          ) : null}
          {/* [aipdd fork delta] 默认生成模型面板：网关配置完成后拉取上游模型清单。
              放在渠道连接卡之后、「高级」折叠之前——每个分类都必须设好默认模型，
              否则生成时报「没有可用渠道 / 模型未找到」，故前置到重点位置强提示用户。 */}
          {mode === "custom" ? (
            <DefaultGenerationModelsPanel
              config={config}
              newApiBaseUrl={customBaseUrl}
            />
          ) : null}
          {mode === "hybrid" ? (
            <details className="mt-5 rounded-md border border-border/70">
              <summary className="cursor-pointer px-3 py-3 text-xs font-medium text-foreground">
                {t("settings.modelConfig.quick.comfyConfig")}
              </summary>
              <div className="border-t border-border/70 px-3 pb-4">
                <FeatureModelsBlock
                  newApiBaseUrl={customBaseUrl}
                  database={customDatabase}
                  channelTypesEnabled={Boolean(
                    config?.custom?.configured &&
                    config?.provisioner?.database?.available,
                  )}
                  savedProviderChannels={
                    config?.provisioner?.providerChannels ?? []
                  }
                  savedEmbeddingModel={config?.provisioner?.embeddingModel}
                  savedMediaModels={
                    config
                      ? (config.provisioner?.mediaModels ?? {})
                      : undefined
                  }
                  backendSnapshotLoaded={Boolean(config?.provisioner)}
                  defaultComfyWorkflows={HYBRID_COMFYUI_WORKFLOWS}
                  mediaOnly
                  comfyOnly
                />
              </div>
            </details>
          ) : null}
          {mode === "custom" ? (
            <details className="mt-5 rounded-md border border-border/70">
              <summary className="cursor-pointer px-3 py-3 text-xs font-medium text-foreground">
                {t("settings.modelConfig.quick.advanced")}
              </summary>
              <div className="border-t border-border/70 px-3 pb-4">
                <FeatureModelsBlock
                  newApiBaseUrl={customBaseUrl}
                  database={customDatabase}
                  channelTypesEnabled={Boolean(
                    config?.custom?.configured &&
                    config?.provisioner?.database?.available,
                  )}
                  savedProviderChannels={
                    config?.provisioner?.providerChannels ?? []
                  }
                  savedEmbeddingModel={config?.provisioner?.embeddingModel}
                  savedMediaModels={
                    config
                      ? (config.provisioner?.mediaModels ?? {})
                      : undefined
                  }
                  backendSnapshotLoaded={Boolean(config?.provisioner)}
                  defaultComfyWorkflows={HYBRID_COMFYUI_WORKFLOWS}
                />
              </div>
            </details>
          ) : null}
          {/* [aipdd fork delta req40] 渠道模型目录：只把渠道 /v1/models 拉到的事实
              摊出来供查看、无需操作，因此放在本页最后一个板块。 */}
          <ProviderCatalogBlockSection enabled={open} />
        </>
      ) : null}
    </section>
  );
}

export {
  ModelConfigSection,
};
