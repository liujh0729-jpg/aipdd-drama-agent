// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 俯视图渲染器回归：每种家具按 shape 画各自符号（不再是清一色灰方块），
// 且 label 非空时画出文字标记（需求：沙发/门窗/人物没有文字标记、分不清）。
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopDownCanvas } from "@/features/canvas/topdown/TopDownCanvas";
import {
  createEmptyScene,
  type TopDownItem,
} from "@/features/canvas/topdown/sceneModel";

function renderScene(items: TopDownItem[]) {
  const scene = { ...createEmptyScene(), items };
  return render(
    <TopDownCanvas
      scene={scene}
      selectedId={null}
      draftArrow={[]}
      svgRef={null}
      onPointerDownBackground={() => {}}
      onPointerDownItem={() => {}}
      onPointerDownRotate={() => {}}
      onDoubleClickBackground={() => {}}
    />,
  );
}

describe("TopDownCanvas 家具符号与文字标记", () => {
  it("每种 shape 都带 data-shape，门用弧线、窗/楼梯用平行直线区分", () => {
    const items: TopDownItem[] = [
      { id: "1", kind: "prop", x: 100, y: 100, w: 120, h: 80, shape: "sofa", label: "" },
      { id: "2", kind: "prop", x: 300, y: 100, w: 120, h: 80, shape: "door", label: "" },
      { id: "3", kind: "prop", x: 500, y: 100, w: 120, h: 80, shape: "window", label: "" },
      { id: "4", kind: "prop", x: 700, y: 100, w: 120, h: 80, shape: "stairs", label: "" },
    ];
    const { container } = renderScene(items);
    for (const shape of ["sofa", "door", "window", "stairs"]) {
      expect(container.querySelector(`[data-shape="${shape}"]`)).toBeTruthy();
    }
    // 门：开合弧线 path。
    expect(container.querySelector('[data-shape="door"] path')).toBeTruthy();
    // 窗：3 条横线；楼梯：多台阶横线。
    expect(container.querySelectorAll('[data-shape="window"] line').length).toBeGreaterThanOrEqual(3);
    expect(container.querySelectorAll('[data-shape="stairs"] line').length).toBeGreaterThanOrEqual(4);
    // 沙发：靠背 + 两扶手的软块 rect（叠在底框之上）。
    expect(container.querySelectorAll('[data-shape="sofa"] rect').length).toBeGreaterThanOrEqual(4);
  });

  it("label 非空时画出文字标记（家具与人物）", () => {
    const items: TopDownItem[] = [
      { id: "p", kind: "person", x: 200, y: 200, label: "人物1", color: "#e23b3b" },
      { id: "s", kind: "prop", x: 100, y: 100, w: 120, h: 80, shape: "sofa", label: "沙发" },
    ];
    const { container } = renderScene(items);
    expect(container.textContent).toContain("沙发");
    expect(container.textContent).toContain("人物1");
  });
});
