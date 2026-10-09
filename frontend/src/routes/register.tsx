// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, redirect } from "@tanstack/react-router";
import { ensureAuthenticatedForAppRoute } from "@/lib/auth-mode";

export const Route = createFileRoute("/register")({
  beforeLoad: async () => {
    if (await ensureAuthenticatedForAppRoute()) {
      throw redirect({ to: "/" as const, replace: true });
    }
    throw redirect({ to: "/login" as const, search: { mode: "register" }, replace: true });
  },
});