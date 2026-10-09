// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req40] 渠道模型目录块。
// 职责：把「渠道自己声明过的模型清单」原样摊给设置页 —— 平台不预先知道渠道里有
// 什么模型，只能拉了才知道；这一页就是那份事实的只读视图 + 重新拉取的入口。

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useProviderCatalog,
  useSyncProviderCatalog,
  type ProviderCatalogBlock,
  type UpstreamCatalogEntry,
} from "@/lib/queries/model-gateway";
import { getResponseErrorMessage } from "./shared";

const CATALOG_KIND_ORDER = [
  "text",
  "vision",
  "image",
  "video",
  "audio",
  // [aipdd fork delta req46] 音乐单独成组：后端目录把 music-3.0 / music-cover 归
  // music（与配音协议不同），混在 audio 组里会让人以为它能直接配音。
  "music",
  "asr",
  "embedding",
  "decision",
  "other",
];

/** 条目的归类：优先渠道自己声明的 ``kind``，其次我们推断出的 ``mediaType``。 */
export function catalogEntryKind(entry: UpstreamCatalogEntry): string {
  const kind = String(entry.kind || "").trim().toLowerCase();
  if (kind) return kind;
  const mediaType = String(entry.mediaType || "").trim().toLowerCase();
  return mediaType && mediaType !== "other" ? mediaType : "other";
}

/** 某个渠道目录里指定类型的候选（下拉的唯一数据源：没有目录就是空表，不兜底名字）。 */
export function catalogCandidates(
  blocks: ProviderCatalogBlock[],
  provider: string,
  kinds: string[],
): UpstreamCatalogEntry[] {
  const wanted = new Set(kinds.map((kind) => kind.trim().toLowerCase()));
  const providerKey = String(provider || "").trim().toLowerCase();
  const names = new Set<string>();
  const out: UpstreamCatalogEntry[] = [];
  for (const block of blocks) {
    if (providerKey && String(block.provider || "").toLowerCase() !== providerKey) {
      continue;
    }
    for (const entry of block.entries || []) {
      if (!wanted.has(catalogEntryKind(entry))) continue;
      const name = String(entry.name || "").trim();
      if (!name || names.has(name)) continue;
      names.add(name);
      out.push(entry);
    }
  }
  return out.sort((left, right) => left.name.localeCompare(right.name));
}

function ProviderCatalogBlockCard({
  block,
  syncing,
  onSync,
}: {
  block: ProviderCatalogBlock;
  syncing: boolean;
  onSync: (provider: string) => void;
}) {
  const { t } = useTranslation();
  const groups = useMemo(() => {
    const byKind = new Map<string, UpstreamCatalogEntry[]>();
    for (const entry of block.entries || []) {
      const kind = catalogEntryKind(entry);
      const list = byKind.get(kind) || [];
      list.push(entry);
      byKind.set(kind, list);
    }
    return CATALOG_KIND_ORDER.filter((kind) => byKind.has(kind)).map((kind) => ({
      kind,
      entries: (byKind.get(kind) || []).slice().sort((left, right) =>
        String(left.name).localeCompare(String(right.name)),
      ),
    }));
  }, [block.entries]);

  return (
    <div className="rounded-md border border-border/70">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 px-3 py-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-foreground">
            {block.provider}
            <span className="ml-2 text-[11px] font-normal text-muted-foreground">
              {t("settings.modelConfig.providerCatalog.entryCount", {
                total: (block.entries || []).length,
              })}
            </span>
          </p>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {block.fetchedAt
              ? t("settings.modelConfig.providerCatalog.fetchedAt", {
                  time: block.fetchedAt,
                })
              : t("settings.modelConfig.providerCatalog.neverFetched")}
            {block.baseUrl ? ` · ${block.baseUrl}` : ""}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={syncing}
          onClick={() => onSync(block.provider)}
        >
          {syncing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          {t("settings.modelConfig.providerCatalog.sync")}
        </Button>
      </div>
      {groups.length === 0 ? (
        <p className="px-3 py-3 text-[11px] text-muted-foreground">
          {t("settings.modelConfig.providerCatalog.empty")}
        </p>
      ) : (
        <div className="px-3 py-2">
          {groups.map((group) => (
            <div key={group.kind} className="mb-2 last:mb-0">
              <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                {t(
                  `settings.modelConfig.providerCatalog.kinds.${group.kind}`,
                  { defaultValue: group.kind },
                )}
                {` · ${group.entries.length}`}
              </p>
              <ul className="mt-1 space-y-1">
                {group.entries.map((entry) => (
                  <li
                    key={`${block.provider}:${entry.name}`}
                    className="flex flex-wrap items-center gap-2 text-[11px] text-foreground"
                  >
                    <span className="font-mono">{entry.name}</span>
                    {entry.inferred ? (
                      <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-400">
                        {t("settings.modelConfig.providerCatalog.sourceInferred")}
                      </span>
                    ) : (
                      <span className="rounded bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-400">
                        {t("settings.modelConfig.providerCatalog.sourceDeclared")}
                      </span>
                    )}
                    {typeof entry.dimensions === "number" ? (
                      <span className="text-muted-foreground">
                        {t("settings.modelConfig.providerCatalog.dimensions", {
                          value: entry.dimensions,
                        })}
                      </span>
                    ) : null}
                    {(entry.endpoints || []).length > 0 ? (
                      <span className="truncate text-muted-foreground">
                        {(entry.endpoints || []).join(" / ")}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ProviderCatalogBlockSection({ enabled = true }: { enabled?: boolean }) {
  const { t } = useTranslation();
  const catalog = useProviderCatalog(enabled);
  const syncCatalog = useSyncProviderCatalog();
  const providers = catalog.data?.data?.providers ?? [];

  const handleSync = (provider: string) => {
    syncCatalog.mutate(
      { provider, force: true },
      {
        onSuccess: (res) => {
          if (res.ok !== true) {
            toast.error(
              getResponseErrorMessage(
                res,
                t("settings.modelConfig.providerCatalog.syncFailed"),
              ),
            );
            return;
          }
          const pruned = res.data?.pruned ?? {};
          const slots = Object.entries(pruned).map(
            ([slot, models]) =>
              `${slot} → ${(models || []).join(", ")}`,
          );
          toast.success(
            slots.length
              ? t("settings.modelConfig.providerCatalog.pruned", {
                  slots: slots.join("；"),
                })
              : t("settings.modelConfig.providerCatalog.noPruned"),
          );
        },
      },
    );
  };

  return (
    <section className="mt-5 rounded-md border border-border/70">
      <div className="border-b border-border/70 px-3 py-2">
        <p className="text-xs font-medium text-foreground">
          {t("settings.modelConfig.providerCatalog.title")}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {t("settings.modelConfig.providerCatalog.hint")}
        </p>
      </div>
      <div className="space-y-3 px-3 py-3">
        {catalog.isLoading ? (
          <p className="text-[11px] text-muted-foreground">
            {t("settings.modelConfig.providerCatalog.loading")}
          </p>
        ) : null}
        {!catalog.isLoading && providers.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            {t("settings.modelConfig.providerCatalog.noProviders")}
          </p>
        ) : null}
        {providers.map((block) => (
          <ProviderCatalogBlockCard
            key={block.provider}
            block={block}
            syncing={
              syncCatalog.isPending &&
              syncCatalog.variables?.provider === block.provider
            }
            onSync={handleSync}
          />
        ))}
      </div>
    </section>
  );
}

export { ProviderCatalogBlockSection };
