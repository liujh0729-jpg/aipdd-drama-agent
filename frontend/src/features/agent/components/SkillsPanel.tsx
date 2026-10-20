// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 技能面板：安装（SKILL.md / zip 技能包）、启停、查看、下载、删除。
// 内置技能来自仓库 .hermes/skills（只读，可停用）；用户安装落在用户级 Agent 数据目录。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, Eye, Loader2, Trash2, Upload } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  downloadAgentSkill,
  useAgentSkillDetail,
  useAgentSkills,
  useDeleteAgentSkill,
  useInstallAgentSkill,
  useSetAgentSkillEnabled,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { agentErrorMessage } from "../core";

export function SkillsPanel() {
  const { t } = useTranslation();
  const skills = useAgentSkills();
  const install = useInstallAgentSkill();
  const setEnabled = useSetAgentSkillEnabled();
  const remove = useDeleteAgentSkill();
  const fileRef = useRef<HTMLInputElement>(null);
  const deleteTimerRef = useRef<number | null>(null);
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<Set<string>>(() => new Set());

  useEffect(
    () => () => {
      if (deleteTimerRef.current) window.clearTimeout(deleteTimerRef.current);
    },
    [],
  );

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setError("");
    try {
      const result = await install.mutateAsync(file);
      toast.success(
        result.updated
          ? t("agent.skills.updated", { name: result.skill.name })
          : t("agent.skills.installed", { name: result.skill.name }),
      );
    } catch (err) {
      setError(await agentErrorMessage(err));
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleDownload = async (name: string) => {
    setDownloading((prev) => new Set(prev).add(name));
    try {
      await downloadAgentSkill(name);
    } catch (err) {
      toast.error(await agentErrorMessage(err));
    } finally {
      setDownloading((prev) => {
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    }
  };

  const handleDeleteClick = (name: string) => {
    if (pendingDelete === name) {
      if (deleteTimerRef.current) {
        window.clearTimeout(deleteTimerRef.current);
        deleteTimerRef.current = null;
      }
      setPendingDelete(null);
      remove.mutate(name, {
        onError: async (err) => toast.error(await agentErrorMessage(err)),
      });
      return;
    }
    setPendingDelete(name);
    if (deleteTimerRef.current) window.clearTimeout(deleteTimerRef.current);
    // 定时器只清除自己那一条，避免把后来点开的另一条确认态一起清掉。
    deleteTimerRef.current = window.setTimeout(() => {
      deleteTimerRef.current = null;
      setPendingDelete((current) => (current === name ? null : current));
    }, 3000);
  };

  const list = skills.data?.data ?? [];

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {t("agent.skills.count", { n: list.length })}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={install.isPending}
          onClick={() => fileRef.current?.click()}
          className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-transparent px-2 text-[11px] font-normal shadow-none hover:bg-white/[0.06]"
        >
          {install.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Upload className="size-3.5" />
          )}
          {t("agent.skills.install")}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.zip"
          className="hidden"
          onChange={(event) => void handleFile(event.target.files?.[0])}
        />
      </div>

      {error ? (
        <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {skills.isLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : skills.isError ? (
        <div className="rounded-[10px] border border-red-500/30 bg-red-500/10 px-3 py-2.5">
          <p className="text-xs text-red-300">{t("agent.skills.loadFailed")}</p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void skills.refetch()}
            className="mt-2 h-7 rounded-[7px] border-white/10 bg-transparent px-2 text-[11px] font-normal shadow-none hover:bg-white/[0.06]"
          >
            {t("agent.skills.retry")}
          </Button>
        </div>
      ) : list.length === 0 ? (
        <div className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-3">
          <p className="text-xs text-muted-foreground">{t("agent.skills.empty")}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t("agent.skills.emptyHint")}
          </p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {list.map((skill) => (
            <div
              key={skill.name}
              className="rounded-[10px] border border-white/[0.06] bg-white/[0.018] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="min-w-0 truncate text-xs font-medium text-foreground">
                    {skill.title || skill.name}
                  </span>
                  {skill.source === "builtin" ? (
                    <span className="shrink-0 rounded-[4px] border border-white/10 px-1 py-px text-[10px] leading-4 text-muted-foreground">
                      {t("agent.skills.builtin")}
                    </span>
                  ) : null}
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    title={t("agent.skills.view")}
                    aria-label={t("agent.skills.view")}
                    onClick={() => setViewing(skill.name)}
                    className="rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <Eye className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    title={t("agent.skills.download")}
                    aria-label={t("agent.skills.download")}
                    disabled={downloading.has(skill.name)}
                    onClick={() => void handleDownload(skill.name)}
                    className="rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
                  >
                    {downloading.has(skill.name) ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Download className="size-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={skill.enabled}
                    aria-label={skill.enabled ? t("agent.skills.disable") : t("agent.skills.enable")}
                    title={skill.enabled ? t("agent.skills.disable") : t("agent.skills.enable")}
                    disabled={setEnabled.isPending}
                    onClick={() =>
                      setEnabled.mutate(
                        { name: skill.name, enabled: !skill.enabled },
                        {
                          onError: async (err) => toast.error(await agentErrorMessage(err)),
                        },
                      )
                    }
                    className={cn(
                      "relative h-4 w-7 rounded-full transition-colors",
                      skill.enabled ? "bg-primary" : "bg-white/15",
                    )}
                  >
                    <span
                      className={cn(
                        "absolute top-0.5 size-3 rounded-full bg-white transition-all",
                        skill.enabled ? "left-3.5" : "left-0.5",
                      )}
                    />
                  </button>
                  {skill.source === "builtin" ? null : (
                    <button
                      type="button"
                      title={
                        pendingDelete === skill.name
                          ? t("agent.skills.deleteConfirm")
                          : t("agent.skills.delete")
                      }
                      aria-label={
                        pendingDelete === skill.name
                          ? t("agent.skills.deleteConfirm")
                          : t("agent.skills.delete")
                      }
                      onClick={() => handleDeleteClick(skill.name)}
                      className={cn(
                        "rounded-sm p-1 transition-colors",
                        pendingDelete === skill.name
                          ? "bg-red-500/15 text-red-400"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  )}
                </div>
              </div>
              {skill.description ? (
                <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-muted-foreground">
                  {skill.description}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] leading-relaxed text-muted-foreground">{t("agent.skills.formatHint")}</p>

      <SkillDetailDialog name={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

function SkillDetailDialog({ name, onClose }: { name: string | null; onClose: () => void }) {
  const { t } = useTranslation();
  const detail = useAgentSkillDetail(name);
  const [downloading, setDownloading] = useState(false);
  const skill = detail.data?.data;
  const files = skill?.files ?? [];

  const handleDownload = async () => {
    if (!skill) return;
    setDownloading(true);
    try {
      await downloadAgentSkill(skill.name);
    } catch (err) {
      toast.error(await agentErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Dialog
      open={Boolean(name)}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="min-w-0 truncate">
              {skill?.title || skill?.name || t("agent.skills.detailTitle")}
            </span>
            {skill?.source === "builtin" ? (
              <span className="shrink-0 rounded-[4px] border border-white/10 px-1 py-px text-[10px] font-normal leading-4 text-muted-foreground">
                {t("agent.skills.builtin")}
              </span>
            ) : null}
          </DialogTitle>
        </DialogHeader>
        {detail.isLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          </div>
        ) : detail.isError || !skill ? (
          <p className="py-4 text-xs text-red-300">{t("agent.skills.detailFailed")}</p>
        ) : (
          <div className="max-h-[62vh] overflow-y-auto pr-1">
            {skill.description ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {skill.description}
              </p>
            ) : null}
            {files.length > 0 ? (
              <div className="mt-2 rounded-[8px] border border-white/[0.06] bg-white/[0.018] px-2.5 py-2">
                <p className="text-[11px] text-muted-foreground">
                  {t("agent.skills.filesCount", { n: files.length })}
                </p>
                <p className="mt-1 break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
                  {files.join(" · ")}
                </p>
              </div>
            ) : null}
            <div className="mt-3 break-words text-xs leading-relaxed text-foreground/90">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="mb-2 mt-3 text-base font-semibold leading-6 first:mt-0">
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="mb-2 mt-3 text-sm font-semibold leading-6 first:mt-0">
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="mb-1.5 mt-2.5 text-xs font-semibold leading-5 first:mt-0">
                      {children}
                    </h3>
                  ),
                  p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
                  ul: ({ children }) => (
                    <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-foreground">{children}</strong>
                  ),
                  a: ({ children, href }) => (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary underline underline-offset-2"
                    >
                      {children}
                    </a>
                  ),
                  code: ({ children }) => (
                    <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[0.92em]">
                      {children}
                    </code>
                  ),
                  pre: ({ children }) => (
                    <pre className="my-2 max-w-full overflow-x-auto rounded-md border border-white/[0.08] bg-black/30 p-2 text-[11px] leading-5 [&>code]:rounded-none [&>code]:bg-transparent [&>code]:p-0">
                      {children}
                    </pre>
                  ),
                  img: ({ src, alt }) => (
                    <img
                      src={src}
                      alt={alt ?? ""}
                      className="my-2 max-w-full rounded-md border border-white/[0.08]"
                    />
                  ),
                  hr: () => <hr className="my-4 border-0 border-t border-white/[0.08]" />,
                  blockquote: ({ children }) => (
                    <blockquote className="my-2 border-l-2 border-white/15 pl-3 text-muted-foreground">
                      {children}
                    </blockquote>
                  ),
                  table: ({ children }) => (
                    <div className="my-2 max-w-full overflow-x-auto">
                      <table className="w-full border-collapse text-[11px]">{children}</table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th className="border border-white/10 px-2 py-1 text-left font-medium">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-white/10 px-2 py-1 align-top">{children}</td>
                  ),
                }}
              >
                {skill.body || ""}
              </ReactMarkdown>
              {skill.truncated ? (
                <p className="mt-2 text-[10px] text-muted-foreground">
                  {t("agent.skills.truncated")}
                </p>
              ) : null}
            </div>
            <div className="mt-3 flex justify-end">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 gap-1.5 px-2 text-[11px] font-normal"
                disabled={downloading}
                onClick={() => void handleDownload()}
              >
                {downloading ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Download className="size-3.5" />
                )}
                {t("agent.skills.download")}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
