// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 展示用模型名去渠道前缀（displayModelName 唯一真源）契约：
// 复合调用名 "渠道::模型" 在展示层只显示模型真名，渠道来源已有单独一栏
// （彩色胶囊 / 侧注）；调用值（id / apiModel）不在本函数职责内，仍保留完整复合名。
import i18n from "i18next";
import { afterEach, describe, expect, it } from "vitest";

import {
  displayModelName,
  forkBrandLabel,
  localizedBrandDescription,
  localizedBrandName,
  providerChannelLabel,
  providerOfficialSiteUrl,
} from "@/lib/provider-labels";
import { enTranslation } from "../helpers/i18n-fixtures";

describe("displayModelName", () => {
  it("传入渠道时精确剥「渠道::」前缀（大小写不敏感）", () => {
    expect(displayModelName("aipdd::ap-glm-5.2", "aipdd")).toBe("ap-glm-5.2");
    expect(displayModelName("aipdd::ap-glm-5.2", "AIPDD")).toBe("ap-glm-5.2");
    expect(displayModelName("LocalAPI::gpt-5", "newapi")).toBe("gpt-5");
  });

  it("渠道线索对不上或缺失时退化为「取最后一段」（历史 Agent 口径）", () => {
    expect(displayModelName("aipdd::ap-glm-5.2", "newapi")).toBe("ap-glm-5.2");
    expect(displayModelName("aipdd::ap-glm-5.2")).toBe("ap-glm-5.2");
    expect(displayModelName("aipdd::creator::model-1")).toBe("model-1");
  });

  it("裸名（无分隔符）原样返回，带渠道线索也不误伤", () => {
    expect(displayModelName("Seedance 2.0 Mini", "volcengine")).toBe("Seedance 2.0 Mini");
    expect(displayModelName("ap-glm-5.2", "aipdd")).toBe("ap-glm-5.2");
  });

  it("空值 / 纯空白安全返回空串", () => {
    expect(displayModelName("")).toBe("");
    expect(displayModelName("   ")).toBe("");
    expect(displayModelName("", "aipdd")).toBe("");
  });

  it("前缀被剥空时回退原串（不产出空展示名）", () => {
    expect(displayModelName("aipdd::", "aipdd")).toBe("aipdd::");
  });
});

// [aipdd fork delta] 品牌名按界面语言取词：en/vi 界面不得冒中文「火山 AI MediaKit」，
// zh 界面不得被英文品牌名抢掉；取词失败（词条缺失）只能回表内默认值或语言
// fallback，绝不把 raw key（"brand.mediakit"）渲染给用户。
describe("品牌名按界面语言取词", () => {
  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("中文界面沿用官方中文子产品线名", () => {
    expect(providerChannelLabel("mediakit")).toBe("火山 AI MediaKit");
    expect(localizedBrandName("mediakit")).toBe("火山 AI MediaKit");
  });

  it("英文界面显示 Volcengine AI MediaKit（名 + 目录描述）", async () => {
    i18n.addResourceBundle("en", "translation", enTranslation, true, true);
    await i18n.changeLanguage("en");

    expect(providerChannelLabel("mediakit")).toBe("Volcengine AI MediaKit");
    expect(localizedBrandDescription("mediakit")).toContain("Volcengine AI MediaKit");
    // 渠道归属不变：其余 fork 渠道仍取表内 ASCII 名。
    expect(forkBrandLabel("volcengine")).toBe("VolcEngine");
  });

  it("非本地化渠道返回空串，调用方的后端渠道名不被覆盖", async () => {
    i18n.addResourceBundle("en", "translation", enTranslation, true, true);
    await i18n.changeLanguage("en");

    // newapi / comfyui 等不跳语言：空串 = 调用方继续用自己的渠道名。
    expect(localizedBrandName("newapi")).toBe("");
    expect(localizedBrandName("comfyui")).toBe("");
    expect(localizedBrandDescription("newapi")).toBe("");
  });

  it("目标语言词条缺失时也不把 raw key 漏给用户", async () => {
    // 没给 vi 挂资源：i18next 先走 fallbackLng(zh)，拿不到就原样回显 key——两种
    // 结局都必须被兜住，给用户的一定是可读品牌名。
    await i18n.changeLanguage("vi");

    expect(localizedBrandName("mediakit")).toBe("火山 AI MediaKit");
    expect(providerChannelLabel("mediakit")).toBe("火山 AI MediaKit");
    expect(localizedBrandDescription("mediakit")).toContain("MediaKit");
  });

  it("空 / 垃圾 provider 不抛错", () => {
    expect(localizedBrandName("")).toBe("");
    expect(providerChannelLabel("")).toBe("");
    expect(providerChannelLabel("  ")).toBe("");
  });
});

// [aipdd fork delta] 渠道官网直达契约：设置面板渠道名旁的链接只认 PROVIDER_OFFICIAL_SITES
// 这一张表。未登记的渠道必须落回空串（调用方据此不渲染链接）——宁可不给入口，也不能
// 猜一个域名把用户带到别的地方去。
describe("providerOfficialSiteUrl", () => {
  it("四家已确认厂商给出官网地址（MiniMax 双站各一条）", () => {
    expect(providerOfficialSiteUrl("aipdd")).toBe("https://www.aipdd.work");
    expect(providerOfficialSiteUrl("volcengine")).toBe(
      "https://www.volcengine.com/",
    );
    expect(providerOfficialSiteUrl("minimax")).toBe("https://www.minimax.io/");
    expect(providerOfficialSiteUrl("minimax_cn")).toBe(
      "https://www.minimax.cn/",
    );
  });

  it("大小写 / 首尾空白不影响命中", () => {
    expect(providerOfficialSiteUrl(" AIPDD ")).toBe("https://www.aipdd.work");
    expect(providerOfficialSiteUrl("MiniMax_CN")).toBe(
      "https://www.minimax.cn/",
    );
  });

  it("未登记渠道与空值返回空串（不猜 URL）", () => {
    expect(providerOfficialSiteUrl("newapi")).toBe("");
    expect(providerOfficialSiteUrl("comfyui")).toBe("");
    // MediaKit 归在 VolcEngine 卡内，自己不占官网链接行。
    expect(providerOfficialSiteUrl("mediakit")).toBe("");
    expect(providerOfficialSiteUrl("")).toBe("");
    expect(providerOfficialSiteUrl("  ")).toBe("");
  });
});