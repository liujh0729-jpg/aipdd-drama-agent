// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export type DirectorWorldSceneSaveHandler = () => Promise<void>;

const handlers = new Map<string, DirectorWorldSceneSaveHandler>();

export function setDirectorWorldSceneSaveHandler(
  nodeId: string,
  handler: DirectorWorldSceneSaveHandler | null,
): void {
  if (!handler) {
    handlers.delete(nodeId);
    return;
  }
  handlers.set(nodeId, handler);
}

export async function saveOpenDirectorWorldScene(nodeId: string): Promise<boolean> {
  const handler = handlers.get(nodeId);
  if (!handler) return false;
  await handler();
  return true;
}
