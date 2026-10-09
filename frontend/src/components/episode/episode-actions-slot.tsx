// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/**
 * Shared "right-rail" slot for the episode chrome — lets a route child (e.g.
 * the beats workbench) project its episode-level batch actions into the
 * header's right column without the parent having to know which route is
 * active or own any of its mutations/state.
 *
 * Flow:
 *   • `EpisodeActionsSlotProvider` owns the target DIV and exposes it via
 *     context.
 *   • Children call `useEpisodeActionsSlot()` to get a render target, then
 *     use `createPortal` to mount their actions there.
 *   • Children also call `useRegisterEpisodeActionsSlot()` to claim the
 *     slot for their lifetime, so the header layout can collapse the right
 *     column entirely on routes that don't use it.
 */
interface SlotContextValue {
  target: HTMLDivElement | null;
  setTarget: (el: HTMLDivElement | null) => void;
  active: boolean;
  register: () => () => void;
}

const EpisodeActionsSlotContext = createContext<SlotContextValue | null>(null);

export function EpisodeActionsSlotProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [count, setCount] = useState(0);
  const register = useCallback(() => {
    setCount((c) => c + 1);
    return () => setCount((c) => c - 1);
  }, []);
  const value = useMemo(
    () => ({ target, setTarget, active: count > 0, register }),
    [count, register, target],
  );

  return (
    <EpisodeActionsSlotContext.Provider value={value}>
      {children}
    </EpisodeActionsSlotContext.Provider>
  );
}

export function useEpisodeActionsSlot(): HTMLDivElement | null {
  return useContext(EpisodeActionsSlotContext)?.target ?? null;
}

export function useEpisodeActionsSlotSetter(): (el: HTMLDivElement | null) => void {
  const ctx = useContext(EpisodeActionsSlotContext);
  if (!ctx) throw new Error("useEpisodeActionsSlotSetter must be used inside EpisodeActionsSlotProvider");
  return ctx.setTarget;
}

/**
 * Whether any route child is currently rendering into the slot. The header
 * layout reads this to decide between 1-column (slot empty) and 2-column
 * (slot active) grid templates.
 */
export function useEpisodeActionsSlotActive(): boolean {
  return useContext(EpisodeActionsSlotContext)?.active ?? false;
}

/**
 * Claim the slot for this component's lifetime. Call from the same component
 * that renders the portal so the "active" flag tracks render presence.
 */
export function useRegisterEpisodeActionsSlot(active = true): void {
  const ctx = useContext(EpisodeActionsSlotContext);
  const register = ctx?.register;
  useEffect(() => {
    if (!register || !active) return;
    return register();
  }, [active, register]);
}
