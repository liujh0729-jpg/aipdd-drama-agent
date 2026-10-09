// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「存入资产库」对话框：选保存位置（系统类目文件夹 / 用户自建文件夹）
// 与用途类目，把 Agent 产出的媒体登记进项目资产库。主线文件夹（mainline）不可选。
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { fetchFreezoneAssetLibraryFolders } from "@/api/ops";
import { Button } from "@/components/ui/button";
import { SUBTLE_HEADER_ACTION_BUTTON_CLASS } from "@/components/ui/header-action-styles";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ASSET_CATEGORIES,
  type AssetCategory,
  systemFolderLabelKey,
} from "@/features/canvas/ui/assetLibraryItems";
import { queryKeys } from "@/lib/query-keys";
import { useSaveAgentMediaToLibrary, type AgentMediaItem } from "@/lib/queries/agent";

import { agentErrorMessage, mediaDisplayName } from "../core";

/** 原生 select 的统一样式（与工具页同源，避免额外引入 Select 组件依赖）。 */
const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-white/10 bg-white/[0.025] px-2 text-sm text-foreground outline-none focus:border-primary/50";

export function SaveToLibraryDialog({
  open,
  onClose,
  item,
  project,
}: {
  open: boolean;
  onClose: () => void;
  item: AgentMediaItem;
  project: string;
}) {
  const { t } = useTranslation();
  const save = useSaveAgentMediaToLibrary();
  const foldersQuery = useQuery({
    queryKey: queryKeys.freezoneAssetLibraryFolders(project),
    queryFn: () => fetchFreezoneAssetLibraryFolders(project),
    enabled: open,
    staleTime: 30_000,
  });
  const [name, setName] = useState("");
  const [folder, setFolder] = useState<string>("other");
  const [category, setCategory] = useState<AssetCategory>("other");

  // 每次打开都回到「待分类资产 + 其它」的默认态（名称按媒体文件名预填）。
  useEffect(() => {
    if (!open) return;
    setName(mediaDisplayName(item));
    setFolder("other");
    setCategory("other");
  }, [open, item]);

  const folderOptions = useMemo(() => {
    const systemFolders = ASSET_CATEGORIES.map((entry) => ({
      key: entry.key as string,
      label: t(systemFolderLabelKey(entry.key) ?? entry.labelKey),
    }));
    const customFolders = (foldersQuery.data ?? []).map((entry) => ({
      key: entry.id,
      label: entry.name,
    }));
    return [...systemFolders, ...customFolders];
  }, [foldersQuery.data, t]);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await save.mutateAsync({
        project,
        url: item.url,
        name: trimmed,
        media: item.kind,
        folder,
        category,
      });
      toast.success(t("agent.library.saved"), {
        description: t("agent.library.savedHint"),
      });
      onClose();
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("agent.library.title")}</DialogTitle>
          <DialogDescription>{t("agent.library.description")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="agent-library-name">{t("agent.library.name")}</Label>
            <Input
              id="agent-library-name"
              value={name}
              maxLength={60}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="agent-library-folder">{t("agent.library.folder")}</Label>
              <select
                id="agent-library-folder"
                value={folder}
                onChange={(event) => setFolder(event.target.value)}
                className={SELECT_CLASS}
              >
                {folderOptions.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="agent-library-category">{t("agent.library.category")}</Label>
              <select
                id="agent-library-category"
                value={category}
                onChange={(event) => setCategory(event.target.value as AssetCategory)}
                className={SELECT_CLASS}
              >
                {ASSET_CATEGORIES.map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {t(entry.labelKey)}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
        <DialogFooter className="p-0 pt-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClose}
            className={SUBTLE_HEADER_ACTION_BUTTON_CLASS}
          >
            {t("agent.library.cancel")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!name.trim() || save.isPending}
            onClick={() => void submit()}
            className="h-8 gap-1.5 rounded-[8px] bg-primary px-3 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
          >
            {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("agent.library.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}