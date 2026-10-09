// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { cn } from "@/lib/utils";

/**
 * Layout-shape placeholder rendered while async content loads. Unlike
 * spinners, skeletons preserve the page's eventual geometry so content
 * swap-in feels instant rather than reflowy.
 *
 * Usage: compose multiple Skeletons to mimic a row/card outline. Don't
 * animate individual pieces — the single pulse on the root container
 * is enough (matches Linear/Vercel/shadcn defaults).
 *
 * Accessibility: announced as a busy region via aria-busy="true" on
 * the list wrapper that owns the skeletons — not per-skeleton.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
