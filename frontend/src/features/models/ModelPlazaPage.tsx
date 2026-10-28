// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] WP-E: 模型广场。只读目录 + 探活 + 调用示例，按六个 connector
// 分区收录本项目里所有「可配置、能被调用」的模型（含 comfyui 本地工作流、火山
// MediaKit、语音合成位）。页面不含任何计费 / 价格 / 充值元素，也不做模型增删改
// —— 渠道与别名映射仍由「设置 → 模型与渠道」负责。
import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Boxes,
  Check,
  ChevronDown,
  Copy,
  Search,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
// [aipdd fork delta] 渠道展示名（与设置面板 / 画布标注同一张表）：fork override →
// LocalAPI 渠道类型名 → 自有品牌名（aipdd→AIPDD、openai→OpenAI、mediakit 按界面
// 语言取词：zh 火山 AI MediaKit / en・vi Volcengine AI MediaKit）。
import { localizedBrandName, providerChannelLabel } from "@/lib/provider-labels";
import {
  useModelPlaza,
  useTestPlazaModel,
  type PlazaCapability,
  type PlazaConnector,
  type PlazaModel,
} from "@/lib/queries/model-plaza";

const CONNECTOR_ORDER: PlazaConnector[] = [
  "gateway",
  "comfyui",
  "mediakit",
  "model-slot",
];

const STATUS_TONE: Record<string, string> = {
  mapped: "border-primary/30 bg-primary/10 text-primary",
  unmapped: "border-border bg-white/[0.02] text-muted-foreground",
  noChannel: "border-border bg-white/[0.02] text-muted-foreground",
  stale: "border-amber-500/30 bg-amber-500/10 text-amber-400",
  capabilityMismatch: "border-amber-500/30 bg-amber-500/10 text-amber-400",
};

/**
 * 渠道展示名。
 *
 * [aipdd fork delta] 后端下发的 channelName 里带着写死的品牌中文（model_plaza.py
 * 的 _FORK_PROVIDER_LABELS / MediaKit 目录条目）：命中本地化品牌时按界面语言
 * 覆盖，其余渠道（用户自起的渠道名 / ComfyUI 等固定展示名）原样展示。
 */
function plazaChannelLabel(model: PlazaModel): string {
  return (
    localizedBrandName(model.provider) ||
    model.channelName ||
    providerChannelLabel(model.provider)
  );
}

/** 复制按钮：与「MCP」页同一套语汇，成功/失败都回 toast。 */
function CopyChip({
  value,
  label,
  className,
}: {
  value: string;
  label: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success(t("models.plaza.copied"));
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error(t("models.plaza.copyFailed"));
    }
  }, [value, t]);

  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      onClick={onCopy}
      className={cn(
        "h-6 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {copied ? <Check className="size-3 text-primary" /> : <Copy className="size-3" />}
      {label}
    </Button>
  );
}

function CodeBlock({ code, label }: { code: string; label: string }) {
  const { t } = useTranslation();
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-black/20">
      <div className="flex items-center justify-between gap-2 border-b border-border px-2.5 py-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <CopyChip value={code} label={t("models.plaza.copy")} />
      </div>
      <pre className="overflow-x-auto px-2.5 py-2 text-xs leading-relaxed text-foreground">
        <code>{code}</code>
      </pre>
    </div>
  );
}

// 调用示例页签。[aipdd fork delta req41] `edit` = 渠道目录声明了 images/edits（图生图 /
// 图片编辑）时后端多给的那条示例；标题跟其它页签同一套 locale（models.plaza.example.*）。
const PLAZA_EXAMPLE_TABS = ["curl", "python", "node", "edit", "poll"] as const;
type PlazaExampleTab = (typeof PLAZA_EXAMPLE_TABS)[number];

function ModelCard({ model }: { model: PlazaModel }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<PlazaExampleTab>("curl");
  const probe = useTestPlazaModel();

  const exampleTabs = useMemo(
    () =>
      PLAZA_EXAMPLE_TABS.filter(
        (key) => typeof model.examples[key] === "string" && model.examples[key],
      ),
    [model.examples],
  );
  const activeTab = exampleTabs.includes(tab) ? tab : exampleTabs[0] ?? "curl";
  const activeCode = String(model.examples[activeTab] ?? "");

  const onTest = useCallback(() => {
    probe.mutate(model.id, {
      onSuccess: (result) => {
        if (result.skipped) {
          toast.info(
            t("models.plaza.test.skipped", {
              reason: t(`models.plaza.test.reason.${result.reason ?? "unknown"}`),
            }),
          );
          return;
        }
        if (result.ok) {
          toast.success(t("models.plaza.test.ok", { ms: result.latencyMs }));
        } else {
          toast.error(
            t("models.plaza.test.failed", {
              status: result.httpStatus || "network",
              error: result.error || t("models.plaza.test.unknownError"),
            }),
          );
        }
      },
    });
  }, [model.id, probe, t]);

  return (
    <article
      className={cn(
        "flex flex-col rounded-[10px] border border-border bg-white/[0.02] p-3",
        model.status !== "mapped" && "opacity-90",
      )}
    >
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-medium text-foreground" title={model.displayName}>
          {model.displayName}
        </h3>
        <span
          className={cn(
            "shrink-0 rounded-full border px-2 py-0.5 text-xs",
            STATUS_TONE[model.status] ?? STATUS_TONE.unmapped,
          )}
        >
          {t(`models.plaza.status.${model.status}`)}
        </span>
      </div>

      <div className="mb-2 flex flex-wrap gap-1">
        {model.capabilities.map((capability) => (
          <span
            key={capability}
            className="rounded bg-white/[0.05] px-1.5 py-0.5 text-xs text-muted-foreground"
          >
            {t(`models.plaza.capability.${capability}`)}
          </span>
        ))}
        <span className="rounded bg-white/[0.05] px-1.5 py-0.5 text-xs text-muted-foreground">
          {t(`models.plaza.invoke.${model.invokeKind}`)}
        </span>
        {model.probeUnusable ? (
          <span
            className="rounded border border-destructive/30 bg-destructive/10 px-1.5 py-0.5 text-xs text-destructive"
            title={t("models.plaza.probe.title")}
          >
            {t(`models.plaza.probe.${model.probeState}`)}
          </span>
        ) : null}
      </div>

      <dl className="mb-2 space-y-1 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.callName")}</dt>
          <dd className="flex min-w-0 items-center gap-1">
            <code className="truncate rounded bg-white/[0.05] px-1.5 py-0.5 text-foreground" title={model.modelName}>
              {model.modelName}
            </code>
            <CopyChip value={model.modelName} label={t("models.plaza.copy")} />
          </dd>
        </div>
        {model.upstreamModel && model.upstreamModel !== model.modelName ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.upstream")}</dt>
            <dd className="truncate font-mono text-foreground" title={model.upstreamModel}>
              {model.upstreamModel}
            </dd>
          </div>
        ) : null}
        {model.provider ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.channel")}</dt>
            <dd
              className="truncate text-foreground"
              title={plazaChannelLabel(model)}
            >
              {plazaChannelLabel(model)}
            </dd>
          </div>
        ) : null}
        {model.fallbackProvider ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.fallback")}</dt>
            <dd className="truncate text-foreground" title={providerChannelLabel(model.fallbackProvider)}>
              {providerChannelLabel(model.fallbackProvider)}
            </dd>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.inUseBy")}</dt>
          <dd className="truncate text-foreground" title={model.inUseBy.join(", ")}>
            {model.inUseBy.length
              ? t("models.plaza.inUseByCount", { n: model.inUseBy.length })
              : t("models.plaza.notUsed")}
          </dd>
        </div>
        {/* [aipdd fork delta] model-slot 卡片：标题是模型名（调用对象），
            这里补一行「功能位」：给用户看中文功能名，内部键留在 title 里供排错。 */}
        {model.slot ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.feature")}</dt>
            <dd
              className="truncate text-muted-foreground"
              title={model.slotLabel ? `${model.slotLabel} \u00b7 ${model.slot}` : model.slot}
            >
              {model.slotLabel || model.slot}
            </dd>
          </div>
        ) : null}
        {model.note ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("models.plaza.field.note")}</dt>
            <dd className="truncate text-muted-foreground">
              {t(`models.plaza.note.${model.note}`)}
            </dd>
          </div>
        ) : null}
      </dl>

      <div className="mt-auto flex items-center gap-1.5">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setOpen((value) => !value)}
          className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
          aria-expanded={open}
        >
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          {t("models.plaza.showExample")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onTest}
          disabled={!model.testable || probe.isPending}
          title={
            model.testable
              ? t("models.plaza.test.hint")
              : t("models.plaza.test.notProbeable")
          }
          className={cn(
            "h-7 gap-1 px-2 text-xs",
            model.testable
              ? "text-primary hover:text-primary"
              : "text-muted-foreground hover:text-muted-foreground",
          )}
        >
          <Zap className="size-3.5" />
          {probe.isPending && probe.variables === model.id
            ? t("models.plaza.test.running")
            : t("models.plaza.test.button")}
        </Button>
      </div>

      {open ? (
        <div className="mt-2 space-y-2">
          {exampleTabs.length ? (
            <>
              <div className="flex gap-1">
                {exampleTabs.map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    className={cn(
                      "rounded px-2 py-0.5 text-xs transition-colors",
                      key === activeTab
                        ? "bg-primary/15 text-primary"
                        : "bg-white/[0.05] text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {t(`models.plaza.example.${key}`)}
                  </button>
                ))}
              </div>
              <CodeBlock code={activeCode} label={t(`models.plaza.example.${activeTab}`)} />
            </>
          ) : (
            <p className="rounded-lg border border-border bg-white/[0.02] px-2.5 py-2 text-xs text-muted-foreground">
              {t("models.plaza.noExample")}
            </p>
          )}
          {model.examples.note ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t(`models.plaza.exampleNote.${model.examples.note}`)}
            </p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function ModelPlazaPage() {
  const { t } = useTranslation();
  const [connector, setConnector] = useState<PlazaConnector | "">("");
  const [capability, setCapability] = useState<PlazaCapability | "">("");
  const [provider, setProvider] = useState("");
  const [keyword, setKeyword] = useState("");
  const { data, isPending, isError, error } = useModelPlaza(connector || undefined);

  const plaza = data?.data;
  const models = plaza?.models ?? [];

  const providerOptions = useMemo(() => {
    const set = new Set<string>();
    for (const item of models) {
      if (item.provider) set.add(item.provider);
    }
    return Array.from(set).sort();
  }, [models]);

  const visible = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return models.filter((item) => {
      if (capability && !item.capabilities.includes(capability)) return false;
      if (provider && item.provider !== provider) return false;
      if (!needle) return true;
      return [
        item.displayName,
        item.modelName,
        item.upstreamModel,
        item.provider,
        // 渠道名搜索也用界面语言的品牌写法（en/vi 搜 “mediakit” 能命中）。
        localizedBrandName(item.provider) || item.channelName,
        item.inUseBy.join(" "),
        // [aipdd fork delta] 槽位键是内部标识：按 SCREENPLAY_NORMALIZER_MODEL 这类
        // 功能位名搜索时应能命中对应模型卡片；中文功能名同样可搜。
        item.slot ?? "",
        item.slotLabel ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [models, capability, provider, keyword]);

  const healthIssues = useMemo(() => {
    const health = plaza?.aliasHealth ?? {};
    return [
      { count: (health.unmapped ?? []).length, key: "unmapped" },
      { count: (health.staleTargets ?? []).length, key: "staleTargets" },
      { count: (health.capabilityMismatch ?? []).length, key: "capabilityMismatch" },
      { count: (health.noChannel ?? []).length, key: "noChannel" },
    ].filter((item) => item.count > 0);
  }, [plaza?.aliasHealth]);

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] min-h-0 flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 flex-col gap-3 border-b border-border/30 bg-background px-9 py-5">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Boxes className="size-[18px]" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate py-1 text-2xl font-semibold tracking-tight text-foreground">
              {t("models.plaza.title")}
            </h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              {t("models.plaza.description")}
            </p>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-9 py-6">
        <div className="mx-auto max-w-6xl space-y-4">

      <section className="rounded-[10px] border border-border bg-white/[0.02] p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <span>{t("models.plaza.gateway.mode")}:</span>
            <span className="text-foreground">{plaza?.gateway.mode ?? "-"}</span>
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <span>{t("models.plaza.gateway.publicBaseUrl")}:</span>
            <code className="truncate rounded bg-white/[0.05] px-1.5 py-0.5 text-foreground">
              {plaza?.gateway.publicBaseUrl || "-"}
            </code>
            {plaza?.gateway.publicBaseUrl ? (
              <CopyChip value={plaza.gateway.publicBaseUrl} label={t("models.plaza.copy")} />
            ) : null}
          </span>
          <span className="flex items-center gap-1">
            <span>{t("models.plaza.gateway.key")}:</span>
            <span className="font-mono text-foreground">
              {plaza?.gateway.apiKeyPreview || t("models.plaza.gateway.notConfigured")}
            </span>
          </span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          {t("models.plaza.sourceNote")}
        </p>
        {/* [aipdd fork delta] 对外调用统一用「设置 → API 密钥」生成的 st- 密钥（与 MCP 同一把），
            上面的网关密钥由服务端持有、仅供内部转发，不用于外部调用。 */}
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t("models.plaza.callKeyNote")}
        </p>
        {healthIssues.length ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
            <AlertTriangle className="size-3.5 shrink-0" />
            {healthIssues.map((item) => (
              <span key={item.key}>
                {t(`models.plaza.health.${item.key}`, { n: item.count })}
              </span>
            ))}
          </div>
        ) : null}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-full border border-border bg-background/40 p-1">
          <button
            type="button"
            onClick={() => setConnector("")}
            className={cn(
              "rounded-full px-3 py-1 text-xs transition-colors",
              connector === ""
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t("models.plaza.connector.all")}
          </button>
          {CONNECTOR_ORDER.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setConnector(id)}
              className={cn(
                "rounded-full px-3 py-1 text-xs transition-colors",
                connector === id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`models.plaza.connector.${id}`)}
            </button>
          ))}
        </div>

        <div className="relative">
          <select
            value={capability}
            onChange={(event) => setCapability(event.target.value as PlazaCapability | "")}
            className="h-8 appearance-none rounded-lg border border-border bg-background/40 pl-2 pr-7 text-xs text-foreground outline-none focus:border-primary"
            aria-label={t("models.plaza.filter.capability")}
          >
            <option value="">{t("models.plaza.filter.allCapabilities")}</option>
            {(plaza?.capabilities ?? []).map((item) => (
              <option key={item} value={item}>
                {t(`models.plaza.capability.${item}`)}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        </div>

        <div className="relative">
          <select
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            className="h-8 appearance-none rounded-lg border border-border bg-background/40 pl-2 pr-7 text-xs text-foreground outline-none focus:border-primary"
            aria-label={t("models.plaza.filter.channel")}
          >
            <option value="">{t("models.plaza.filter.allChannels")}</option>
            {providerOptions.map((item) => (
              <option key={item} value={item}>
                {providerChannelLabel(item)}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        </div>

        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder={t("models.plaza.search.placeholder")}
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>

      {isPending ? (
        <div className="flex h-40 items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      ) : isError ? (
        <div className="rounded-[10px] border border-border bg-white/[0.02] px-6 py-8 text-center">
          <div className="mb-1 text-sm font-medium text-foreground">{t("models.plaza.error.title")}</div>
          <div className="text-xs text-muted-foreground">{String((error as Error)?.message ?? "")}</div>
        </div>
      ) : !visible.length ? (
        <div className="rounded-[10px] border border-border bg-white/[0.02] px-6 py-10 text-center text-sm text-muted-foreground">
          {t("models.plaza.empty")}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((model) => (
            <ModelCard key={model.id} model={model} />
          ))}
        </div>
      )}
        </div>
      </div>
    </div>
  );
}
