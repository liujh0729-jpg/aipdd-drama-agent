// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute, Navigate } from "@tanstack/react-router";

// Legacy redirect — overview content moved to the script tab's SourcePanel.
// File kept so existing /overview bookmarks still match a route and hop to /script.
//
// Uses <Navigate /> rather than a `loader`/`beforeLoad` because `createLazyFileRoute`
// on the installed TanStack Router only accepts component/errorComponent/pendingComponent/
// notFoundComponent — loaders belong on the non-lazy side of a file route.
function OverviewRedirect() {
  const { project, episode } = Route.useParams();
  return (
    <Navigate
      to="/projects/$project/episodes/$episode/script"
      params={{ project, episode }}
      replace
    />
  );
}

export const Route = createLazyFileRoute(
  "/_app/projects/$project/episodes/$episode/overview",
)({
  component: OverviewRedirect,
});
