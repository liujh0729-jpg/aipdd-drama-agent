// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useAuthStore } from "@/stores/auth-store";
import { authRequired } from "@/lib/runtime-config";

export type AuthMode = "cookie" | "local";

export function authMode(): AuthMode {
  return import.meta.env.VITE_AUTH_MODE === "local" ? "local" : "cookie";
}

export function isLocalAuthMode(): boolean {
  return authMode() === "local";
}

export async function ensureAuthenticatedForAppRoute(): Promise<boolean> {
  const auth = useAuthStore.getState();
  if (auth.username) return true;
  if (authRequired()) return false;
  return Boolean(await auth.getCurrentUser());
}
