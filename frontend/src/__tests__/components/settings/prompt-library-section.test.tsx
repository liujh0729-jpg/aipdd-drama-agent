// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import i18next from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptLibrarySection } from "@/components/settings/prompt-library-section";
import { enTranslation } from "../../helpers/i18n-fixtures";

const saveOverride = vi.hoisted(() => vi.fn());
const resetOverride = vi.hoisted(() => vi.fn());
const refetch = vi.hoisted(() => vi.fn());
const listState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const confirmDialog = vi.hoisted(() => vi.fn());

vi.mock("@/lib/queries/prompt-library", () => ({
  PROMPT_OVERRIDE_MAX_CHARS: 20_000,
  usePromptLibrary: () => listState.current,
  useSavePromptOverride: () => ({ mutateAsync: saveOverride, isPending: false }),
  useResetPromptOverride: () => ({
    mutateAsync: resetOverride,
    isPending: false,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

vi.mock("@/components/confirm-dialog-host", () => ({ confirmDialog }));

const PORTRAIT_LABEL = "角色立绘提示词（动画风格）";
const MOTION_LABEL = "视频运镜系统提示词（I2V Motion Director）";

const PORTRAIT = {
  key: "character.portrait.animation",
  group: "character",
  label: PORTRAIT_LABEL,
  description: "生成动画风格角色脸部身份参考图的提示词，用于角色立绘锚点。",
  variables: ["character_tag", "character_name"],
  default: "DEFAULT PORTRAIT {character_tag}",
  value: "DEFAULT PORTRAIT {character_tag}",
  is_overridden: false,
  updated_at: null,
};

const MOTION = {
  key: "video.motion_director.system",
  group: "video",
  label: MOTION_LABEL,
  description: "把分镜首帧转成图生视频运动描述的系统提示词，产出 4-6 句时长运动链。",
  variables: [],
  default: "MOTION DEFAULT",
  value: "MOTION CUSTOM",
  is_overridden: true,
  updated_at: "2026-09-24T00:00:00+00:00",
};

const PAYLOAD = {
  revision: "1:2026-09-24",
  groups: ["character", "video", "freezone", "grid"],
  items: [PORTRAIT, MOTION],
};

/** 后端 prompt_library_defaults.DEFAULT_PROMPTS 的 20 个 key，词条表必须全覆盖。 */
const PROMPT_KEYS = [
  "character.portrait.animation",
  "character.portrait.realistic",
  "character.identity_sheet.animation",
  "character.identity_sheet.realistic",
  // [aipdd fork delta] 五官垫图
  "character.face_mat",
  "character.costume_reference",
  "character.ethnicity.fallback",
  "video.motion_director.system",
  "video.keyframe_transition.system",
  "freezone.translator.system",
  "freezone.text_writer.system",
  "freezone.story_script.system",
  "freezone.story_script.video_suffix",
  // [aipdd fork delta] 故事版基础提示词
  "freezone.story_version.system",
  "grid.ethnicity.fallback",
  "grid.colorization_rules",
  "grid.prop_colorization_rule",
  "grid.scene_anchor_rule",
  "grid.material_only_scene_rule",
  "grid.rough_scene_repair_rule",
  "grid.temporal_framing",
];

function cardOf(label: string): HTMLElement {
  const node = screen.getByText(label).closest("[data-prompt-key]");
  if (!node) throw new Error(`missing prompt card for ${label}`);
  return node as HTMLElement;
}

function readLocale(language: "zh" | "en" | "vi"): Record<string, any> {
  return JSON.parse(
    readFileSync(`public/locales/${language}/translation.json`, "utf8"),
  );
}

function promptLibraryEntries(bundle: Record<string, any>): string[] {
  const found: string[] = [];
  const walk = (node: Record<string, any>, prefix: string) => {
    for (const [part, value] of Object.entries(node)) {
      if (
        value &&
        typeof value === "object" &&
        "label" in (value as object) &&
        "description" in (value as object)
      ) {
        found.push(prefix ? `${prefix}.${part}` : part);
      } else if (value && typeof value === "object") {
        walk(value as Record<string, any>, prefix ? `${prefix}.${part}` : part);
      }
    }
  };
  walk(bundle.settings.promptLibrary.items, "");
  return found;
}

beforeEach(() => {
  vi.clearAllMocks();
  listState.current = {
    isLoading: false,
    isError: false,
    data: { data: PAYLOAD },
    refetch,
  };
});

describe("PromptLibrarySection", () => {
  it("按分组渲染提示词，标出变量与已修改状态", () => {
    render(<PromptLibrarySection />);

    expect(screen.getByText("角色与身份")).toBeInTheDocument();
    expect(screen.getByText("视频运镜")).toBeInTheDocument();
    expect(screen.getByText(PORTRAIT_LABEL)).toBeInTheDocument();
    expect(screen.getByText(MOTION_LABEL)).toBeInTheDocument();
    expect(screen.getByText("{character_tag}")).toBeInTheDocument();
    expect(screen.getByText("共 2 条，其中 1 条已修改")).toBeInTheDocument();
    expect(screen.getByText("已修改", { exact: true })).toBeInTheDocument();
  });

  it("修改后保存走覆盖接口，内容为空时不提交", async () => {
    render(<PromptLibrarySection />);
    const card = cardOf(PORTRAIT_LABEL);
    const textarea = within(card).getByLabelText(PORTRAIT_LABEL);

    fireEvent.change(textarea, { target: { value: "   " } });
    fireEvent.click(within(card).getByRole("button", { name: "保存" }));
    expect(saveOverride).not.toHaveBeenCalled();
    expect(screen.getByText("提示词内容不能为空")).toBeInTheDocument();

    saveOverride.mockResolvedValueOnce({ ok: true, data: PAYLOAD });
    fireEvent.change(textarea, { target: { value: "MY PORTRAIT" } });
    fireEvent.click(within(card).getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(saveOverride).toHaveBeenCalledWith({
        key: "character.portrait.animation",
        value: "MY PORTRAIT",
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith("已保存「角色立绘提示词（动画风格）」，立即生效");
  });

  it("恢复默认先确认，再调删除接口", async () => {
    render(<PromptLibrarySection />);
    const card = cardOf(MOTION_LABEL);

    confirmDialog.mockResolvedValueOnce(false);
    fireEvent.click(within(card).getByRole("button", { name: "恢复默认" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(resetOverride).not.toHaveBeenCalled();

    confirmDialog.mockResolvedValueOnce(true);
    resetOverride.mockResolvedValueOnce({ ok: true, data: PAYLOAD });
    fireEvent.click(within(card).getByRole("button", { name: "恢复默认" }));
    await waitFor(() =>
      expect(resetOverride).toHaveBeenCalledWith("video.motion_director.system"),
    );
    expect(toastSuccess).toHaveBeenCalledWith(
      "已恢复「视频运镜系统提示词（I2V Motion Director）」的默认提示词",
    );
  });

  it("搜索按名称 / 说明 / 内容过滤", () => {
    render(<PromptLibrarySection />);
    const search = screen.getByPlaceholderText("搜索提示词（名称 / 说明 / 内容）…");

    fireEvent.change(search, { target: { value: "运镜" } });
    expect(screen.queryByText(PORTRAIT_LABEL)).toBeNull();
    expect(screen.getByText(MOTION_LABEL)).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "MOTION CUSTOM" } });
    expect(screen.getByText(MOTION_LABEL)).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "没有这条" } });
    expect(screen.getByText("没有匹配的提示词")).toBeInTheDocument();
  });

  it("英文界面渲染英文词条，而不是中文兜底", async () => {
    const en = i18next.createInstance();
    await en.use(initReactI18next).init({
      lng: "en",
      fallbackLng: "en",
      interpolation: { escapeValue: false },
      resources: { en: { translation: enTranslation } },
      react: { useSuspense: false },
    });
    render(
      <I18nextProvider i18n={en}>
        <PromptLibrarySection />
      </I18nextProvider>,
    );

    expect(screen.getByText("Characters & Identity")).toBeInTheDocument();
    expect(screen.getByText("Character portrait prompt (animated)")).toBeInTheDocument();
    expect(screen.queryByText(PORTRAIT_LABEL)).toBeNull();
  });

  it("三语词条覆盖面板文案、分组名与全部 20 条提示词", () => {
    for (const language of ["zh", "en", "vi"] as const) {
      const bundle = readLocale(language);
      const library = bundle.settings.promptLibrary;
      expect(bundle.settings.pages.prompts).toBeTruthy();
      for (const key of [
        "title",
        "description",
        "summary",
        "searchPlaceholder",
        "variablesLabel",
        "save",
        "reset",
        "overriddenBadge",
        "loadFailed",
      ]) {
        expect(library[key], `${language}.${key}`).toBeTruthy();
      }
      for (const group of ["character", "video", "freezone", "grid"]) {
        expect(library.groups[group], `${language}.groups.${group}`).toBeTruthy();
      }
      expect(promptLibraryEntries(bundle).sort()).toEqual([...PROMPT_KEYS].sort());
    }
  });

  it("设置对话框挂上第三页并渲染面板，查询层指向提示词库接口", () => {
    const dialog = readFileSync(
      "src/components/settings/settings-dialog.tsx",
      "utf8",
    );
    // [aipdd fork delta] 设置对话框后来追加了 styles / world / apiKeys 等页，这里守的
    // 是「prompts 页已挂上」而不是页签字面量全等；只断言联合类型的前缀，避免每加
    // 一页就红一条用例。
    expect(dialog).toContain("useState<");
    // [aipdd fork delta] 联合类型已随 prettier 拆成多行（且后续又追加了 apiKeys /
    // users / styles / world / vectcut 等页）：逐项断言关键页存在，不再匹配单行前缀。
    for (const pageKey of ['"models"', '"storage"', '"prompts"']) {
      expect(dialog).toContain(pageKey);
    }
    expect(dialog).toContain('t("settings.pages.prompts")');
    expect(dialog).toContain("<PromptLibrarySection />");

    const queries = readFileSync("src/lib/queries/prompt-library.ts", "utf8");
    expect(queries).toContain('api/v1/prompt-library');
    expect(queries).toContain(".put(");
    expect(queries).toContain(".delete(");
    expect(readFileSync("src/lib/query-keys.ts", "utf8")).toContain(
      'promptLibrary: () => ["prompt-library"] as const',
    );
  });
});