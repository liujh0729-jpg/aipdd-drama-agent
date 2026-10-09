// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 画布模型选择器的渠道彩色胶囊：模型名旁必须标出该模型所属
// 渠道（下拉每项 + 触发器已选项）。展示名与配色取自 @/lib/provider-labels：
// aipdd→AIPDD、newapi→NewAPI、openai→OpenAI（其它额外渠道）、volcengine→
// VolcEngine；channel 缺省回退 providerId；空串 = 明确不标注（如音频面板合成的
// 「默认引擎」）。
//
// 用显式 `models` prop 渲染：picker 对四个 hook 全部传 null 静默，零网络、零 mock。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ProviderModelPicker,
  type ModelOption,
} from "@/features/canvas/ui/ProviderModelPicker";

const MODELS: ModelOption[] = [
  {
    id: "ap-glm-5.2",
    // providerId 是执行身份（这里按真实别名数据填 openai），展示渠道另填 aipdd：
    // 胶囊必须读 channel 而不是 providerId。
    providerId: "openai",
    apiModel: "ap-glm-5.2",
    label: "GLM 5.2",
    channel: "aipdd",
  },
  {
    id: "seedance-2.0-mini",
    providerId: "newapi",
    apiModel: "newapi_seedance-2.0-mini",
    label: "Seedance 2.0 Mini",
    channel: "volcengine",
  },
  {
    // channel 缺省 → 回退 providerId 展示（huimeng → HuiMeng）。
    id: "huimeng/gpt-image-2",
    providerId: "huimeng",
    apiModel: "huimeng_gpt_image2",
    label: "HuiMeng GPT Image 2",
  },
  {
    // 未登记渠道 → 原样回显 + 中性样式，绝不猜色。
    id: "acme/mystery",
    providerId: "newapi",
    apiModel: "mystery-1",
    label: "Mystery One",
    channel: "acme-cloud",
  },
  {
    // 空串 = 明确不标注（音频面板合成的「默认引擎」按此约定构造）。
    id: "",
    providerId: "newapi",
    apiModel: "",
    label: "默认引擎",
    channel: "",
  },
];

function renderPicker(selectedModelId = "ap-glm-5.2") {
  return render(
    <ProviderModelPicker
      selectedModelId={selectedModelId}
      onChange={() => {}}
      models={MODELS}
    />,
  );
}

describe("ProviderModelPicker 渠道彩色胶囊", () => {
  it("触发器在已选模型名旁标注其渠道（优先读 channel，而非 providerId）", () => {
    const { container } = renderPicker();

    const trigger = screen.getByRole("button");
    const badge = trigger.querySelector('[data-channel="aipdd"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe("AIPDD");
    // 未打开下拉时页面上只应有触发器这一枚胶囊。
    expect(container.querySelectorAll("[data-channel]")).toHaveLength(1);
  });

  it("下拉每项在模型名旁标注渠道；缺省回退 providerId、空串不标注", () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button"));

    // 触发器 + 5 个选项。
    expect(screen.getAllByRole("button")).toHaveLength(6);
    // 触发器 1 枚 + 下拉 GLM 5.2 选项 1 枚。
    expect(document.querySelectorAll('[data-channel="aipdd"]')).toHaveLength(2);

    expect(document.querySelector('[data-channel="volcengine"]')?.textContent).toBe(
      "VolcEngine",
    );
    // channel 缺省 → 回退 providerId（huimeng → HuiMeng）。
    expect(document.querySelector('[data-channel="huimeng"]')?.textContent).toBe(
      "HuiMeng",
    );

    // 未登记渠道：原样回显 + 中性样式（无 chart token）。
    const unknownBadge = document.querySelector('[data-channel="acme-cloud"]');
    expect(unknownBadge?.textContent).toBe("acme-cloud");
    expect(unknownBadge?.className).toContain("text-text-muted");
    expect(unknownBadge?.className).not.toMatch(/chart-/);

    // 空串 = 明确不标注：「默认引擎」所在行内没有任何渠道胶囊。
    const defaultRow = screen.getByRole("button", { name: /默认引擎/ });
    expect(defaultRow.querySelector("[data-channel]")).toBeNull();
  });

  it("不同渠道的胶囊配色经 DESIGN.md 的 chart token 区分", () => {
    renderPicker();
    fireEvent.click(screen.getByRole("button"));

    const aipdd = document.querySelector('[data-channel="aipdd"]');
    const volcengine = document.querySelector('[data-channel="volcengine"]');
    expect(aipdd?.className).toContain("text-chart-4");
    expect(aipdd?.className).toContain("bg-chart-4/16");
    expect(volcengine?.className).toContain("text-chart-5");
    expect(aipdd?.className).not.toBe(volcengine?.className);
  });
});