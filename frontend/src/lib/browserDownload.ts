// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Pure-web download helper: fetch the URL into a blob, create an object URL,
// and trigger a
// `<a download>` click. If the fetch fails (e.g. cross-origin without CORS),
// fall back to a plain anchor pointing at the original URL with `target=_blank`
// — the browser will at least open it so the user can save manually.

function inferFilenameFromUrl(url: string): string | null {
  try {
    const u = new URL(url, window.location.origin);
    const last = u.pathname.split('/').filter(Boolean).pop();
    return last ?? null;
  } catch {
    const last = url.split('?')[0]?.split('/').filter(Boolean).pop() ?? null;
    return last;
  }
}

/**
 * Trigger a download for an in-memory Blob (e.g. a client-side transcoded audio
 * file). Wraps it in an object URL, clicks a hidden `<a download>`, then revokes.
 */
export function downloadBlobAsFile(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename || 'download';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
}

export async function downloadUrlAsFile(
  url: string,
  suggestedFilename?: string,
): Promise<void> {
  if (!url) {
    return;
  }
  const filename = suggestedFilename || inferFilenameFromUrl(url) || 'download';

  let objectUrl: string | null = null;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      throw new Error(`fetch failed: ${resp.status}`);
    }
    const blob = await resp.blob();
    objectUrl = URL.createObjectURL(blob);
  } catch (err) {
    console.warn('[browserDownload] blob fetch failed, falling back to direct href', err);
  }

  const anchor = document.createElement('a');
  anchor.href = objectUrl ?? url;
  anchor.download = filename;
  if (!objectUrl) {
    // Cross-origin without CORS: best we can do is open the URL so the user
    // can right-click → "Save As" manually. `download` is ignored by browsers
    // when the response is opaque, so target=_blank is the polite fallback.
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
  }
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);

  if (objectUrl) {
    // Defer revoke so the browser has time to consume the URL.
    const toRevoke = objectUrl;
    setTimeout(() => URL.revokeObjectURL(toRevoke), 5000);
  }
}
