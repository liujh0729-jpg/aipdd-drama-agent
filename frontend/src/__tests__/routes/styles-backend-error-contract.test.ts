// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const routeSource = readFileSync(
  "src/routes/_app/projects.$project/styles.tsx",
  "utf8",
);
const querySource = readFileSync("src/lib/queries/styles.ts", "utf8");

// [aipdd fork delta] WP-D: 积分估价已拆除，这里只保留后端错误类型化契约。
describe("style analysis backend error contract", () => {
  it("uses typed backend errors for upstream quota and missing rules", () => {
    expect(querySource).toContain("jsonWithBackendError");
    expect(querySource).toContain("throwHttpErrors: false");
    expect(routeSource).toContain("backendErrorToastMessage(error, t)");
  });
});
