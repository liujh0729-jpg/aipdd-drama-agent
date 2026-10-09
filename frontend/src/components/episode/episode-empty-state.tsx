// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

interface EpisodeEmptyStateProps {
  icon: LucideIcon;
  title?: string;
  description?: string;
  className?: string;
}

export function EpisodeEmptyState({
  icon: Icon,
  title,
  description,
  className,
}: EpisodeEmptyStateProps) {
  return (
    <div
      className={cn(
        "flex h-full flex-col items-center justify-center p-6 text-center",
        className,
      )}
    >
      <div className="mb-3 flex size-12 items-center justify-center rounded-full border border-border bg-card">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      {title && (
        <h3 className="mb-1.5 text-sm font-semibold text-foreground">
          {title}
        </h3>
      )}
      {description && (
        <p className="max-w-[15rem] text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      )}
    </div>
  );
}
