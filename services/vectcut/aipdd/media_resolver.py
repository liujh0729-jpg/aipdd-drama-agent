"""[AIPDD] 素材引用解析：把 Dramaclaw 的 ``/static`` 相对 URL 解析为本地文件路径。

VectCut 上游用 ffmpeg 直接消费 URL 或本地文件路径，而 Dramaclaw 的
``/static/projects/...`` 静态资源要求登录会话（ffmpeg 下载拿不到 cookie）。
当 VectCut 与 Dramaclaw 素材位于同一文件系统时（共享卷 / 本机部署），
把 URL 换算成本地绝对路径直读是最可靠的素材通道。

解析约定：
    /static/projects/{project}/{file_path}   →   {root}/{username}/{project_dir}/{file_path}

- ``roots`` 由调用方提供（服务端读 ``VECTCUT_MEDIA_ROOTS``），逐个尝试；
- ``project`` 在 URL 里通常是项目 id，磁盘目录通常是项目名，因此可传
  ``project_dir`` 提升唯一性；两者都会尝试；
- 两者都没命中时回退到「用户名/项目目录任意」的通配搜索（``root/*/*/{file_path}``），
  命中唯一即解析成功——这样调用方无需知道项目目录名；
- 命中多个候选时报 ``ambiguous``（提示传 ``project_dir``），绝不做猜测；
- http(s) 直链与已存在的本地路径原样透传（交给上游处理）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

STATIC_PROJECTS_PREFIX = "/static/projects/"


@dataclass(frozen=True)
class MediaResolution:
    """一次素材引用的解析结果。"""

    ok: bool
    path: str = ""
    mode: str = ""  # resolved | passthrough
    error: str = ""
    code: str = ""  # not_found | ambiguous | invalid

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.ok,
            "path": self.path,
            "mode": self.mode,
            "error": self.error,
            "code": self.code,
        }


def _candidate_hits(root: Path, token: str, file_path: str) -> list[Path]:
    """在 root 下找 ``{root}/{token}/{file_path}`` 与 ``{root}/*/{token}/{file_path}``。"""
    hits: list[Path] = []
    direct = root / token / file_path
    if direct.is_file():
        hits.append(direct)
    try:
        children = sorted(root.iterdir())
    except OSError:
        return hits
    for child in children:
        if not child.is_dir():
            continue
        candidate = child / token / file_path
        if candidate.is_file():
            hits.append(candidate)
    return hits


def _wildcard_hits(root: Path, file_path: str) -> list[Path]:
    """在 ``root/*/*/{file_path}``（用户名/项目目录任意）下搜索。"""
    hits: list[Path] = []
    try:
        usernames = sorted(root.iterdir())
    except OSError:
        return hits
    for username_dir in usernames:
        if not username_dir.is_dir():
            continue
        try:
            projects = sorted(username_dir.iterdir())
        except OSError:
            continue
        for project_dir in projects:
            if not project_dir.is_dir():
                continue
            candidate = project_dir / file_path
            if candidate.is_file():
                hits.append(candidate)
    return hits


def resolve_media_reference(
    reference: str,
    roots: list[Path],
    project_dir: str | None = None,
) -> MediaResolution:
    """解析素材引用，返回本地路径或透传值。"""
    reference = (reference or "").strip()
    if not reference:
        return MediaResolution(False, error="media reference is empty", code="invalid")

    parsed = urlparse(reference)
    if parsed.scheme in ("http", "https"):
        # 外部直链：上游 ffmpeg 自行下载（公开可访问的源专用）。
        return MediaResolution(True, path=reference, mode="passthrough")

    # 已存在的本地路径（绝对或相对服务进程 CWD）直接透传。
    local_candidate = Path(reference).expanduser()
    if local_candidate.is_file():
        return MediaResolution(True, path=str(local_candidate.resolve()), mode="passthrough")

    raw_path = parsed.path if parsed.scheme else reference.split("?", 1)[0]
    raw_path = unquote(raw_path)
    if not raw_path.startswith(STATIC_PROJECTS_PREFIX):
        return MediaResolution(
            False,
            error=f"unsupported media reference (expect a local file path or {STATIC_PROJECTS_PREFIX}...): {reference}",
            code="invalid",
        )

    rest = raw_path[len(STATIC_PROJECTS_PREFIX):]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return MediaResolution(False, error=f"malformed static reference: {reference}", code="invalid")
    project_token, file_path = parts[0], parts[1]
    if ".." in Path(file_path).parts:
        return MediaResolution(False, error="path traversal rejected", code="invalid")

    dir_tokens: list[str] = []
    for token in (project_dir, project_token):
        token = (token or "").strip().strip("/")
        if token and token not in dir_tokens and "/" not in token and ".." not in Path(token).parts:
            dir_tokens.append(token)

    matches: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for token in dir_tokens:
            for hit in _candidate_hits(root, token, file_path):
                resolved = str(hit.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    matches.append(hit)

    if not matches:
        # token 都没命中时，回退到「用户名/项目目录任意」的通配搜索：
        # URL 里的 project 是项目 id，磁盘目录是项目名，调用方常拿不到项目名。
        for root in roots:
            root = Path(root).expanduser()
            if not root.is_dir():
                continue
            for hit in _wildcard_hits(root, file_path):
                resolved = str(hit.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    matches.append(hit)

    if not matches:
        return MediaResolution(
            False,
            error=(
                f"media not found under configured roots {[str(r) for r in roots]}: {raw_path}. "
                "Check VECTCUT_MEDIA_ROOTS covers the folder that holds this project."
            ),
            code="not_found",
        )
    if len(matches) > 1:
        return MediaResolution(
            False,
            error=(
                f"ambiguous media reference ({len(matches)} candidates): "
                + ", ".join(str(m) for m in matches[:4])
                + ". Pass project_dir to disambiguate."
            ),
            code="ambiguous",
        )
    return MediaResolution(True, path=str(matches[0].resolve()), mode="resolved")