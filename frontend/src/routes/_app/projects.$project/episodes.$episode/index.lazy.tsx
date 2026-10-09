// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute, Navigate } from "@tanstack/react-router";

// Bare /episodes/$episode/ → /episodes/$episode/script
//
// 脚本是第一站，裸链接默认进「脚本」。
//
// Uses <Navigate /> rather than a `loader`/`beforeLoad` because `createLazyFileRoute`
// on the installed TanStack Router only accepts component/errorComponent/pendingComponent/
// notFoundComponent — loaders belong on the non-lazy side of a file route.
function EpisodeIndexRedirect() {
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
  "/_app/projects/$project/episodes/$episode/",
)({
  component: EpisodeIndexRedirect,
});
