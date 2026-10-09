// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createRoot, type Root } from "react-dom/client";

const REACT_ROOT_KEY = Symbol.for("supertale.reactRoot");

type ReactRootContainer = (Element | DocumentFragment) & {
  [REACT_ROOT_KEY]?: Root;
};

export function getOrCreateReactRoot(container: Element | DocumentFragment): Root {
  const rootContainer = container as ReactRootContainer;
  const existing = rootContainer[REACT_ROOT_KEY];
  if (existing) return existing;
  const root = createRoot(container);
  rootContainer[REACT_ROOT_KEY] = root;
  return root;
}
