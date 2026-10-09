// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 五官垫图（身份卡 1:1 画布 4×4 网格设定表）与「三视图与五官垫图」拼接前端契约
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function loadIdentitiesLocale(language: string): Record<string, string> {
  const bundle = JSON.parse(
    readFileSync(`public/locales/${language}/translation.json`, "utf8"),
  ) as { characters: { identities: Record<string, string> } };
  return bundle.characters.identities;
}

const routeSource = readFileSync(
  resolve(process.cwd(), "src/routes/_app/projects.$project/characters.lazy.tsx"),
  "utf8",
);
const queriesSource = readFileSync(
  resolve(process.cwd(), "src/lib/queries/characters.ts"),
  "utf8",
);
const typeSource = readFileSync(
  resolve(process.cwd(), "src/types/character.ts"),
  "utf8",
);

const locales = ["zh", "en", "vi"] as const;

describe("identity face mat (五官垫图) contract", () => {
  it("dispatches the async task through the face-mat route", () => {
    expect(queriesSource).toContain("export function useGenerateIdentityFaceMatAsync");
    expect(queriesSource).toContain(
      "identities/${identityId}/face-mat/generate-async",
    );
  });

  it("exposes the persisted face mat slot on the identity type", () => {
    expect(typeSource).toContain("face_mat_image_path?: string | null;");
    expect(typeSource).toContain("face_mat_image_url?: string | null;");
  });

  it("puts the button block above 参考输入与变体 and wires the task stream", () => {
    const faceMatBlock = routeSource.indexOf("{/* [aipdd fork delta] 五官垫图");
    const refsBlock = routeSource.indexOf('{t("characters.identities.refsTitle")}');

    expect(faceMatBlock).toBeGreaterThan(-1);
    expect(refsBlock).toBeGreaterThan(-1);
    expect(faceMatBlock).toBeLessThan(refsBlock);

    expect(routeSource).toContain(
      "const genFaceMat = useGenerateIdentityFaceMatAsync(project, characterName);",
    );
    expect(routeSource).toContain(
      "const identityFaceMatScope = `character:${characterName}:identity_face_mat:${identity.identity_name}`;",
    );
    expect(routeSource).toContain("identityFaceMatTask.start({ scope: res.scope, taskId: res.task_id });");
    expect(routeSource).toContain("src={resolveMediaUrl(identity.face_mat_image_url) ?? \"\"}");
    // 已有五官垫图时按钮文案切成「重新生成」
    expect(routeSource).toContain('t("characters.identities.faceMatRegenerate")');
  });

  it("does not pollute the identity generation attempt counter", () => {
    const handler = routeSource.slice(
      routeSource.indexOf("const handleGenFaceMat = async () => {"),
      routeSource.indexOf("const handleGenFaceMat = async () => {") + 900,
    );

    expect(handler).toContain("genFaceMat.mutateAsync");
    expect(handler).not.toContain("bumpAttempt");
  });

  it("ships the three locale keys in every language", () => {
    for (const lang of locales) {
      const identities = loadIdentitiesLocale(lang);

      for (const key of ["faceMat", "faceMatRegenerate", "faceMatHint"]) {
        expect(identities[key], `${lang}.${key}`).toBeTruthy();
        expect(typeof identities[key], `${lang}.${key}`).toBe("string");
      }
    }
  });
});

describe("identity three-view + face mat stitch (三视图与五官垫图) contract", () => {
  it("calls the synchronous stitch route and refreshes the identity list", () => {
    expect(queriesSource).toContain("export function useStitchIdentityComposite");
    expect(queriesSource).toContain(
      "identities/${identityId}/composite/stitch",
    );
    expect(queriesSource).toContain(
      "qc.invalidateQueries({ queryKey: queryKeys.identities(project, name) });",
    );
  });

  it("exposes the composite slot on the identity type", () => {
    expect(typeSource).toContain("composite_image_path?: string | null;");
    expect(typeSource).toContain("composite_image_url?: string | null;");
  });

  it("sits below the face mat block and stitches on thumbnail click", () => {
    const faceMatBlock = routeSource.indexOf("{/* [aipdd fork delta] 五官垫图");
    const compositeBlock = routeSource.indexOf(
      "{/* [aipdd fork delta] 三视图与五官垫图",
    );
    const refsBlock = routeSource.indexOf('{t("characters.identities.refsTitle")}');

    expect(faceMatBlock).toBeGreaterThan(-1);
    expect(compositeBlock).toBeGreaterThan(faceMatBlock);
    expect(compositeBlock).toBeLessThan(refsBlock);

    const block = routeSource.slice(compositeBlock, refsBlock);
    // 点缩略图本身就是拼接入口
    expect(block).toContain("onClick={handleStitchComposite}");
    expect(block).toContain('src={resolveMediaUrl(identity.composite_image_url) ?? ""}');
    // 两张源图齐备才允许拼
    expect(routeSource).toContain(
      "!!identity.image_url && !!identity.face_mat_image_url",
    );
    // 本地合成：不占任务队列、不报积分
    expect(block).not.toContain("Task.start");
    expect(block).not.toContain("CreditCostInline");
  });

  it("keeps the stitch handler out of the task stream and the attempt counter", () => {
    const start = routeSource.indexOf("const handleStitchComposite = async () => {");
    const handler = routeSource.slice(start, start + 700);

    expect(start).toBeGreaterThan(-1);
    expect(handler).toContain("stitchComposite.mutateAsync(identity.identity_id)");
    expect(handler).not.toContain("bumpAttempt");
    expect(handler).not.toContain("Task.start");
  });

  it("ships the composite locale keys in every language", () => {
    for (const lang of locales) {
      const identities = loadIdentitiesLocale(lang);

      for (const key of [
        "compositeTitle",
        "composite",
        "compositeRegenerate",
        "compositeHint",
        "compositeNeedsSources",
        "compositeDone",
      ]) {
        expect(identities[key], `${lang}.${key}`).toBeTruthy();
        expect(typeof identities[key], `${lang}.${key}`).toBe("string");
      }
    }
  });
});
