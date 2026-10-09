import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const loginCss = readFileSync("src/components/login/login.module.css", "utf8");
const heroSource = readFileSync(
  "src/components/login/cinematic/LoginCinematicHero.tsx",
  "utf8",
);
const stageSource = readFileSync("src/components/login/login-stage.tsx", "utf8");
const moreInfoSource = readFileSync(
  "src/components/login/cinematic/MoreInfoMenu.tsx",
  "utf8",
);

function rule(selector: string): string {
  const match = loginCss.match(new RegExp(`\\.${selector}\\s*\\{([\\s\\S]*?)\\}`));
  expect(match, `missing .${selector} rule`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("login header popover backdrop contract", () => {
  it("does not isolate resting popovers behind a zero-value filter root", () => {
    const headerRule = rule("stageTopBar");
    expect(headerRule).toContain("filter: none");
    expect(headerRule).not.toMatch(/(?:^|\n)\s*(?:-webkit-)?backdrop-filter\s*:/);
    expect(headerRule).not.toMatch(/filter:\s*blur\(0/);
  });

  it("keeps the compact header actions visually grouped", () => {
    expect(rule("stageActions")).toContain("gap: 24px");
  });

  it("keeps backdrop blur on both business and more-info surfaces", () => {
    expect(rule("businessWechatPanel")).toMatch(/backdrop-filter:\s*blur\(22px\)/);
    expect(loginCss).toMatch(
      /\.moreInfoMenu,\s*\.moreInfoContent\s*\{[\s\S]*?border:\s*1px solid rgba\(255, 255, 255, 0\.2\);[\s\S]*?background:\s*rgba\(32, 34, 38, 0\.38\);[\s\S]*?backdrop-filter:\s*blur\(22px\) saturate\(135%\)/,
    );
  });

  it("animates each glass surface on its own pre-promoted compositor layer", () => {
    const businessPopover = rule("businessWechatPopover");
    const moreInfoPopover = rule("moreInfoPopover");
    const moreInfoMenu = rule("moreInfoMenu");

    expect(businessPopover).not.toMatch(/(?:opacity|visibility)\s*:/);
    expect(moreInfoPopover).not.toMatch(/(?:opacity|visibility)\s*:/);
    expect(loginCss).toMatch(
      /\.businessWechatPopover > \.businessWechatPanel\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?will-change:\s*opacity, transform/,
    );
    expect(rule("businessWechatPanel")).not.toContain("opacity: 0");
    expect(moreInfoMenu).toContain("will-change: opacity, transform");
    expect(moreInfoMenu).toContain("opacity: 0");
    // [aipdd fork delta] The cinematic header no longer renders the business
    // popover, so the hero has no businessOpen-gated surface left.
    expect(heroSource).not.toContain("businessOpen");
    expect(moreInfoSource).toContain("aria-hidden={!menuOpen}");
    expect(moreInfoSource).toContain("inert={!menuOpen}");
  });

  it("uses a compact content-sized information menu and a semantic icon", () => {
    expect(moreInfoSource).toContain("<BookOpenText aria-hidden=\"true\" />");
    expect(moreInfoSource).not.toContain("MoreHorizontal");
    expect(rule("moreInfoPopover")).toContain("width: max-content");
    expect(rule("moreInfoMenu")).toContain("min-width: 124px");
    expect(rule("moreInfoMenu")).not.toContain("scrollbar-gutter");
    expect(moreInfoSource).not.toContain("SHOW_DEVELOPMENT_PREVIEW");
  });

  it("keeps the compact business-contact treatment on the login-stage header", () => {
    // [aipdd fork delta] The cinematic header dropped the business/announcement/
    // stars entries; the login-stage variant keeps its business entry untouched.
    expect(stageSource).toContain("<MessageSquareQuote aria-hidden=\"true\" />");
    expect(stageSource).toContain('t("auth.businessWechat.shortLabel")');
    expect(stageSource).not.toContain("MessageCircle");
    expect(heroSource).not.toContain("businessWechat");
    expect(heroSource).not.toContain("AnnouncementEntry");
    expect(heroSource).not.toContain("GithubMark");
  });

  it("points the hero learn-more link at the AIPDD wiki in a new tab", () => {
    expect(heroSource).toContain(
      "https://aiworkin.feishu.cn/wiki/QjAfwFtYbiqSAukMvU6cg5Kan2f?from=from_copylink",
    );
    expect(heroSource).toMatch(/href=\{LEARN_MORE_URL\}[\s\S]*?target="_blank"/);
  });

  it("applies header blur only while the cinematic header exits", () => {
    expect(heroSource).toContain("filter: `blur(${heroExitProgress * 8}px)`");
  });
});
