// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
const VERSION_UPDATE_EVENT = "supertale:version-update-dialog";

export function openVersionUpdateDialog() {
  window.dispatchEvent(new Event(VERSION_UPDATE_EVENT));
}

export function subscribeOpenVersionUpdateDialog(listener: () => void) {
  window.addEventListener(VERSION_UPDATE_EVENT, listener);
  return () => window.removeEventListener(VERSION_UPDATE_EVENT, listener);
}
