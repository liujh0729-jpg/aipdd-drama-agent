// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → API 密钥：一把 key 同时用于外部 MCP
// （DRAMACLAW_AGENT_TOKEN）与模型广场的 OpenAI 兼容调用（Authorization: Bearer）。
// 明文只在生成时展示一次；列表只给前缀与时间戳。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";

import { confirmDialog } from "@/components/confirm-dialog-host";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useApiKeys,
  useCreateApiKey,
  useRevokeApiKey,
  type ApiKeyRecord,
} from "@/lib/queries/api-keys";
import { cn } from "@/lib/utils";

/** 后端 detail 是固定英文；认得出的先按词条翻，认不出的原样保留。 */
const BACKEND_ERROR_KEYS: ReadonlyArray<readonly [RegExp, string]> = [
  [/too many active api keys/i, "settings.apiKeys.errors.tooMany"],
  [/^api key not found$/i, "settings.apiKeys.errors.notFound"],
];

function apiKeyErrorText(error: unknown, fallback: string, t: TFunction): string {
  const record = error as { message?: unknown; error?: unknown } | null;
  const raw =
    typeof record?.message === "string"
      ? record.message
      : typeof record?.error === "string"
        ? record.error
        : "";
  const detail = raw.trim();
  if (!detail) return fallback;
  for (const [pattern, key] of BACKEND_ERROR_KEYS) {
    if (pattern.test(detail)) {
      return t(key, { defaultValue: detail });
    }
  }
  return detail;
}

function formatTimestamp(value: string, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 生成成功后的“只显示一次”卡片：明文 + 一键复制。 */
function CreatedKeyCard({ value, onDismiss }: { value: string; onDismiss: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t("settings.apiKeys.copyFailed"));
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-emerald-400/30 bg-emerald-400/10 px-3.5 py-3">
      <div className="text-xs font-medium text-emerald-200">
        {t("settings.apiKeys.createdOnce")}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-black/40 px-2.5 py-2 font-mono text-xs break-all text-emerald-100">
          {value}
        </code>
        <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
          {copied ? (
            <Check className="size-3.5" aria-hidden />
          ) : (
            <Copy className="size-3.5" aria-hidden />
          )}
          {copied ? t("settings.apiKeys.copied") : t("settings.apiKeys.copy")}
        </Button>
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-[11px] leading-relaxed text-emerald-200/80">
          {t("settings.apiKeys.createdOnceHint")}
        </p>
        <button
          type="button"
          className="shrink-0 text-[11px] text-emerald-200/80 underline"
          onClick={onDismiss}
        >
          {t("settings.apiKeys.dismiss")}
        </button>
      </div>
    </div>
  );
}

function ApiKeyRow({ record }: { record: ApiKeyRecord }) {
  const { t, i18n } = useTranslation();
  const revokeMutation = useRevokeApiKey();

  const handleRevoke = async () => {
    const confirmed = await confirmDialog({
      title: t("settings.apiKeys.revoke"),
      description: t("settings.apiKeys.revokeConfirm", {
        name: record.name,
        prefix: record.key_prefix,
      }),
      confirmText: t("settings.apiKeys.revoke"),
      confirmVariant: "destructive",
    });
    if (!confirmed) return;
    try {
      const res = await revokeMutation.mutateAsync(record.id);
      if (res.ok === false) {
        toast.error(apiKeyErrorText(res, t("settings.apiKeys.revokeFailed"), t));
        return;
      }
      toast.success(t("settings.apiKeys.revokedDone", { name: record.name }));
    } catch (error) {
      toast.error(apiKeyErrorText(error, t("settings.apiKeys.revokeFailed"), t));
    }
  };

  return (
    <div
      data-api-key-row={record.id}
      className="rounded-lg border border-border bg-white/[0.02] px-3.5 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">
              {record.name}
            </span>
            {record.revoked ? (
              <Badge variant="destructive" className="shrink-0 text-[10px]">
                {t("settings.apiKeys.statuses.revoked")}
              </Badge>
            ) : (
              <Badge variant="outline" className="shrink-0 text-[10px]">
                {t("settings.apiKeys.statuses.active")}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            {record.key_prefix}…
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t("settings.apiKeys.createdAt", {
              date: formatTimestamp(record.created_at, i18n.language),
            })}
            {" · "}
            {record.last_used_at
              ? t("settings.apiKeys.lastUsedAt", {
                  date: formatTimestamp(record.last_used_at, i18n.language),
                })
              : t("settings.apiKeys.neverUsed")}
          </p>
        </div>

        {record.revoked ? null : (
          <Button
            variant="outline"
            size="sm"
            disabled={revokeMutation.isPending}
            onClick={() => void handleRevoke()}
          >
            {revokeMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Trash2 className="size-3.5" aria-hidden />
            )}
            {t("settings.apiKeys.revoke")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function ApiKeysSection() {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useApiKeys();
  const createMutation = useCreateApiKey();
  const [name, setName] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  const handleCreate = async () => {
    try {
      const res = await createMutation.mutateAsync({ name: name.trim() });
      if (res.ok === false) {
        toast.error(apiKeyErrorText(res, t("settings.apiKeys.createFailed"), t));
        return;
      }
      setName("");
      setCreatedKey(res.data.key);
      toast.success(t("settings.apiKeys.generated", { name: res.data.name }));
    } catch (err) {
      toast.error(apiKeyErrorText(err, t("settings.apiKeys.createFailed"), t));
    }
  };

  const records = data?.data ?? [];
  const activeCount = records.filter((record) => !record.revoked).length;
  const hasActiveKey = activeCount > 0;

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            hasActiveKey ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.apiKeys.title")}
        </h3>
        {data && !hasActiveKey ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.apiKeys.warningIconLabel")}
          />
        ) : null}
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.apiKeys.description")}
      </p>

      {data ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {t("settings.apiKeys.summary", {
            total: records.length,
            active: activeCount,
          })}
        </p>
      ) : null}

      <div className="mt-3 rounded-md border border-border bg-white/[0.02] px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
          <KeyRound className="size-3.5 text-muted-foreground" aria-hidden />
          {t("settings.apiKeys.usageTitle")}
        </div>
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[11px] leading-relaxed text-muted-foreground">
          <li>{t("settings.apiKeys.usageMcp")}</li>
          <li>{t("settings.apiKeys.usagePlaza")}</li>
        </ul>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("settings.apiKeys.namePlaceholder")}
          maxLength={64}
          className="h-8 w-56 text-sm"
        />
        <Button
          type="button"
          size="sm"
          disabled={createMutation.isPending}
          onClick={() => void handleCreate()}
        >
          {createMutation.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Plus className="size-3.5" aria-hidden />
          )}
          {t("settings.apiKeys.generate")}
        </Button>
      </div>

      {createdKey ? (
        <CreatedKeyCard value={createdKey} onDismiss={() => setCreatedKey(null)} />
      ) : null}

      {error ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{error.message || t("settings.apiKeys.loadFailed")}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : isPending ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.apiKeys.loading")}
        </div>
      ) : records.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          {t("settings.apiKeys.empty")}
        </p>
      ) : (
        <div className="mt-4 space-y-2.5">
          {records.map((record) => (
            <ApiKeyRow key={record.id} record={record} />
          ))}
        </div>
      )}
    </section>
  );
}