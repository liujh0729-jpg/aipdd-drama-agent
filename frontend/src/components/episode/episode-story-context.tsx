// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EpisodeStoryContextLabels {
  summary: string;
  noSummary: string;
  keyEvents: string;
  noKeyEvents: string;
  cliffhanger: string;
  noCliffhanger: string;
}

interface EpisodeStoryContextProps {
  contentSummary?: string | null;
  keyEvents?: string[] | null;
  cliffhanger?: string | null;
  labels: EpisodeStoryContextLabels;
  className?: string;
}

export function EpisodeStoryContext({
  contentSummary,
  keyEvents,
  cliffhanger,
  labels,
  className,
}: EpisodeStoryContextProps) {
  const summary = contentSummary?.trim() ?? "";
  const events = (keyEvents ?? []).map((item) => item.trim()).filter(Boolean);
  const hook = cliffhanger?.trim() ?? "";

  return (
    <section
      className={cn(
        "border-y border-border/20 px-5 py-5",
        className,
      )}
    >
      <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr_1fr]">
        <StoryBlock title={labels.summary}>
          {summary ? (
            <p className="whitespace-pre-wrap text-foreground/85">{summary}</p>
          ) : (
            <p className="italic text-muted-foreground/60">{labels.noSummary}</p>
          )}
        </StoryBlock>

        <StoryBlock title={labels.keyEvents}>
          {events.length > 0 ? (
            <ul className="space-y-1.5">
              {events.map((event, index) => (
                <li key={`${index}-${event}`} className="flex gap-2">
                  <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/70" />
                  <span className="text-foreground/85">{event}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="italic text-muted-foreground/60">{labels.noKeyEvents}</p>
          )}
        </StoryBlock>

        <StoryBlock title={labels.cliffhanger}>
          {hook ? (
            <p className="whitespace-pre-wrap text-foreground/85">{hook}</p>
          ) : (
            <p className="italic text-muted-foreground/60">
              {labels.noCliffhanger}
            </p>
          )}
        </StoryBlock>
      </div>
    </section>
  );
}

function StoryBlock({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <h2 className="mb-2 text-xs font-medium uppercase text-muted-foreground">
        {title}
      </h2>
      <div className="text-sm leading-relaxed">{children}</div>
    </div>
  );
}
