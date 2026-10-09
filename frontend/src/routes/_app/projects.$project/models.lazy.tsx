// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] WP-E: 顶部「模型广场」标签的路由壳，与「MCP」同为项目内页。
import { createLazyFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ModelPlazaPage } from "@/features/models/ModelPlazaPage";
import { useAllProjectSummaries } from "@/lib/queries/projects";

function ModelsProjectRoute() {
  const { t } = useTranslation();
  const { project } = Route.useParams();
  const { data: projects, isLoading } = useAllProjectSummaries();

  if (isLoading || !projects) {
    return (
      <div className="flex h-[calc(100%+3rem)] -m-6 items-center justify-center bg-background text-muted-foreground">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  const matched =
    projects.find((item) => item.id === project) ??
    projects.find((item) => item.name === project) ??
    null;

  if (!matched) {
    return (
      <div className="-m-6 flex h-[calc(100%+3rem)] items-center justify-center bg-background">
        <div className="max-w-md rounded-2xl border border-white/[0.06] bg-white/[0.025] px-6 py-8 text-center">
          <div className="mb-2 text-base font-medium text-foreground">{t("project.notFound")}</div>
          <div className="mb-6 text-sm text-muted-foreground">
            <code className="rounded bg-white/[0.05] px-1 py-0.5">{project}</code>
          </div>
        </div>
      </div>
    );
  }

  return <ModelPlazaPage />;
}

export const Route = createLazyFileRoute("/_app/projects/$project/models")({
  component: ModelsProjectRoute,
});
