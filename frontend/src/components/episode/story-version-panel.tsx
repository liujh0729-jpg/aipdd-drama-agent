// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分镜脚本页「故事版」面板：用户输入创作要求 → 取提示词库
// freezone.story_version.system 的基础提示词拼接 → submitFreezoneGen 出多格分镜板
// → 轮询取图 → 存进资产库「故事版」分类，页内给出缩略预览。镜头生成视频 /
// 草图生成渲染图的选参考图弹窗据此类目自动带出，无需额外渲染层改动。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ImageIcon, Loader2, Sparkles } from "lucide-react";

import {
  fetchFreezoneJobResult,
  submitFreezoneAddVideoCharacterLibraryItem,
  submitFreezoneGen,
} from "@/api/ops";
import { awaitTaskCompletion, isTaskPollTimeoutError } from "@/api/tasks";
import { notifyTaskStillRunning } from "@/features/canvas/application/errorDialog";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { usePromptLibrary } from "@/lib/queries/prompt-library";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

// 与后端 prompt_library_defaults.py 的条目 key、以及资产库类目保持一致。
const STORY_VERSION_PROMPT_KEY = "freezone.story_version.system";
const STORY_VERSION_REQUIREMENT_VAR = "user_requirement";

// 基础提示词含 {user_requirement} 时按位插入，否则追加到末尾——两种都让用户
// 输入进入最终图像 prompt，覆盖后端模板改动时前端不必跟改。
function composePrompt(base: string, requirement: string): string {
  const token = `{${STORY_VERSION_REQUIREMENT_VAR}}`;
  if (base.includes(token)) return base.split(token).join(requirement);
  return `${base}\n\n${requirement}`;
}

export function StoryVersionPanel({
  project,
  episode,
}: {
  project: string;
  episode: number;
}) {
  const { t } = useTranslation();
  const { data: promptRes } = usePromptLibrary();
  const [requirement, setRequirement] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const basePrompt = useMemo(() => {
    const items = promptRes?.data?.items ?? [];
    const found = items.find((item) => item.key === STORY_VERSION_PROMPT_KEY);
    return (found?.value || found?.default || "").trim();
  }, [promptRes]);

  const handleGenerate = async () => {
    if (busy) return;
    const trimmed = requirement.trim();
    if (!trimmed) {
      toast.error(t("episode.storyVersion.requirementRequired"));
      return;
    }
    if (!basePrompt) {
      toast.error(t("episode.storyVersion.promptMissing"));
      return;
    }
    setBusy(true);
    try {
      const ref = await submitFreezoneGen(project, {
        prompt: composePrompt(basePrompt, trimmed),
        aspectRatio: "16:9",
        imageSize: "2K",
      });
      await awaitTaskCompletion(ref.task_key, project, {
        taskType: ref.task_type,
      });
      const result = await fetchFreezoneJobResult(
        project,
        "freezone_gen",
        ref.job_id,
      );
      if (!result.url) {
        throw new Error("story-version: gen completed without url");
      }
      const categoryLabel = t("canvas.assetLibrary.category.storyVersion");
      await submitFreezoneAddVideoCharacterLibraryItem(project, {
        name: `EP${episode} ${categoryLabel}`,
        media: "image",
        imageUrls: [result.url],
        category: "storyVersion",
      });
      setPreviewUrl(result.url);
      toast.success(t("episode.storyVersion.success"));
    } catch (error) {
      // 轮询超时 ≠ 生成失败：后端还在跑，提示用户稍后到资产库查看即可。
      if (isTaskPollTimeoutError(error)) {
        notifyTaskStillRunning(t);
        return;
      }
      toast.error(backendErrorToastMessage(error, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-[10px] border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center gap-2">
        <ImageIcon className="size-3.5 text-muted-foreground" />
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          {t("episode.storyVersion.title")}
        </h2>
      </div>
      <p className="mb-2 text-xs leading-relaxed text-muted-foreground">
        {t("episode.storyVersion.description")}
      </p>
      <Textarea
        value={requirement}
        onChange={(event) => setRequirement(event.target.value)}
        disabled={busy}
        placeholder={t("episode.storyVersion.placeholder")}
        className="min-h-16 resize-none text-sm"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <Button
          size="sm"
          onClick={handleGenerate}
          disabled={busy || !basePrompt}
          className="h-7 gap-1.5 rounded-[7px] bg-primary px-2.5 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 [&_svg]:size-3.5"
        >
          {busy ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          {t("episode.storyVersion.generate")}
        </Button>
        {previewUrl && (
          <a
            href={previewUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <img
              src={previewUrl}
              alt={t("episode.storyVersion.previewAlt")}
              className="h-10 w-auto rounded-[6px] border border-white/[0.08] object-cover"
            />
            <span className="truncate">
              {t("episode.storyVersion.preview")}
            </span>
          </a>
        )}
      </div>
    </section>
  );
}
