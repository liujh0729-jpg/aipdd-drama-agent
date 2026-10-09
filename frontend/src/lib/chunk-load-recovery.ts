// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useSyncExternalStore } from "react";

type RecoveryState = "idle" | "reload-required";
type RecoveryResult = "ignored" | "needs-user-reload";

type Listener = () => void;

let recoveryState: RecoveryState = "idle";
const listeners = new Set<Listener>();
let installed = false;

function getErrorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (error && typeof error === "object") {
    const record = error as Record<string, unknown>;
    const message = typeof record.message === "string" ? record.message : "";
    const name = typeof record.name === "string" ? record.name : "";
    return `${name} ${message}`.trim();
  }
  return "";
}

export function isChunkLoadError(error: unknown): boolean {
  const message = getErrorMessage(error);
  return /Failed to fetch dynamically imported module/i.test(message)
    || /Importing a module script failed/i.test(message)
    || /error loading dynamically imported module/i.test(message)
    || /ChunkLoadError/i.test(message);
}

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

function requireUserReload(): void {
  if (recoveryState === "reload-required") return;
  recoveryState = "reload-required";
  notify();
}

export function requestChunkLoadRecovery(error: unknown): RecoveryResult {
  if (!isChunkLoadError(error)) return "ignored";

  requireUserReload();
  return "needs-user-reload";
}

export function installChunkLoadRecovery(): () => void {
  if (installed || typeof window === "undefined") return () => undefined;
  installed = true;

  const handlePreloadError = (event: Event) => {
    const payload = (event as Event & { payload?: unknown }).payload;
    if (!isChunkLoadError(payload)) return;
    event.preventDefault();
    requestChunkLoadRecovery(payload);
  };

  const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
    if (!isChunkLoadError(event.reason)) return;
    event.preventDefault();
    requestChunkLoadRecovery(event.reason);
  };

  window.addEventListener("vite:preloadError", handlePreloadError);
  window.addEventListener("unhandledrejection", handleUnhandledRejection);

  return () => {
    installed = false;
    window.removeEventListener("vite:preloadError", handlePreloadError);
    window.removeEventListener("unhandledrejection", handleUnhandledRejection);
  };
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): boolean {
  return recoveryState === "reload-required";
}

export function useChunkLoadRecoveryRequired(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function resetChunkLoadRecoveryForTests(): void {
  recoveryState = "idle";
  installed = false;
  listeners.clear();
}
