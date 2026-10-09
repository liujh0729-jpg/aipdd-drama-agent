// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 技能面板：安装（SKILL.md / zip 技能包）、启停、删除。
// 技能由用户自装，文件落在用户级 Agent 数据目录，与主线的数据完全隔离。
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
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
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

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
                <span className="min-w-0 truncate text-xs font-medium text-foreground">
                  {skill.title || skill.name}
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={skill.enabled}
                    title={skill.enabled ? t("agent.skills.disable") : t("agent.skills.enable")}
                    disabled={setEnabled.isPending}
                    onClick={() =>
                      setEnabled.mutate({ name: skill.name, enabled: !skill.enabled })
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
                  <button
                    type="button"
                    title={
                      pendingDelete === skill.name
                        ? t("agent.skills.deleteConfirm")
                        : t("agent.skills.delete")
                    }
                    onClick={() => {
                      if (pendingDelete === skill.name) {
                        setPendingDelete(null);
                        remove.mutate(skill.name);
                      } else {
                        setPendingDelete(skill.name);
                        window.setTimeout(() => setPendingDelete(null), 3000);
                      }
                    }}
                    className={cn(
                      "rounded-sm p-1 transition-colors",
                      pendingDelete === skill.name
                        ? "bg-red-500/15 text-red-400"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
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
    </div>
  );
}