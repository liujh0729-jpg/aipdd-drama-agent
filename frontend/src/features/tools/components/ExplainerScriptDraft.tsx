// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 讲解类视频「台词草稿」：两段式出片的中间产物与逐镜编辑器。
//
// 草稿的形状与后端 `freezone/explainer_script.py` **逐字段一致**（snake_case 原样读、
// 原样回传）：读取 → 编辑 → 整份交给成片任务的 `locked_script`，中间不做任何字段转换。
// 一旦前端另建一套驼峰结构，两边字段迟早会走样，而走样的代价是成片按错稿渲染。
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** 草稿任务类型：两族草稿（科普讲解 / 白板动画）共用一条只跑文本的任务。 */
export const EXPLAINER_SCRIPT_TASK_TYPE = "freezone_explainer_script";

export type ScriptDraftKind = "explainer" | "whiteboard";

/**
 * 与后端 `explainer.py` 的 EXPLAINER_PRIMITIVES / EXPLAINER_MOTIONS 同一份受限词汇。
 * 下拉里只放渲染器画得出来的规格：让用户选一个后端会归一化掉的值，等于让他白改。
 */
export const EXPLAINER_PRIMITIVE_IDS = [
  "term",
  "flow",
  "compare",
  "bars",
  "bignumber",
  "list",
  "cycle",
  "timeline",
] as const;

export const EXPLAINER_MOTION_IDS = [
  "softin",
  "typewriter",
  "lightup",
  "travel",
  "countup",
] as const;

export interface ExplainerDraftShot {
  chapter: string;
  sentences: string[];
  primitive: string;
  headline: string;
  items: string[];
  numbers: string[];
  emphasis: number;
  motion: string;
}

export interface ExplainerDraftTitle {
  big: string;
  rest: string;
  en: string;
  tagline: string;
}

export interface ExplainerDraft {
  draft_version: number;
  kind: "explainer";
  title: ExplainerDraftTitle;
  duration_s: number;
  estimated_seconds: number;
  shots: ExplainerDraftShot[];
}

export interface WhiteboardDraftScene {
  text: string;
  keyText: string;
  scenePrompt: string;
}

export interface WhiteboardDraft {
  draft_version: number;
  kind: "whiteboard";
  estimated_seconds: number;
  scenes: WhiteboardDraftScene[];
}

export type ScriptDraft = ExplainerDraft | WhiteboardDraft;

const FIELD_CLASS =
  "h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary";

/** 朗读权重：中日韩字符算 1，其余可见字符算 0.55（与后端 `_text_weight` 同口径）。 */
function textWeight(text: string): number {
  let weight = 0;
  for (const char of text) {
    if (/\s/.test(char)) continue;
    weight += char.codePointAt(0)! > 0x2e80 ? 1 : 0.55;
  }
  return weight;
}

const str = (value: unknown): string => (typeof value === "string" ? value : "");
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(str) : [];

/**
 * 任务 result 里的草稿 → 可编辑草稿；形状不对或类型不符就返回 null。
 *
 * 返回 null 不是错误：面板拿到的可能是上一个版本留下的产物、或另一种草稿
 * （白板面板读到讲解草稿），两种情况都只能当成「还没有草稿」。
 */
export function readScriptDraft(raw: unknown, kind: ScriptDraftKind): ScriptDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (str(data.kind) !== kind) return null;
  if (kind === "whiteboard") {
    const scenes = (Array.isArray(data.scenes) ? data.scenes : [])
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
      .map((item) => ({
        text: str(item.text),
        keyText: str(item.keyText),
        scenePrompt: str(item.scenePrompt),
      }));
    if (scenes.length === 0) return null;
    return {
      draft_version: Number(data.draft_version) || 1,
      kind: "whiteboard",
      estimated_seconds: Number(data.estimated_seconds) || 0,
      scenes,
    };
  }
  const shots = (Array.isArray(data.shots) ? data.shots : [])
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      chapter: str(item.chapter),
      sentences: strList(item.sentences),
      primitive: str(item.primitive) || "term",
      headline: str(item.headline),
      items: strList(item.items),
      numbers: strList(item.numbers),
      emphasis: Number.isInteger(item.emphasis) ? Number(item.emphasis) : -1,
      motion: str(item.motion) || "softin",
    }));
  if (shots.length === 0) return null;
  const title = (data.title ?? {}) as Record<string, unknown>;
  return {
    draft_version: Number(data.draft_version) || 1,
    kind: "explainer",
    title: {
      big: str(title.big),
      rest: str(title.rest),
      en: str(title.en),
      tagline: str(title.tagline),
    },
    duration_s: Number(data.duration_s) || 180,
    estimated_seconds: Number(data.estimated_seconds) || 0,
    shots,
  };
}

const splitList = (raw: string): string[] => raw.split(/[,，、]/);

/** 按改过的台词现算时长，而不是沿用生成时的估算值（改短了还显示原来的秒数是骗人）。 */
export function estimateDraftSeconds(draft: ScriptDraft): number {
  const texts =
    draft.kind === "whiteboard"
      ? draft.scenes.map((scene) => scene.text)
      : draft.shots.flatMap((shot) => shot.sentences);
  const weight = texts.reduce((sum, text) => sum + textWeight(text), 0);
  return Math.round((weight / 4.2) * 10) / 10;
}

/** 面板提交前的兜底：清空行/空条目在这里滤掉，后端就不用再猜「空串算不算一句」。 */
export function toLockedScript(draft: ScriptDraft): Record<string, unknown> {
  if (draft.kind === "whiteboard") {
    return {
      draft_version: draft.draft_version,
      kind: "whiteboard",
      scenes: draft.scenes
        .map((scene) => ({
          text: scene.text.trim(),
          keyText: scene.keyText.trim(),
          scenePrompt: scene.scenePrompt.trim(),
        }))
        .filter((scene) => Boolean(scene.text)),
    };
  }
  return {
    draft_version: draft.draft_version,
    kind: "explainer",
    title: draft.title,
    duration_s: draft.duration_s,
    shots: draft.shots
      .map((shot) => ({
        chapter: shot.chapter.trim(),
        sentences: shot.sentences.map((line) => line.trim()).filter(Boolean),
        primitive: shot.primitive,
        headline: shot.headline.trim(),
        items: shot.items.map((item) => item.trim()).filter(Boolean),
        numbers: shot.numbers.map((item) => item.trim()).filter(Boolean),
        emphasis: shot.emphasis,
        motion: shot.motion,
      }))
      .filter((shot) => shot.sentences.length > 0),
  };
}

/** 草稿里还剩不剩可渲染的内容（用户可能把每一镜都删了）。 */
export function hasDraftContent(draft: ScriptDraft): boolean {
  const locked = toLockedScript(draft);
  return draft.kind === "whiteboard"
    ? (locked.scenes as unknown[]).length > 0
    : (locked.shots as unknown[]).length > 0;
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
      {children}
    </Label>
  );
}

function ShotCard({
  shot,
  index,
  onChange,
  onRemove,
}: {
  shot: ExplainerDraftShot;
  index: number;
  onChange: (next: ExplainerDraftShot) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const empty = shot.sentences.every((line) => !line.trim());
  return (
    <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">
          {t("tools.explainerScript.shotLabel", { n: index + 1 })}
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onRemove}
          aria-label={t("tools.explainerScript.removeShot")}
          className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground"
        >
          <Trash2 className="size-3.5" />
          {t("tools.explainerScript.removeShot")}
        </Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.chapter")}</FieldLabel>
          <input
            value={shot.chapter}
            onChange={(e) => onChange({ ...shot, chapter: e.target.value })}
            placeholder={t("tools.explainerScript.chapterPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.headline")}</FieldLabel>
          <input
            value={shot.headline}
            onChange={(e) => onChange({ ...shot, headline: e.target.value })}
            placeholder={t("tools.explainerScript.headlinePlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
      </div>
      <div className="space-y-1">
        <FieldLabel>{t("tools.explainerScript.sentences")}</FieldLabel>
        <Textarea
          rows={3}
          value={shot.sentences.join("\n")}
          onChange={(e) => onChange({ ...shot, sentences: e.target.value.split("\n") })}
          placeholder={t("tools.explainerScript.sentencesPlaceholder")}
        />
        {empty ? (
          <p className="text-[11px] leading-relaxed text-amber-400/90">
            {t("tools.explainerScript.emptyShot")}
          </p>
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.primitive")}</FieldLabel>
          <select
            value={shot.primitive}
            onChange={(e) => onChange({ ...shot, primitive: e.target.value })}
            className={FIELD_CLASS}
          >
            {EXPLAINER_PRIMITIVE_IDS.map((id) => (
              <option key={id} value={id}>
                {t(`tools.explainerScript.primitives.${id}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.motion")}</FieldLabel>
          <select
            value={shot.motion}
            onChange={(e) => onChange({ ...shot, motion: e.target.value })}
            className={FIELD_CLASS}
          >
            {EXPLAINER_MOTION_IDS.map((id) => (
              <option key={id} value={id}>
                {t(`tools.explainerScript.motions.${id}`)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.items")}</FieldLabel>
          <input
            value={shot.items.join("，")}
            onChange={(e) => onChange({ ...shot, items: splitList(e.target.value) })}
            placeholder={t("tools.explainerScript.itemsPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.numbers")}</FieldLabel>
          <input
            value={shot.numbers.join("，")}
            onChange={(e) => onChange({ ...shot, numbers: splitList(e.target.value) })}
            placeholder={t("tools.explainerScript.numbersPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
      </div>
    </div>
  );
}

function SceneCard({
  scene,
  index,
  onChange,
  onRemove,
}: {
  scene: WhiteboardDraftScene;
  index: number;
  onChange: (next: WhiteboardDraftScene) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">
          {t("tools.explainerScript.sceneLabel", { n: index + 1 })}
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onRemove}
          aria-label={t("tools.explainerScript.removeScene")}
          className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground"
        >
          <Trash2 className="size-3.5" />
          {t("tools.explainerScript.removeScene")}
        </Button>
      </div>
      <div className="space-y-1">
        <FieldLabel>{t("tools.explainerScript.sceneText")}</FieldLabel>
        <Textarea
          rows={3}
          value={scene.text}
          onChange={(e) => onChange({ ...scene, text: e.target.value })}
          placeholder={t("tools.explainerScript.sceneTextPlaceholder")}
        />
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.keyText")}</FieldLabel>
          <input
            value={scene.keyText}
            onChange={(e) => onChange({ ...scene, keyText: e.target.value })}
            placeholder={t("tools.explainerScript.keyTextPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
        <div className="space-y-1">
          <FieldLabel>{t("tools.explainerScript.scenePrompt")}</FieldLabel>
          <input
            value={scene.scenePrompt}
            onChange={(e) => onChange({ ...scene, scenePrompt: e.target.value })}
            placeholder={t("tools.explainerScript.scenePromptPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
      </div>
    </div>
  );
}

export interface ScriptDraftCardProps {
  draft: ScriptDraft | null;
  onChange: (next: ScriptDraft | null) => void;
  onGenerate: () => void;
  /** 有草稿任务在排队/运行中。 */
  waiting?: boolean;
  /** 最近一条草稿任务的失败原因（任务不跑在请求里，错误只能从任务回读）。 */
  error?: string;
  /** 生成草稿后源文案/资料又被改过：草稿可能已经对不上。 */
  sourceChanged?: boolean;
  busy?: boolean;
}

/**
 * 台词草稿卡片：生成 / 逐镜编辑 / 丢弃。
 *
 * 「出片」按钮不在这里：成片有十几个参数，摆进这张卡片反而把草稿编辑挤没了。
 * 卡片只负责产出一份可确认的稿子，成片按面板底部的提交按钮走。
 */
export function ScriptDraftCard({
  draft,
  onChange,
  onGenerate,
  waiting = false,
  error = "",
  sourceChanged = false,
  busy = false,
}: ScriptDraftCardProps) {
  const { t } = useTranslation();
  const seconds = useMemo(() => (draft ? estimateDraftSeconds(draft) : 0), [draft]);
  const summary = draft
    ? draft.kind === "whiteboard"
      ? t("tools.explainerScript.summaryScenes", {
          scenes: draft.scenes.length,
          seconds: seconds.toFixed(1),
        })
      : t("tools.explainerScript.summary", {
          shots: draft.shots.length,
          seconds: seconds.toFixed(1),
        })
    : "";

  return (
    <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-xs font-medium text-foreground">
            {t("tools.explainerScript.title")}
          </p>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {draft ? summary : t("tools.explainerScript.hint")}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {draft ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => onChange(null)}
              className="h-8 px-2 text-xs text-muted-foreground"
            >
              {t("tools.explainerScript.discard")}
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onGenerate}
            disabled={busy || waiting}
            className="h-8 gap-1.5 px-2.5 text-xs"
          >
            {waiting ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {draft
              ? t("tools.explainerScript.regenerate")
              : t("tools.explainerScript.generate")}
          </Button>
        </div>
      </div>
      {waiting ? (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.explainerScript.waiting")}
        </p>
      ) : null}
      {error ? (
        <p className="text-[11px] leading-relaxed text-red-400">{error}</p>
      ) : null}
      {sourceChanged ? (
        <p className="text-[11px] leading-relaxed text-amber-400/90">
          {t("tools.explainerScript.sourceChanged")}
        </p>
      ) : null}
      {draft === null ? (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.explainerScript.empty")}
        </p>
      ) : draft.kind === "whiteboard" ? (
        <div className="space-y-2">
          {draft.scenes.map((scene, index) => (
            <SceneCard
              key={index}
              scene={scene}
              index={index}
              onChange={(next) => {
                const scenes = [...draft.scenes];
                scenes[index] = next;
                onChange({ ...draft, scenes });
              }}
              onRemove={() =>
                onChange({
                  ...draft,
                  scenes: draft.scenes.filter((_, i) => i !== index),
                })
              }
            />
          ))}
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.explainerScript.lockedNote")}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {draft.shots.map((shot, index) => (
            <ShotCard
              key={index}
              shot={shot}
              index={index}
              onChange={(next) => {
                const shots = [...draft.shots];
                shots[index] = next;
                onChange({ ...draft, shots });
              }}
              onRemove={() =>
                onChange({
                  ...draft,
                  shots: draft.shots.filter((_, i) => i !== index),
                })
              }
            />
          ))}
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.explainerScript.lockedNote")}
          </p>
        </div>
      )}
    </div>
  );
}
