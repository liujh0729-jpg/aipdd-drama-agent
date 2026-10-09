"""[AIPDD] VectCut 服务适配层入口。

职责（升级 VectCut 上游时本文件与 ``aipdd/`` 目录保留、无需改动）：

1. 跨平台启动：Windows 保留上游原生行为（剪映桌面热重载）；macOS/Linux
   注入 ``desktop_companion`` 兼容 stub —— 上游 ``capcut_server.py`` 顶层
   执行 ``ctypes.windll.user32``，非 Windows 平台 import 即崩。
2. 配置自举：``config.json`` 缺失时按环境变量生成（draft_profile / 端口）。
   上游 ``settings/local.py`` 固定读取同目录 ``config.json``。
3. 附加 ``/aipdd/*`` 路由：
   - ``GET  /aipdd/health``        服务信息（草稿目录、素材根），供 MCP 探测；
   - ``POST /aipdd/resolve_media`` 素材引用 → 本地路径解析（见 media_resolver）；
   - ``POST /aipdd/save_draft``    保存草稿，未指定 draft_folder 时注入
     ``VECTCUT_DRAFT_DIR``（默认 ``<service>/drafts``），保证草稿总是落在
     部署方可控、用户可见的目录；
   - ``GET  /aipdd/drafts``        列出草稿目录下的草稿文件夹（资产库同步用）；
   - ``POST /aipdd/drafts/upload`` 上传一份草稿文件夹（资产库上传用）；
   - ``GET  /aipdd/drafts/<draft_id>/download`` 把草稿文件夹打包成 zip 下发，
     供 Dramaclaw 资产库「剪映草稿」栏目的下载入口使用。

草稿目录覆盖：save_draft / drafts / drafts/upload / drafts/<id>/download 四个入口
都接受 ``draft_folder``（form 字段或 query 参数）覆盖默认根目录——Dramaclaw
「设置 → 剪映草稿」里配置的剪映草稿目录就通过它透传进来；覆盖值只接受绝对
路径且拒绝 ``..`` 段。

环境变量：
   VECTCUT_HOST          监听地址（默认 127.0.0.1；容器内用 0.0.0.0）
   VECTCUT_PORT          监听端口（默认 9001，亦写入自动生成的 config.json）
   VECTCUT_DRAFT_PROFILE 草稿格式（upstream: capcut_legacy / jianying_legacy /
                         jianying_pro_10，默认 jianying_pro_10）
   VECTCUT_DRAFT_DIR     默认草稿输出目录（默认 <service>/drafts）
   VECTCUT_MEDIA_ROOTS   素材根目录列表（POSIX 冒号分隔；默认 <repo>/state:<repo>/output）
   VECTCUT_INTERNAL_TOKEN 可选服务间共享密钥。非空时，除 /aipdd/health 外的
                        /aipdd/* 入口必须带匹配的 ``X-VectCut-Token`` 头；未设时
                        不校验，行为与旧版一致（向后兼容）。
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import sys
import types
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath

SERVICE_DIR = Path(__file__).resolve().parents[1]
# 容器里服务代码可直接位于文件系统根（如 /service），没有仓库层级——REPO_ROOT
# 仅作本机部署的素材根兜底；容器部署一律显式提供 VECTCUT_MEDIA_ROOTS。
_parents = SERVICE_DIR.parents
REPO_ROOT = _parents[1] if len(_parents) > 1 else SERVICE_DIR

DEFAULT_PORT = 9001
DEFAULT_DRAFT_PROFILE = "jianying_pro_10"
# 草稿 id 白名单：上游生成的 draft_id 形如 ``dfd_xxxx``，下载路由只认这些字符，
# 防目录穿越。点号在白名单内，但整段纯点（``.`` / ``..``）仍属非法 id，用前瞻
# 要求至少一个字母数字。
DRAFT_ID_RE = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}$")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _internal_token_required(path: str, supplied: str, expected: str) -> bool:
    """O19 共享密钥判定的纯函数（便于无 Flask 依赖单测）。

    返回 ``True`` 表示该请求应被拒：部署侧设了 ``expected`` 且目标
    是非健康检查的 ``/aipdd/*`` 入口，但 ``supplied`` 头缺失或不匹配。
    ``expected`` 为空（默认）时永不拒，保证向后兼容。
    """
    if not expected:
        return False
    path = path or ""
    if not path.startswith("/aipdd/") or path == "/aipdd/health":
        return False
    supplied = (supplied or "").strip()
    return not (supplied and hmac.compare_digest(supplied, expected))


def _install_desktop_companion_stub() -> None:
    """非 Windows 平台用可报告的 no-op stub 顶替 desktop_companion。"""
    if sys.platform == "win32" or "desktop_companion" in sys.modules:
        return

    stub = types.ModuleType("desktop_companion")

    def _unsupported(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "success": False,
            "error": "desktop companion (CapCut/Jianying hot reload) requires Windows",
        }

    stub.reload_capcut_desktop = _unsupported  # type: ignore[attr-defined]
    stub.get_capcut_windows = lambda *_a, **_k: []  # type: ignore[attr-defined]
    sys.modules["desktop_companion"] = stub


def default_draft_dir() -> Path:
    """默认草稿输出目录。"""
    configured = _env("VECTCUT_DRAFT_DIR")
    return Path(configured).expanduser() if configured else SERVICE_DIR / "drafts"


def _safe_dir_override(raw: str) -> Path | None:
    """校验调用方给的草稿目录覆盖值：绝对路径且无 ``..`` 段，否则返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if ".." in path.parts:
        return None
    if not path.is_absolute():
        return None
    return path


def resolve_draft_root(override: str | None) -> Path:
    """草稿根目录：调用方覆盖（Dramaclaw 设置 → 剪映草稿目录）优先，否则部署默认。"""
    text = str(override or "").strip()
    if text:
        safe = _safe_dir_override(text)
        if safe is None:
            raise ValueError(f"invalid draft_folder: {text!r}")
        return safe
    return default_draft_dir()


def _safe_relative_path(raw: str) -> PurePosixPath | None:
    """上传文件的相对路径：拒绝绝对路径、``..``、盘符/冒号与超深嵌套。"""
    text = str(raw or "").strip().replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or len(parts) > 32:
        return None
    for part in parts:
        if part == ".." or ":" in part or "\x00" in part:
            return None
    return PurePosixPath(*parts)


def media_roots() -> list[Path]:
    """素材根目录列表（resolve_media 用）。"""
    configured = _env("VECTCUT_MEDIA_ROOTS")
    if configured:
        return [Path(p).expanduser() for p in configured.split(os.pathsep) if p.strip()]
    return [REPO_ROOT / "state", REPO_ROOT / "output"]


def draft_profile_name() -> str:
    return _env("VECTCUT_DRAFT_PROFILE", DEFAULT_DRAFT_PROFILE)


def _ensure_config() -> None:
    """config.json 缺失时按环境变量生成（存在则一律尊重用户配置）。"""
    config_path = SERVICE_DIR / "config.json"
    if config_path.exists():
        return
    try:
        port = int(_env("VECTCUT_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    payload = {
        "draft_profile": draft_profile_name(),
        "port": port,
        "is_upload_draft": False,
        "preview_router": "/draft/downloader",
    }
    try:
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:  # 只读挂载等场景忽略，上游会退化到自身默认值
        print(f"[aipdd] warning: cannot write {config_path}: {exc}", file=sys.stderr)


def _register_extras(app: object) -> None:
    from flask import jsonify, request

    @app.before_request  # type: ignore[attr-defined]
    def aipdd_require_internal_token() -> object:
        """O19 纵深防御：可选服务间共享密钥。

        默认（VECTCUT_INTERNAL_TOKEN 未设）直接放行，行为与旧版一致；一旦
        部署侧设了该变量，除健康检查外的 /aipdd/* 入口必须带匹配的
        ``X-VectCut-Token``（用常量时间比较），否则返 401。
        """
        expected = _env("VECTCUT_INTERNAL_TOKEN")
        supplied = request.headers.get("X-VectCut-Token")
        if _internal_token_required(request.path, supplied, expected):
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return None

    @app.get("/aipdd/health")  # type: ignore[attr-defined]
    def aipdd_health() -> object:
        return jsonify(
            {
                "success": True,
                "service": "vectcut-aipdd",
                "draft_profile": draft_profile_name(),
                "draft_dir": str(default_draft_dir()),
                "media_roots": [str(p) for p in media_roots()],
                "platform": sys.platform,
            }
        )

    @app.post("/aipdd/resolve_media")  # type: ignore[attr-defined]
    def aipdd_resolve_media() -> object:
        from aipdd.media_resolver import resolve_media_reference

        data = request.get_json(silent=True) or {}
        result = resolve_media_reference(
            str(data.get("reference") or ""),
            roots=media_roots(),
            project_dir=str(data.get("project_dir") or "") or None,
        )
        return jsonify(result.as_dict())

    @app.post("/aipdd/save_draft")  # type: ignore[attr-defined]
    def aipdd_save_draft() -> object:
        from save_draft_impl import save_draft_impl

        data = request.get_json(silent=True) or {}
        draft_id = str(data.get("draft_id") or "").strip()
        if not draft_id:
            return jsonify({"success": False, "output": None, "error": "draft_id is required"})
        draft_folder = str(data.get("draft_folder") or "").strip()
        try:
            draft_dir = resolve_draft_root(draft_folder or None)
        except ValueError as exc:
            return jsonify({"success": False, "output": None, "error": str(exc)})
        draft_dir.mkdir(parents=True, exist_ok=True)
        try:
            output = save_draft_impl(
                draft_id,
                str(draft_dir),
                project_name=data.get("project_name"),
                auto_deploy=bool(data.get("auto_deploy", True)),
            )
        except Exception as exc:  # noqa: BLE001 - 统一以 JSON 形式返回给调用方
            return jsonify({"success": False, "output": None, "error": str(exc)})
        return jsonify(
            {
                "success": bool(output.get("success")),
                "output": output,
                "error": str(output.get("error") or ""),
            }
        )

    @app.get("/aipdd/drafts")  # type: ignore[attr-defined]
    def aipdd_list_drafts() -> object:
        """列出草稿目录下的草稿文件夹（资产库「同步草稿目录」用）。"""
        override = (request.args.get("draft_folder") or "").strip()
        try:
            root = resolve_draft_root(override or None)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        drafts: list[dict[str, object]] = []
        if root.is_dir():
            for child in sorted(root.iterdir(), key=lambda p: p.name):
                if child.is_symlink() or not child.is_dir() or child.name.startswith("."):
                    continue
                file_count = 0
                size_bytes = 0
                for entry in child.rglob("*"):
                    if entry.is_symlink() or not entry.is_file():
                        continue
                    file_count += 1
                    try:
                        size_bytes += entry.stat().st_size
                    except OSError:
                        continue
                drafts.append(
                    {
                        "draft_id": child.name,
                        "file_count": file_count,
                        "size_bytes": size_bytes,
                        "modified_at": datetime.fromtimestamp(
                            child.stat().st_mtime
                        ).isoformat(),
                    }
                )
        return jsonify({"success": True, "drafts": drafts})

    @app.post("/aipdd/drafts/upload")  # type: ignore[attr-defined]
    def aipdd_upload_draft() -> object:
        """上传一份草稿文件夹（multipart 平铺文件 + 相对路径），落盘到草稿目录。

        一次上传 = 一个草稿文件夹 = 目标目录 ``<root>/<draft_id>/``；落盘过程中
        的任何失败都会回收已写入的半成品，不留半个草稿在目录里。
        """
        draft_id = str(request.form.get("draft_id") or "").strip()
        if not DRAFT_ID_RE.match(draft_id):
            return jsonify({"success": False, "error": f"invalid draft_id: {draft_id!r}"}), 400
        override = str(request.form.get("draft_folder") or "").strip()
        try:
            root = resolve_draft_root(override or None)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        files = request.files.getlist("files")
        relative_paths = request.form.getlist("relative_paths")
        if not files:
            return jsonify({"success": False, "error": "files is required"}), 400
        if len(relative_paths) != len(files):
            return jsonify(
                {"success": False, "error": "relative_paths count must match files"}
            ), 400
        target = root / draft_id
        if target.exists():
            return jsonify(
                {"success": False, "error": f"draft already exists: {draft_id}"}
            ), 400
        written = 0
        total_bytes = 0
        try:
            for storage, raw_rel in zip(files, relative_paths):
                rel = _safe_relative_path(raw_rel)
                if rel is None:
                    raise ValueError(f"invalid relative path: {raw_rel!r}")
                dest = target.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                storage.save(str(dest))
                written += 1
                total_bytes += dest.stat().st_size
        except ValueError as exc:
            shutil.rmtree(target, ignore_errors=True)
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - 统一以 JSON 形式返回给调用方
            shutil.rmtree(target, ignore_errors=True)
            return jsonify({"success": False, "error": str(exc)}), 500
        return jsonify(
            {
                "success": True,
                "output": {
                    "draft_id": draft_id,
                    "draft_dir": str(target),
                    "files": written,
                    "size_bytes": total_bytes,
                },
            }
        )

    @app.get("/aipdd/drafts/<draft_id>/download")  # type: ignore[attr-defined]
    def aipdd_download_draft(draft_id: str) -> object:
        """把草稿文件夹打包成 zip 下发（资产库「剪映草稿」的下载入口）。"""
        import tempfile

        from flask import send_file

        if not DRAFT_ID_RE.match(draft_id):
            return jsonify({"success": False, "error": f"invalid draft_id: {draft_id!r}"}), 400
        override = (request.args.get("draft_folder") or "").strip()
        try:
            root = resolve_draft_root(override or None).resolve()
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        draft_dir = (root / draft_id).resolve()
        # resolve 后再校验父目录：``..`` 或符号链接绕出去的一律按不存在处理。
        if draft_dir.parent != root or not draft_dir.is_dir():
            return jsonify({"success": False, "error": f"draft not found: {draft_id}"}), 404

        fd, tmp_path = tempfile.mkstemp(prefix=f"aipdd_draft_{draft_id}_", suffix=".zip")
        os.close(fd)
        try:
            shutil.make_archive(tmp_path[: -len(".zip")], "zip", str(draft_dir))
        except Exception as exc:  # noqa: BLE001 - 打包失败以 JSON 形式返回给调用方
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return jsonify({"success": False, "error": str(exc)}), 500

        response = send_file(
            tmp_path,
            as_attachment=True,
            download_name=f"{draft_id}.zip",
            mimetype="application/zip",
        )

        @response.call_on_close
        def _cleanup_archive() -> None:
            # Linux 上先 unlink 也不影响已打开的 fd，发送完即回收临时包。
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return response


def main() -> None:
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    _ensure_config()
    _install_desktop_companion_stub()

    import capcut_server  # noqa: E402  - 必须在 _ensure_config/_install_stub 之后

    _register_extras(capcut_server.app)

    host = _env("VECTCUT_HOST", "127.0.0.1")
    try:
        port = int(_env("VECTCUT_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    print(
        f"[aipdd] vectcut service listening on {host}:{port} "
        f"(draft dir: {default_draft_dir()}, media roots: {[str(p) for p in media_roots()]})",
        file=sys.stderr,
    )
    capcut_server.app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()