import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  ImageIcon,
  Loader2,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";

import type { FreezoneImageModelInfo } from "@/api/ops";
import { StyleCoverModelPicker, resolveStyleCoverModel } from "@/components/model-picker/StyleCoverModelPicker";
import { Button } from "@/components/ui/button";
import { readLastImageModel, writeLastImageModel } from "@/features/canvas/domain/lastImageModel";
import {
  useDeleteStyleGalleryCover,
  useGenerateStyleGalleryCover,
  useStyleGalleryImageModels,
  useStyleGalleryStyles,
  useUploadStyleGalleryCover,
  type StyleGalleryItem,
} from "@/lib/queries/style-gallery";
import { cn } from "@/lib/utils";

const ACCEPT_IMAGE = "image/png,image/jpeg,image/webp,image/gif";

function StyleCoverThumb({ item }: { item: StyleGalleryItem }) {
  if (!item.has_cover || !item.cover_url) {
    return (
      <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg border border-dashed border-border bg-white/[0.02] text-muted-foreground/50">
        <ImageIcon className="size-5" aria-hidden />
      </div>
    );
  }
  return (
    <img
      src={item.cover_url ?? ""}
      alt={item.label}
      loading="lazy"
      className="h-24 w-24 shrink-0 rounded-lg border border-border bg-white/[0.02] object-cover shadow-sm ring-0 ring-white/10 transition group-hover:ring-2"
      onError={(event) => {
        event.currentTarget.style.visibility = "hidden";
      }}
    />
  );
}

function StyleRow({
  item,
  selectedModel,
}: {
  item: StyleGalleryItem;
  /** 当前所选生成模型；目录未就绪 / 为空时为 null，AI 生成按钮禁用。 */
  selectedModel: FreezoneImageModelInfo | null;
}) {
  const { t } = useTranslation();
  const uploadCover = useUploadStyleGalleryCover();
  const deleteCover = useDeleteStyleGalleryCover();
  const generateCover = useGenerateStyleGalleryCover();
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = uploadCover.isPending || generateCover.isPending;

  const handleFile = (file: File | null) => {
    if (!file) return;
    uploadCover.mutate(
      { styleId: item.id, file },
      {
        onSuccess: () => toast.success(t("settings.styleGallery.uploaded", { label: item.label })),
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : t("settings.styleGallery.uploadFailed")),
      },
    );
  };

  const handleGenerate = () => {
    if (!selectedModel) return;
    generateCover.mutate(
      { styleId: item.id, model: selectedModel.id },
      {
        onSuccess: () =>
          toast.success(
            t("settings.styleGallery.generated", {
              label: item.label,
              model: selectedModel.label,
            }),
          ),
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : t("settings.styleGallery.generateFailed"),
          ),
      },
    );
  };

  const handleDelete = () => {
    deleteCover.mutate(item.id, {
      onSuccess: () => toast.success(t("settings.styleGallery.deleted")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.styleGallery.deleteFailed")),
    });
  };

  return (
    <div className="group flex items-center gap-3 rounded-lg border border-border bg-white/[0.02] p-3 transition-colors hover:border-white/10 hover:bg-white/[0.03]">
      <StyleCoverThumb item={item} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-foreground">{item.label}</div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
          {item.id}
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_IMAGE}
        className="hidden"
        onChange={(event) => {
          handleFile(event.target.files?.[0] ?? null);
          event.target.value = "";
        }}
      />
      <Button
        variant="ghost"
        size="sm"
        className="shrink-0 text-violet-300 hover:bg-white/[0.08] hover:text-violet-200"
        title={t("settings.styleGallery.generateHint")}
        disabled={busy || !selectedModel}
        onClick={handleGenerate}
      >
        {generateCover.isPending ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <Sparkles className="size-3.5" aria-hidden />
        )}
        {generateCover.isPending
          ? t("settings.styleGallery.generating")
          : t("settings.styleGallery.generate")}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="shrink-0 text-emerald-300 hover:bg-white/[0.08] hover:text-emerald-200"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {uploadCover.isPending ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <Upload className="size-3.5" aria-hidden />
        )}
        {item.has_cover ? t("settings.styleGallery.replace") : t("settings.styleGallery.upload")}
      </Button>
      {item.has_cover ? (
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0 text-rose-300 hover:bg-rose-400/10 hover:text-rose-200"
          title={t("settings.styleGallery.delete")}
          disabled={deleteCover.isPending}
          onClick={handleDelete}
        >
          {deleteCover.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Trash2 className="size-3.5" aria-hidden />
          )}
        </Button>
      ) : null}
    </div>
  );
}

export function StyleGallerySection() {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useStyleGalleryStyles();
  // [aipdd fork delta] 「AI 生成」的图片模型选择：目录与生成端同源，初始值延续
  // 「上次所选图片模型」（与画布图片节点 / 图墙共享同一个 localStorage 值）。
  const coverModels = useStyleGalleryImageModels();
  const [coverModelId, setCoverModelId] = useState<string | null>(() => readLastImageModel());
  const coverModelList = coverModels.data ?? [];
  const coverModelSelected = resolveStyleCoverModel(coverModelList, coverModelId);
  const handleCoverModelSelect = (modelId: string) => {
    setCoverModelId(modelId);
    writeLastImageModel(modelId);
  };

  const items = useMemo(() => data?.data ?? [], [data]);
  const total = items.length;
  const covered = items.filter((item) => item.has_cover).length;
  const allCovered = total > 0 && covered === total;

  const grouped = useMemo(() => {
    const map = new Map<string, StyleGalleryItem[]>();
    for (const item of items) {
      const list = map.get(item.category) ?? [];
      list.push(item);
      map.set(item.category, list);
    }
    return [...map.entries()];
  }, [items]);

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            allCovered ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.styleGallery.title")}
        </h3>
        {data && total > 0 && !allCovered ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.styleGallery.warningIconLabel")}
          />
        ) : null}
        {/* [aipdd fork delta] 生成前先选图片模型：触发器上的渠道胶囊就是模型出处。 */}
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-xs text-muted-foreground">
            {t("settings.styleGallery.generateModel")}
          </span>
          <StyleCoverModelPicker
            variant="settings"
            className="min-w-0"
            models={coverModelList}
            selectedId={coverModelId}
            onSelect={handleCoverModelSelect}
            isLoading={coverModels.isLoading}
            ariaLabel={t("settings.styleGallery.generateModel")}
          />
        </div>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.styleGallery.description")}
      </p>

      {data ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {t("settings.styleGallery.summary", { total, covered })}
        </p>
      ) : null}

      {error ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{error.message || t("settings.styleGallery.loadFailed")}</span>
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
          {t("settings.styleGallery.loading")}
        </div>
      ) : grouped.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          {t("settings.styleGallery.empty")}
        </p>
      ) : (
        <div className="mt-4 space-y-5">
          {grouped.map(([category, groupItems]) => (
            <div key={category} className="space-y-2">
              <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {t(`freezone.categories.${category}`, category)}
              </div>
              <div className="grid gap-2.5 sm:grid-cols-2">
                {groupItems.map((item) => (
                  <StyleRow key={item.id} item={item} selectedModel={coverModelSelected} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}