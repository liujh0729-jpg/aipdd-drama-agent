// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 记忆面板：查看/编辑长期记忆（MEMORY.md），保存后注入后续回合的上下文。
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAgentMemory, useUpdateAgentMemory } from "@/lib/queries/agent";

import { agentErrorMessage } from "../core";

export function MemoryPanel() {
  const { t } = useTranslation();
  const memory = useAgentMemory();
  const update = useUpdateAgentMemory();
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  // 远端内容变化时同步草稿；用户正在编辑（dirty）时不打断。
  useEffect(() => {
    if (dirty) return;
    setDraft(memory.data?.data.content ?? "");
  }, [memory.data, dirty]);

  const save = async () => {
    try {
      await update.mutateAsync(draft);
      setDirty(false);
      toast.success(t("agent.memory.saved"));
    } catch (error) {
      toast.error(await agentErrorMessage(error));
    }
  };

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <p className="text-xs leading-relaxed text-muted-foreground">{t("agent.memory.description")}</p>
      {memory.isLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <Textarea
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setDirty(true);
          }}
          placeholder={t("agent.memory.placeholder")}
          rows={10}
          className="min-h-40 resize-none font-mono text-xs"
        />
      )}
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!dirty || update.isPending}
          onClick={() => void save()}
          className="h-7 gap-1.5 rounded-[7px] bg-primary px-2 text-[11px] font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
        >
          {update.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Save className="size-3.5" />
          )}
          {t("agent.memory.save")}
        </Button>
      </div>
    </div>
  );
}