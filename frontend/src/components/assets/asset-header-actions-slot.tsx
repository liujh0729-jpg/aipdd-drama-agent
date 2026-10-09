// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  createContext,
  useContext,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { createPortal } from "react-dom";

const SlotContext = createContext<HTMLElement | null>(null);
const SlotSetterContext = createContext<Dispatch<SetStateAction<HTMLElement | null>>>(
  () => {},
);

export function AssetHeaderActionsSlotProvider({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <SlotContext.Provider value={slot}>
      <SlotSetterContext.Provider value={setSlot}>
        {children}
      </SlotSetterContext.Provider>
    </SlotContext.Provider>
  );
}

export function AssetHeaderActionsTarget({ className }: { className?: string }) {
  const setSlot = useContext(SlotSetterContext);
  return <div ref={setSlot} className={className} />;
}

export function AssetHeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(SlotContext);
  if (!slot) return null;
  return createPortal(children, slot);
}
