// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
const COOKIE_NAME = "server-region";
const REGION_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

// One year. Long enough to survive between visits; short enough that
// decommissioned regions naturally self-expire.
const MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function secureAttr(): string {
  // Secure would block the cookie on http://localhost. Opt in by protocol.
  if (typeof window === "undefined") return "";
  return window.location.protocol === "https:" ? "; secure" : "";
}

export function getRegionCookie(): string | null {
  if (typeof document === "undefined") return null;
  const raw = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${COOKIE_NAME}=`));
  if (!raw) return null;
  let value: string;
  try {
    value = decodeURIComponent(raw.slice(COOKIE_NAME.length + 1));
  } catch {
    return null;
  }
  return REGION_ID_RE.test(value) ? value : null;
}

export function setRegionCookie(regionId: string): void {
  if (!REGION_ID_RE.test(regionId)) {
    throw new Error(`invalid region id: ${regionId}`);
  }
  if (typeof document === "undefined") return;
  document.cookie =
    `${COOKIE_NAME}=${encodeURIComponent(regionId)}` +
    `; path=/` +
    `; max-age=${MAX_AGE_SECONDS}` +
    `; samesite=lax` +
    secureAttr();
}

export function clearRegionCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; samesite=lax${secureAttr()}`;
}
