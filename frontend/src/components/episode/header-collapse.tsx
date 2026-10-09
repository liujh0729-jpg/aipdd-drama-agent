// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

interface HeaderCollapseValue {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
}

const HeaderCollapseContext = createContext<HeaderCollapseValue | null>(null);

export function HeaderCollapseProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const value = useMemo(() => ({ collapsed, setCollapsed }), [collapsed]);
  return (
    <HeaderCollapseContext.Provider value={value}>
      {children}
    </HeaderCollapseContext.Provider>
  );
}

export function useHeaderCollapsed(): boolean {
  return useContext(HeaderCollapseContext)?.collapsed ?? false;
}

// Wraps a chrome region that should slide away when the header is collapsed.
// Animates max-height to the content's measured height (tracked via a
// ResizeObserver) so it works for variable-height content without a hard-coded
// max-height that could clip or lag.
export function CollapsibleHeaderRegion({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const collapsed = useHeaderCollapsed();
  const innerRef = useRef<HTMLDivElement>(null);
  const [contentHeight, setContentHeight] = useState<number>();

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const measure = () => setContentHeight(el.scrollHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      className={cn(
        "shrink-0 overflow-hidden transition-all duration-300 ease-in-out",
        className,
      )}
      style={{
        maxHeight: collapsed ? 0 : (contentHeight ?? undefined),
        opacity: collapsed ? 0 : 1,
      }}
    >
      <div ref={innerRef}>{children}</div>
    </div>
  );
}

// Returns a ref to attach to a scroll-container subtree. Drives the header
// collapse from `wheel` direction (capture phase, so it catches wheels over any
// nested overflow region). Wheel intent is used instead of scrollTop deltas
// because collapsing the chrome resizes the scroll panes and clamps their
// scrollTop — a scroll-delta scheme reads that reflow as a phantom scroll and
// fights itself. Wheel deltaY is the user's actual intent and is reflow-immune.
export function useHideHeaderOnScroll<T extends HTMLElement>() {
  const ctx = useContext(HeaderCollapseContext);
  const setCollapsed = ctx?.setCollapsed;
  const ref = useRef<T>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root || !setCollapsed) return;

    const WHEEL_THRESHOLD = 4;
    // Collapsing animates the chrome's max-height to 0, which grows the scroll
    // panes; that reflow clamps a pane's scrollTop back to 0 and fires phantom
    // `scroll` events throughout the animation. The reveal-on-top handler below
    // must not read those as "user reached the top" or the header bounces right
    // back (esp. with a short right pane, e.g. only 文案 expanded). We suppress
    // reveal-on-top while the collapse settles — but keyed off the reflow scrolls
    // themselves: each one pushes the window out, so it tracks the *actual*
    // animation rather than a guessed duration (robust to transition-length
    // changes and frame jank), and expires shortly after the last reflow scroll.
    // KICK covers the gap from collapse to the first reflow scroll; GRACE keeps
    // it alive between subsequent ones. Genuine wheel-up still reveals via onWheel.
    const COLLAPSE_KICK_MS = 200;
    const COLLAPSE_GRACE_MS = 160;
    let collapsed = false;
    let suppressRevealUntil = 0;
    const set = (next: boolean) => {
      if (next === collapsed) return;
      collapsed = next;
      if (next) suppressRevealUntil = Date.now() + COLLAPSE_KICK_MS;
      setCollapsed(next);
    };

    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) < WHEEL_THRESHOLD) return;
      set(event.deltaY > 0);
    };
    // Reaching the very top reveals the header (covers scrollbar drag and keyboard
    // scrolling that wheel events don't see). During the collapse-settle window a
    // scrollTop of 0 is a reflow artifact, not a real top-reach: ignore it and
    // push the window out so we keep covering the rest of the animation.
    const onScroll = (event: Event) => {
      const now = Date.now();
      if (now < suppressRevealUntil) {
        suppressRevealUntil = now + COLLAPSE_GRACE_MS;
        return;
      }
      const target = event.target;
      if (target instanceof HTMLElement && target.scrollTop <= 0) set(false);
    };

    root.addEventListener("wheel", onWheel, { capture: true, passive: true });
    root.addEventListener("scroll", onScroll, true);
    return () => {
      root.removeEventListener("wheel", onWheel, true);
      root.removeEventListener("scroll", onScroll, true);
    };
  }, [setCollapsed]);

  // Always reveal the header again when the consuming tab unmounts.
  useEffect(() => () => setCollapsed?.(false), [setCollapsed]);

  return ref;
}
