// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute, Navigate } from "@tanstack/react-router";

// v3 redirect — the per-stage sketches page has been merged into /beats as
// the 草图 sub-tab. Kept so legacy bookmarks / inbound links continue to work.
function SketchesRedirect() {
  const { project, episode } = Route.useParams();
  return (
    <Navigate
      to="/projects/$project/episodes/$episode/beats"
      params={{ project, episode }}
      search={{ sub: "sketch" } as never}
      replace
    />
  );
}

export const Route = createLazyFileRoute(
  "/_app/projects/$project/episodes/$episode/sketches",
)({
  component: SketchesRedirect,
});
