// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import { ListChecks } from "lucide-react";

export function EmptyState({ variant = "all" }: { variant?: "all" | "filter" }) {
  const { t } = useTranslation();
  const label = t(variant === "all" ? "taskCenter.panel.empty" : "taskCenter.panel.emptyFiltered");
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2.5 text-muted-foreground">
      <span className="flex size-12 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.035] text-muted-foreground/75">
        <ListChecks className="size-5 stroke-[1.7]" />
      </span>
      <p className="text-sm font-normal leading-relaxed tracking-normal text-muted-foreground">
        {label}
      </p>
    </div>
  );
}
