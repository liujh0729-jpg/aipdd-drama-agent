// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const nodeSource = readFileSync(
  "src/features/canvas/nodes/TextAnnotationNode.tsx",
  "utf8",
);
const apiSource = readFileSync("src/api/ops.ts", "utf8");

describe("canvas AI text generation contract", () => {
  // [aipdd fork delta] WP-D: 积分估价与计费参数已从本节点拆除，只保留调用契约。
  it("submits the instruction as the AI text generation prompt", () => {
    expect(nodeSource).toContain("submitFreezoneTextGenerate(projectId");
    expect(nodeSource).toContain("prompt,");
  });

  it("keeps the user instruction separate from generated content", () => {
    expect(nodeSource).toContain("value={instruction}");
    expect(nodeSource).toContain(
      "updateNodeData(nodeId, { instruction: event.target.value })",
    );
    expect(nodeSource).toContain("content: result.generated_text");
  });

  it("reuses AI text generation when drafting a text-to-video prompt", () => {
    expect(nodeSource).toContain("const prompt = (promptOverride ?? instruction).trim()");
    expect(nodeSource).toContain("mode === 'imageToPrompt' || mode === 'textToVideo'");
    expect(nodeSource).toContain("void runTextGenerate()");
    expect(nodeSource).toContain("void runInstructionTranslate()");
    expect(nodeSource).not.toContain("runTextToVideo");
    expect(nodeSource).not.toContain("submitFreezoneVideoGen");
  });

  it("translates the creation prompt without replacing generated text", () => {
    expect(nodeSource).toContain("const trimmed = instruction.trim()");
    expect(nodeSource).toContain("text: trimmed");
    expect(nodeSource).toContain("instruction: result.translated_text");
  });

  it("keeps clone-audio text in content for downstream audio generation", () => {
    expect(nodeSource).toContain("mode === 'textToMusic'");
    expect(nodeSource).toContain("<ContentOpsPanel");
    expect(nodeSource).toContain(
      "updateNodeData(nodeId, { content: event.target.value })",
    );
    expect(nodeSource).toContain("content: result.translated_text");
  });

  it("shows the shared progress overlay while AI text is generating", () => {
    expect(nodeSource.match(/<NodeGenerationOverlay/g) ?? []).toHaveLength(2);
    expect(nodeSource).toContain("startedAt={data.generationStartedAt ?? null}");
  });

  it("uses the dedicated text generation API and result endpoint", () => {
    expect(apiSource).toContain("/freezone/text/generate");
    expect(apiSource).toContain(
      "/freezone/jobs/freezone_text_generate/",
    );
  });
});
