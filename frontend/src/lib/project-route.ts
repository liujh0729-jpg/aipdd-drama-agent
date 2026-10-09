// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
type ProjectRouteCandidate = {
  id: string;
  name: string;
};

export function canonicalProjectRouteParam(
  routeProject: string,
  projects: ProjectRouteCandidate[] | undefined,
): string | null {
  if (!routeProject || !projects) return routeProject;
  const decodedRouteProject = decodePathSegment(routeProject);

  const byId = projects.find((project) => project.id === decodedRouteProject);
  return byId?.id ?? null;
}

export function replaceProjectPathParam(pathname: string, nextProject: string): string {
  const parts = pathname.split("/");
  const index = parts.findIndex((part) => part === "projects");
  if (index < 0 || index + 1 >= parts.length || !parts[index + 1]) return pathname;
  parts[index + 1] = encodeURIComponent(nextProject);
  return parts.join("/");
}

function decodePathSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
