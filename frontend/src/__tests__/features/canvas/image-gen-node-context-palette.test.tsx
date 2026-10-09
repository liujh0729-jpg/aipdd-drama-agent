// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("ImageGenNode context palette", () => {
  it("wires ImageGenNode to context collection and prompt insertion", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/features/canvas/nodes/ImageGenNode.tsx"),
      "utf8",
    );

    // 调色盘按钮通过 NodeContextPromptPaletteButton 接入（该 wrapper 内部订阅
    // nodes/edges 构建 palette，宿主节点不再为它订阅整图）。
    expect(source).toContain("<NodeContextPromptPaletteButton");
    expect(source).toContain("nodeId={id}");
    // 插入走编辑器命令式 API（回调稳定，不再依赖 prompt）。
    expect(source).toContain("contextPromptPaletteInsertionText(entry)");
    expect(source).toContain("insertTextAtCursor(");
    expect(source).toContain("ref={promptEditorRef}");

    // 上下文收集逻辑已下沉到 wrapper —— 在那里仍然构建 palette。
    const wrapperSource = readFileSync(
      resolve(
        process.cwd(),
        "src/features/canvas/nodes/ContextPromptPaletteButton.tsx",
      ),
      "utf8",
    );
    expect(wrapperSource).toContain(
      "buildContextPromptPaletteForNode(nodes, edges, nodeId)",
    );
  });
});
