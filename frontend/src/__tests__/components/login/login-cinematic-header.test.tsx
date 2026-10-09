// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 登录页顶栏按用户要求不保留任何右上角「快捷入口 icon」：只留 Brand。
// 曾经的「模型广场 / MCP」两个快捷图标已下线，本用例锁定「无快捷入口」这一契约，防止
// 后续批次把它们带回来。语言切换器是另一类、按需求新增的正当控件（其自身行为由
// language-switcher.test.tsx 覆盖），此处 mock 掉，保持本用例只守「快捷入口不回归」。
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginCinematicHeader } from "@/components/login/cinematic/LoginCinematicHero";

vi.mock("@/components/login/login-stage", () => ({ Brand: () => <div /> }));
vi.mock("@/components/login/cinematic/MoreInfoMenu", () => ({ MoreInfoMenu: () => null }));
vi.mock("@/components/layout/language-switcher", () => ({ LanguageSwitcher: () => null }));

describe("LoginCinematicHeader top bar", () => {
  it("renders no quick-entry icon buttons", () => {
    render(<LoginCinematicHeader />);

    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByTitle("nav.models")).toBeNull();
    expect(screen.queryByTitle("nav.mcp")).toBeNull();
  });
});
