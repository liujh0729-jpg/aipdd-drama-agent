// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 提示词库：内置提示词可查看、修改、恢复默认。
// 覆盖值由后端 prompt_library 存入 runtime_settings，生成链路即时生效。
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, RotateCcw, Save, Search } from "lucide-react";

import { confirmDialog } from "@/components/confirm-dialog-host";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  PROMPT_OVERRIDE_MAX_CHARS,
  usePromptLibrary,
  useResetPromptOverride,
  useSavePromptOverride,
  type PromptLibraryItem,
} from "@/lib/queries/prompt-library";

/** 后端错误体（detail/error/message）里的可读文案优先，兜底用传入文案。 */
async function promptLibraryErrorMessage(
  error: unknown,
  fallback: string,
): Promise<string> {
  const response = (error as { response?: Response } | null)?.response;
  if (response) {
    const body = await response
      .clone()
      .json()
      .catch(() => null);
    if (body && typeof body === "object") {
      const data = body as {
        detail?: unknown;
        error?: unknown;
        message?: unknown;
      };
      for (const value of [data.detail, data.error, data.message]) {
        if (typeof value === "string" && value.trim()) return value.trim();
      }
    }
  }
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === "string" && message.trim()
    ? message.trim()
    : fallback;
}

function PromptItemEditor({ item }: { item: PromptLibraryItem }) {
  const { t } = useTranslation();
  const saveMutation = useSavePromptOverride();
  const resetMutation = useResetPromptOverride();
  const [draft, setDraft] = useState(item.value);
  const [inlineError, setInlineError] = useState<string | null>(null);

  // 保存 / 恢复后列表会重新拉取，用服务端值对齐编辑框，避免停在旧内容上。
  useEffect(() => {
    setDraft(item.value);
    setInlineError(null);
  }, [item.value]);

  // 词条缺失时回落到后端内置的中文名 / 说明，面板不会出现空标题。
  const label = t(`settings.promptLibrary.items.${item.key}.label`, {
    defaultValue: item.label,
  });
  const description = t(`settings.promptLibrary.items.${item.key}.description`, {
    defaultValue: item.description,
  });
  const dirty = draft !== item.value;
  const tooLong = draft.length > PROMPT_OVERRIDE_MAX_CHARS;
  const busy = saveMutation.isPending || resetMutation.isPending;

  const handleSave = async () => {
    if (!draft.trim()) {
      setInlineError(t("settings.promptLibrary.validation.empty"));
      return;
    }
    if (tooLong) {
      setInlineError(
        t("settings.promptLibrary.validation.tooLong", {
          max: PROMPT_OVERRIDE_MAX_CHARS,
        }),
      );
      return;
    }
    setInlineError(null);
    try {
      const res = await saveMutation.mutateAsync({
        key: item.key,
        value: draft,
      });
      if (!res.ok) {
        toast.error(
          t("settings.promptLibrary.saveFailed", { error: res.error }),
        );
        return;
      }
      toast.success(t("settings.promptLibrary.saveSuccess", { label }));
    } catch (error) {
      toast.error(
        t("settings.promptLibrary.saveFailed", {
          error: await promptLibraryErrorMessage(
            error,
            t("settings.promptLibrary.requestFailed"),
          ),
        }),
      );
    }
  };

  const handleReset = async () => {
    const confirmed = await confirmDialog({
      title: t("settings.promptLibrary.resetTitle"),
      description: t("settings.promptLibrary.resetConfirm", { label }),
      confirmText: t("settings.promptLibrary.reset"),
      confirmVariant: "destructive",
    });
    if (!confirmed) return;
    try {
      const res = await resetMutation.mutateAsync(item.key);
      if (!res.ok) {
        toast.error(
          t("settings.promptLibrary.resetFailed", { error: res.error }),
        );
        return;
      }
      toast.success(t("settings.promptLibrary.resetSuccess", { label }));
    } catch (error) {
      toast.error(
        t("settings.promptLibrary.resetFailed", {
          error: await promptLibraryErrorMessage(
            error,
            t("settings.promptLibrary.requestFailed"),
          ),
        }),
      );
    }
  };

  return (
    <div
      data-prompt-key={item.key}
      className="rounded-lg border border-border bg-white/[0.02] p-3.5"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground">{label}</span>
            {item.is_overridden && (
              <Badge variant="secondary" className="h-4 text-[10px]">
                {t("settings.promptLibrary.overriddenBadge")}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
          <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>{t("settings.promptLibrary.variablesLabel")}</span>
            {item.variables.length === 0 ? (
              <span>{t("settings.promptLibrary.noVariables")}</span>
            ) : (
              item.variables.map((name) => (
                <code
                  key={name}
                  className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[10px] text-foreground/80"
                >
                  {`{${name}}`}
                </code>
              ))
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {item.is_overridden && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void handleReset()}
            >
              {resetMutation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : (
                <RotateCcw className="size-3.5" aria-hidden />
              )}
              {t("settings.promptLibrary.reset")}
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            disabled={!dirty || busy || tooLong}
            onClick={() => void handleSave()}
          >
            {saveMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Save className="size-3.5" aria-hidden />
            )}
            {t("settings.promptLibrary.save")}
          </Button>
        </div>
      </div>
      <Textarea
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          setInlineError(null);
        }}
        spellCheck={false}
        aria-label={label}
        className="mt-2.5 max-h-[260px] overflow-auto font-mono text-[11px] leading-relaxed"
      />
      <div className="mt-1 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
        <span className="text-destructive">{inlineError ?? ""}</span>
        <span className={tooLong ? "text-destructive" : undefined}>
          {t("settings.promptLibrary.charCount", { total: draft.length })}
        </span>
      </div>
    </div>
  );
}

export function PromptLibrarySection() {
  const { t } = useTranslation();
  const listQuery = usePromptLibrary(true);
  const [search, setSearch] = useState("");

  const payload = listQuery.data?.data;
  const items = useMemo(() => payload?.items ?? [], [payload]);

  // 搜索覆盖标题、说明、key 与正文，方便按内容定位某条提示词。
  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return items;
    return items.filter((item) => {
      const label = t(`settings.promptLibrary.items.${item.key}.label`, {
        defaultValue: item.label,
      });
      const description = t(
        `settings.promptLibrary.items.${item.key}.description`,
        { defaultValue: item.description },
      );
      return [label, description, item.key, item.value].some((value) =>
        value.toLowerCase().includes(keyword),
      );
    });
  }, [items, search, t]);

  const grouped = useMemo(() => {
    const order = payload?.groups ?? [];
    const buckets = new Map<string, PromptLibraryItem[]>();
    for (const item of filtered) {
      const bucket = buckets.get(item.group);
      if (bucket) bucket.push(item);
      else buckets.set(item.group, [item]);
    }
    const rank = (group: string) => {
      const index = order.indexOf(group);
      return index === -1 ? Number.MAX_SAFE_INTEGER : index;
    };
    return [...buckets.entries()].sort(
      ([left], [right]) => rank(left) - rank(right),
    );
  }, [filtered, payload]);

  const overriddenCount = items.filter((item) => item.is_overridden).length;

  return (
    <div className="px-5 py-4">
      <div className="text-sm font-semibold text-foreground">
        {t("settings.promptLibrary.title")}
      </div>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
        {t("settings.promptLibrary.description")}
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {t("settings.promptLibrary.summary", {
          total: items.length,
          modified: overriddenCount,
        })}
      </p>

      <div className="relative mt-3">
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("settings.promptLibrary.searchPlaceholder")}
          className="h-8 pl-8 text-sm"
        />
      </div>

      {listQuery.isLoading ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.promptLibrary.loading")}
        </div>
      ) : listQuery.isError || !payload ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{t("settings.promptLibrary.loadFailed")}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void listQuery.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : (
        <div className="mt-4 space-y-5">
          {grouped.length === 0 && (
            <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              {t("settings.promptLibrary.empty")}
            </p>
          )}
          {grouped.map(([group, groupItems]) => (
            <section key={group} data-prompt-group={group} className="space-y-2.5">
              <div className="flex items-center gap-2">
                <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  {t(`settings.promptLibrary.groups.${group}`, {
                    defaultValue: group,
                  })}
                </h3>
                <span className="text-[11px] text-muted-foreground">
                  {t("settings.promptLibrary.groupCount", {
                    total: groupItems.length,
                  })}
                </span>
              </div>
              <div className="space-y-2.5">
                {groupItems.map((item) => (
                  <PromptItemEditor key={item.key} item={item} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}