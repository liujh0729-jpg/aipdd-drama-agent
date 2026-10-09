// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  translateSkillDescription,
  translateSkillInputLabel,
  translateSkillName,
  translateSkillOutputLabel,
  translateSkillCardinality,
  translateSkillParameterLabel,
  translateSkillParameterOption,
  translateSkillRequirement,
} from "@/features/freezone/context/skillI18n";
import type { SkillDefinition } from "@/features/freezone/context/skillRoles";

const translations: Record<string, string> = {
  "viewer.threeD.skillDefinitions.freezone_sketch_from_context.name": "从当前背景生成草图",
  "viewer.threeD.skillDefinitions.freezone_sketch_from_context.description": "从镜头上下文和当前背景生成主线草图候选。",
  "viewer.threeD.skillDefinitions.freezone_frame_from_context.name": "从镜头上下文生成分镜",
  "viewer.threeD.skillDefinitions.freezone_frame_from_context.description": "从镜头上下文、草图和参考图生成主线分镜候选。",
  "viewer.threeD.skillInputLabels.beat_context": "镜头上下文",
  "viewer.threeD.skillOutputLabels.current_frame_candidate": "分镜候选",
  "viewer.threeD.skillParameterLabels.freezone_sketch_from_context.aspect_ratio": "比例",
  "viewer.threeD.skillParameterOptions.freezone_sketch_from_context.aspect_ratio.2_3": "竖幅 2:3",
  "viewer.threeD.skillParameterOptions.freezone_sketch_from_context.aspect_ratio.16_9": "横幅 16:9",
  "viewer.threeD.skillParameterLabels.freezone_frame_from_context.background_reference_mode": "背景参考模式",
  "viewer.threeD.skillParameterOptions.freezone_frame_from_context.background_reference_mode.material_only": "仅外观",
  "viewer.threeD.skillParameterOptions.freezone_frame_from_context.background_reference_mode.scene_anchor": "场景锚点",
  "viewer.threeD.skillRequirement.required": "必填",
  "viewer.threeD.skillRequirement.optional": "可选",
  "viewer.threeD.skillCardinality.single": "单个",
  "viewer.threeD.skillCardinality.multi": "多个",
};

const t = ((key: string) => translations[key] ?? key) as never;

describe("mainline skill i18n", () => {
  it("translates backend English names and descriptions by skill id", () => {
    const skill = {
      id: "freezone.sketch_from_context",
      display_name: "Sketch From Selected Background",
      description: "Generate a mainline sketch candidate from beat context and selected background.",
    } as SkillDefinition;

    expect(translateSkillName(skill, t)).toBe("从当前背景生成草图");
    expect(translateSkillDescription(skill, t)).toBe("从镜头上下文和当前背景生成主线草图候选。");
  });

  it("keeps skill node input/output labels in Chinese", () => {
    expect(translateSkillInputLabel("beat_context", "Beat context", t)).toBe("镜头上下文");
    expect(translateSkillOutputLabel("current_frame_candidate", "Current frame candidate", t)).toBe("分镜候选");
  });

  it("translates skill parameter labels and option values", () => {
    expect(
      translateSkillParameterLabel(
        "freezone.sketch_from_context",
        "aspect_ratio",
        "Aspect ratio",
        t,
      ),
    ).toBe("比例");
    expect(
      translateSkillParameterOption(
        "freezone.sketch_from_context",
        "aspect_ratio",
        "2:3",
        t,
      ),
    ).toBe("竖幅 2:3");
    expect(
      translateSkillParameterOption(
        "freezone.sketch_from_context",
        "aspect_ratio",
        "16:9",
        t,
      ),
    ).toBe("横幅 16:9");
    expect(
      translateSkillParameterLabel(
        "freezone.frame_from_context",
        "background_reference_mode",
        "Background reference mode",
        t,
      ),
    ).toBe("背景参考模式");
    expect(
      translateSkillParameterOption(
        "freezone.frame_from_context",
        "background_reference_mode",
        "material_only",
        t,
      ),
    ).toBe("仅外观");
    expect(
      translateSkillParameterOption(
        "freezone.frame_from_context",
        "background_reference_mode",
        "scene_anchor",
        t,
      ),
    ).toBe("场景锚点");
  });

  it("translates requirement and cardinality labels", () => {
    expect(translateSkillRequirement(true, t)).toBe("必填");
    expect(translateSkillRequirement(false, t)).toBe("可选");
    expect(translateSkillCardinality("single", t)).toBe("单个");
    expect(translateSkillCardinality("multi", t)).toBe("多个");
  });
});
