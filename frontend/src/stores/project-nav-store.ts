// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";

import type { ProjectSection } from "@/components/layout/project-navigation-routes";

/**
 * 记住每个项目的导航位置：
 * - lastSectionByProject：项目内最后停留的区块（画布 freezone 或剧集各子页），
 *   进入项目时恢复到这里，而不是固定落到画布。
 * - lastXiajiSectionByProject：最后停留的剧集子页（故事/剧本/资产/分镜/虾导/风格），
 *   顶部切到「剧集」时恢复到这里，而不是固定落到故事。
 */

/** 可被记忆的区块：画布 + 剧集六个子页（tasks 等其它路由不参与记忆）。 */
const REMEMBERED_SECTIONS = new Set<ProjectSection>([
  "freezone",
  "story",
  "ingest",
  "characters",
  "episodes",
  "assistant",
  "styles",
]);

export type XiajiSection = Exclude<
  ProjectSection,
  "freezone" | "tasks"
>;

export function isRememberedSection(
  section: ProjectSection | null,
): section is ProjectSection {
  return section !== null && REMEMBERED_SECTIONS.has(section);
}

interface ProjectNavState {
  lastSectionByProject: Record<string, ProjectSection>;
  lastXiajiSectionByProject: Record<string, XiajiSection>;
  rememberSection: (project: string, section: ProjectSection) => void;
  reset: () => void;
}

export const useProjectNavStore = create<ProjectNavState>()(
  persist(
    (set) => ({
      lastSectionByProject: {},
      lastXiajiSectionByProject: {},
      rememberSection: (project, section) =>
        set((state) => {
          if (!project || !REMEMBERED_SECTIONS.has(section)) return state;
          const next: Partial<ProjectNavState> = {
            lastSectionByProject: {
              ...state.lastSectionByProject,
              [project]: section,
            },
          };
          if (section !== "freezone") {
            next.lastXiajiSectionByProject = {
              ...state.lastXiajiSectionByProject,
              [project]: section as XiajiSection,
            };
          }
          return next as ProjectNavState;
        }),
      reset: () =>
        set({ lastSectionByProject: {}, lastXiajiSectionByProject: {} }),
    }),
    {
      name: "supertale-project-nav",
      version: 1,
      storage: createJSONStorage(() => quotaSafeStateStorage),
    },
  ),
);
