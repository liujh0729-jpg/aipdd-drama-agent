// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] ModelPickerCore 的共享行为契约：画布 ProviderModelPicker 与工具页
// ModelSelect 合并后的公共语义都由这里保证 —— combobox/listbox/option/group 全套
// aria、Esc 与外点关闭、选中回调后自动关闭、禁用项 aria-disabled + hover 提示
// （且不可选）、画布模式保持无 role 的既有 DOM 结构。
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ModelPickerCore,
  type ModelPickerCoreProps,
} from "@/components/model-picker/ModelPickerCore";

const GROUPS = [
  {
    label: "Seedance 系列",
    options: [
      { value: "seedance-2.0-mini", label: "Seedance 2.0 Mini", channel: "volcengine" },
      {
        value: "seedance-1.0",
        label: "Seedance 1.0",
        channel: "volcengine",
        disabledReason: "挂载参考素材时不可用",
      },
    ],
  },
  {
    options: [{ value: "", label: "默认引擎" }],
  },
];

function renderCore(overrides: Partial<ModelPickerCoreProps> = {}) {
  const onSelect = vi.fn();
  const view = render(
    <ModelPickerCore
      groups={GROUPS}
      selectedValue="seedance-2.0-mini"
      onSelect={onSelect}
      triggerLabel="当前模型"
      triggerClassName="trigger"
      chevronClassName="chevron"
      popoverClassName="popover"
      withListboxRole
      ariaLabel="模型"
      {...overrides}
    />,
  );
  return { onSelect, ...view };
}

describe("ModelPickerCore 共享行为契约", () => {
  it("工具页模式：combobox/listbox/option/group 全套 aria 语义完整", () => {
    renderCore();
    const trigger = screen.getByRole("combobox", { name: "模型" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-haspopup", "listbox");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const listbox = screen.getByRole("listbox", { name: "模型" });
    const group = within(listbox).getByRole("group", { name: "Seedance 系列" });
    expect(within(group).getByRole("option", { name: /Seedance 1.0/ })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(within(listbox).getByRole("option", { name: /Seedance 2.0 Mini/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // 分组头是纯展示（不在 option 语义里）。
    expect(within(listbox).getByText("Seedance 系列")).toBeInTheDocument();
  });

  it("选中回调后自动关闭", () => {
    const { onSelect } = renderCore();
    fireEvent.click(screen.getByRole("combobox", { name: "模型" }));
    fireEvent.click(screen.getByRole("option", { name: /默认引擎/ }));
    expect(onSelect).toHaveBeenCalledWith("");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("Esc 关闭（画布模式同样生效）", () => {
    renderCore({ withListboxRole: undefined, ariaLabel: undefined });
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button", { name: /默认引擎/ })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("button", { name: /默认引擎/ })).toBeNull();
  });

  it("外点关闭", () => {
    renderCore();
    fireEvent.click(screen.getByRole("combobox", { name: "模型" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("禁用项：aria-disabled、点击不触发选择也不关闭，hover 出提示", () => {
    const { onSelect } = renderCore();
    fireEvent.click(screen.getByRole("combobox", { name: "模型" }));
    const disabled = screen.getByRole("option", { name: /Seedance 1.0/ });
    expect(disabled).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(disabled);
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    // hover 出提示（portal 到 body 的浮层），移出即消。
    fireEvent.mouseEnter(disabled);
    expect(screen.getByText("挂载参考素材时不可用")).toBeInTheDocument();
    fireEvent.mouseLeave(disabled);
    expect(screen.queryByText("挂载参考素材时不可用")).toBeNull();
  });

  it("画布模式：触发器与选项都不带 role（保持原 DOM 结构）", () => {
    renderCore({ withListboxRole: undefined, ariaLabel: undefined, stopPropagation: true });
    const trigger = screen.getByRole("button");
    expect(trigger).not.toHaveAttribute("role");
    fireEvent.click(trigger);
    const option = screen.getByRole("button", { name: /Seedance 2.0 Mini/ });
    expect(option).not.toHaveAttribute("role");
    expect(option).not.toHaveAttribute("aria-selected");
  });
});