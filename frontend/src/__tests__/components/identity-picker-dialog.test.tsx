// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    interpolation: { escapeValue: false },
    resources: {
      zh: {
        translation: {
          common: { cancel: "取消", save: "保存" },
          identityPicker: {
            title: "选择本集身份",
            empty: "暂无角色身份",
            aiPlan: "AI 规划",
            defaultIdentity: "默认",
          },
        },
      },
    },
  });
});

const identitiesByCharacter = vi.hoisted(
  (): {
    data: Record<string, Array<{ identity_id: string; identity_name: string }>>;
  } => ({
    data: {
      秦: [
        { identity_id: "秦_幼年", identity_name: "幼年" },
        { identity_id: "秦_青年", identity_name: "青年" },
      ],
    },
  }),
);

vi.mock("@/lib/queries/characters", () => ({
  useCharacterIdentities: (_project: string, character: string) => ({
    data: { ok: true, data: identitiesByCharacter.data[character] ?? [] },
  }),
}));

import { IdentityPickerDialog } from "@/components/identity-picker-dialog";

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

describe("IdentityPickerDialog", () => {
  it("saves episode default identity map together with selected identities", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <Wrapper>
        <IdentityPickerDialog
          open
          onOpenChange={vi.fn()}
          project="demo"
          characters={[{ name: "秦" }]}
          selected={["秦_幼年", "秦_青年"]}
          defaultMap={{ 秦: "秦_幼年" }}
          onChange={onChange}
          onPlan={vi.fn()}
          planPending={false}
        />
      </Wrapper>,
    );

    await user.click(screen.getByRole("radio", { name: "青年 默认" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(onChange).toHaveBeenCalledWith(
      ["秦_幼年", "秦_青年"],
      { 秦: "秦_青年" },
    );
  });
});
