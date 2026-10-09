// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Tagged-template for building API paths with every interpolated segment
 * percent-encoded. Use in place of raw template strings so that user-supplied
 * identifiers (project names, character names, episode numbers, etc.) cannot
 * alter the path structure — e.g. `p\`api/v1/projects/${project}\`` when
 * `project = "foo/../admin"` becomes `api/v1/projects/foo%2F..%2Fadmin`.
 *
 * Numbers and booleans are coerced via String(); null/undefined become "".
 */
export function p(
  strings: TemplateStringsArray,
  ...values: ReadonlyArray<string | number | boolean | null | undefined>
): string {
  let out = "";
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) {
      const v = values[i];
      if (v === null || v === undefined) return;
      out += encodeURIComponent(String(v));
    }
  });
  return out;
}
