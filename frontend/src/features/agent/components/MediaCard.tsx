// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Agent 回合产出的媒体卡片：预览 + 右键/按钮「存入资产库」。
// 存入动作复用主线的资产库文件夹体系（系统类目 + 用户自建文件夹），见 SaveToLibraryDialog。
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, ExternalLink, FolderPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AgentMediaItem } from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { SaveToLibraryDialog } from "./SaveToLibraryDialog";

/** 自绘右键菜单项：避免为一处交互引入新的 ui 依赖。 */
function MenuItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-white/[0.06]"
    >
      {icon}
      {label}
    </button>
  );
}

export function MediaCard({ item, project }: { item: AgentMediaItem; project: string }) {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);

  // 菜单外点击 / Esc / 滚动即关闭（与画布右键菜单同一交互习惯）。
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const kind = item.kind === "video" || item.kind === "audio" ? item.kind : "image";
  const clamped = menu
    ? {
        x: Math.max(4, Math.min(menu.x, window.innerWidth - 190)),
        y: Math.max(4, Math.min(menu.y, window.innerHeight - 130)),
      }
    : null;

  return (
    <div
      className="group/media relative max-w-md"
      onContextMenu={(event) => {
        event.preventDefault();
        setMenu({ x: event.clientX, y: event.clientY });
      }}
    >
      <div className="overflow-hidden rounded-[10px] border border-white/[0.06] bg-black/20">
        {kind === "image" ? (
          <img src={item.url} alt={item.name} className="max-h-72 w-auto object-contain" />
        ) : kind === "video" ? (
          <video src={item.url} controls className="max-h-72 w-full" />
        ) : (
          <div className="flex items-center gap-3 px-3 py-2.5">
            <span className="text-xs text-muted-foreground">{t("agent.media.audio")}</span>
            <audio src={item.url} controls className="h-8 flex-1" />
          </div>
        )}
      </div>
      <div
        className={cn(
          "pointer-events-none absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition-opacity",
          "group-hover/media:pointer-events-auto group-hover/media:opacity-100",
        )}
      >
        <Button
          type="button"
          size="icon-sm"
          variant="secondary"
          title={t("agent.media.saveToLibrary")}
          onClick={() => setSaveOpen(true)}
          className="border border-white/10 bg-black/40 text-foreground backdrop-blur hover:bg-black/55"
        >
          <FolderPlus className="size-3.5" />
        </Button>
      </div>

      {clamped ? (
        <div
          className="fixed z-50 min-w-44 rounded-md border border-white/10 bg-popover p-1 shadow-xl shadow-black/20"
          style={{ left: clamped.x, top: clamped.y }}
        >
          <MenuItem
            icon={<FolderPlus className="size-3.5" />}
            label={t("agent.media.saveToLibrary")}
            onClick={() => {
              setMenu(null);
              setSaveOpen(true);
            }}
          />
          <MenuItem
            icon={<Download className="size-3.5" />}
            label={t("agent.media.download")}
            onClick={() => {
              setMenu(null);
              const anchor = document.createElement("a");
              anchor.href = item.url;
              anchor.download = item.name;
              anchor.click();
            }}
          />
          <MenuItem
            icon={<ExternalLink className="size-3.5" />}
            label={t("agent.media.open")}
            onClick={() => {
              setMenu(null);
              window.open(item.url, "_blank", "noopener,noreferrer");
            }}
          />
        </div>
      ) : null}

      <SaveToLibraryDialog
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        item={item}
        project={project}
      />
    </div>
  );
}