// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具页批量输入：本地多文件上传 + 项目素材库多选，两条来源汇入同一批队列。
import { useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2, Upload, X } from "lucide-react";

import {
  fetchFreezoneVideoCharacterLibrary,
  uploadFreezoneImage,
} from "@/api/ops";
import { normalizeLibraryList, type LibraryItem } from "@/features/canvas/ui/assetLibraryItems";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { kindFromFile, type AcceptKind, type BatchItem } from "@/features/tools/core";

function acceptMatches(accept: AcceptKind, kind: BatchItem["kind"]): boolean {
  if (accept === "video-or-image") return kind === "video" || kind === "image";
  return accept === kind;
}

function mediaToKind(media: LibraryItem["media"]): BatchItem["kind"] | null {
  if (media === "video" || media === "image" || media === "audio") return media;
  return null;
}

export interface BatchInputAreaProps {
  project: string;
  accept: AcceptKind;
  value: BatchItem[];
  onChange: (next: BatchItem[]) => void;
}

export function BatchInputArea({ project, accept, value, onChange }: BatchInputAreaProps) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [library, setLibrary] = useState<LibraryItem[] | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState(false);

  const selectedUrls = useMemo(() => new Set(value.map((v) => v.url)), [value]);

  const addUnique = useCallback(
    (items: BatchItem[]) => {
      const existing = new Set(value.map((v) => v.url));
      const merged = [...value];
      for (const item of items) {
        if (!existing.has(item.url)) {
          existing.add(item.url);
          merged.push(item);
        }
      }
      onChange(merged);
    },
    [value, onChange],
  );

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setUploading(true);
      try {
        const picked: BatchItem[] = [];
        for (const file of Array.from(files)) {
          const kind = kindFromFile(file);
          if (!kind || !acceptMatches(accept, kind)) continue;
          const uploaded = await uploadFreezoneImage(project, file, file.name);
          picked.push({ url: uploaded.url, name: file.name, kind });
        }
        if (picked.length) addUnique(picked);
      } finally {
        setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    },
    [accept, addUnique, project],
  );

  const openLibrary = useCallback(async () => {
    const next = !libraryOpen;
    setLibraryOpen(next);
    if (next && library === null && !libraryLoading) {
      setLibraryLoading(true);
      setLibraryError(false);
      try {
        const payload = await fetchFreezoneVideoCharacterLibrary(project);
        setLibrary(normalizeLibraryList(payload));
      } catch {
        setLibraryError(true);
      } finally {
        setLibraryLoading(false);
      }
    }
  }, [libraryOpen, library, libraryLoading, project]);

  const libraryItems = useMemo(() => {
    if (!library) return [];
    return library.filter((item) => {
      const kind = mediaToKind(item.media);
      return kind ? acceptMatches(accept, kind) : false;
    });
  }, [library, accept]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => void handleFiles(e.target.files)}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="border-white/10 bg-white/[0.02] hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
          onClick={() => fileRef.current?.click()}
        >
          {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
          {t("tools.common.upload")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="border-white/10 bg-white/[0.02] hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
          onClick={() => void openLibrary()}
        >
          <Plus className="size-4" />
          {t("tools.common.fromLibrary")}
        </Button>
        {value.length > 0 ? (
          <span className="text-xs leading-relaxed text-muted-foreground">{t("tools.common.selectedCount", { n: value.length })}</span>
        ) : null}
      </div>

      {libraryOpen ? (
        <div className="max-h-56 overflow-y-auto rounded-lg border border-border bg-white/[0.02] p-2">
          {libraryLoading ? (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              {t("tools.common.libraryLoading")}
            </div>
          ) : libraryError ? (
            <div className="px-2 py-3 text-xs leading-relaxed text-red-400">{t("tools.common.libraryError")}</div>
          ) : libraryItems.length === 0 ? (
            <div className="px-2 py-3 text-xs leading-relaxed text-muted-foreground">{t("tools.common.libraryEmpty")}</div>
          ) : (
            <ul className="grid grid-cols-2 gap-1 sm:grid-cols-3">
              {libraryItems.map((item) => {
                const checked = selectedUrls.has(item.url);
                return (
                  <li key={item.id ?? item.url}>
                    <label
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-xs",
                        checked
                          ? "border-primary/60 bg-primary/10 text-foreground"
                          : "border-border text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const kind = mediaToKind(item.media) ?? "image";
                          if (checked) {
                            onChange(value.filter((v) => v.url !== item.url));
                          } else {
                            addUnique([{ url: item.url, name: item.name, kind }]);
                          }
                        }}
                      />
                      <span className="truncate">{item.name}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}

      {value.length > 0 ? (
        <ul className="flex flex-wrap gap-2">
          {value.map((item) => (
            <li
              key={item.url}
              className="flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.05] py-1 pl-1 pr-2 text-xs text-foreground"
            >
              {item.kind === "image" ? (
                <img src={item.url} alt={item.name} className="size-6 rounded-full object-cover" />
              ) : (
                <span className="flex size-6 items-center justify-center rounded-full bg-white/10 text-[9px] uppercase">
                  {item.kind.slice(0, 3)}
                </span>
              )}
              <span className="max-w-[160px] truncate">{item.name}</span>
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v.url !== item.url))}
                className="text-muted-foreground hover:text-foreground"
                aria-label={t("tools.common.remove")}
              >
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-4 text-xs text-muted-foreground">
          <Trash2 className="size-3.5 opacity-0" />
          {t("tools.common.noInput")}
        </div>
      )}
    </div>
  );
}
