// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * List-shape skeletons, colocated so that row shapes evolve together with
 * their real counterparts. Each component renders an `aria-busy` wrapper
 * with `role="status"` so assistive tech announces "Loading list" once,
 * not per-row.
 */

interface BusyListProps {
  label: string;
  className?: string;
  children: React.ReactNode;
}

function BusyList({ label, className, children }: BusyListProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      className={cn(className)}
    >
      {children}
    </div>
  );
}

// ─── Episodes grid (4-col card grid mimicking the real list) ────────────────
export function EpisodeListSkeleton({ label }: { label: string }) {
  return (
    <BusyList
      label={label}
      className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4"
    >
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3"
        >
          <Skeleton className="aspect-[16/10] w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </BusyList>
  );
}

// ─── Tasks (vertical rows with status pill + metadata) ──────────────────────
export function TaskListSkeleton({ label }: { label: string }) {
  return (
    <BusyList label={label} className="space-y-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-md border border-border bg-card p-3"
        >
          <Skeleton className="size-2 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-1/2" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="h-5 w-16" />
        </div>
      ))}
    </BusyList>
  );
}

// ─── Styles / Characters sidebar list (compact rows with avatar + title) ────
export function SidebarListSkeleton({
  label,
  rows = 6,
}: {
  label: string;
  rows?: number;
}) {
  return (
    <BusyList label={label} className="flex flex-col gap-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-2 rounded-md border border-border bg-card p-2"
        >
          <Skeleton className="size-8 shrink-0 rounded" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </BusyList>
  );
}

// ─── Detail pane skeleton (header + paragraph + grid of tiles) ──────────────
export function DetailPaneSkeleton({ label }: { label: string }) {
  return (
    <BusyList label={label} className="space-y-6 p-6">
      <div className="space-y-2">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-3.5 w-2/3" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="aspect-square w-full" />
        ))}
      </div>
      <div className="space-y-1.5">
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-5/6" />
        <Skeleton className="h-3.5 w-4/6" />
      </div>
    </BusyList>
  );
}
