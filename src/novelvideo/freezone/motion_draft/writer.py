"""[aipdd fork delta] 动效大片交付：把组装好的草稿字典落成一盘剪映工程目录。

剪映认的不是一个 JSON 文件，而是一整个目录：内容本体 ``draft_content.json`` 之外还有
``.bak`` / ``template-2.tmp`` / ``template.tmp`` 几份镜像、``Timelines/<时间线ID>/`` 下面
那一套同名文件、``draft_meta_info.json`` 工程索引、``project.json`` 时间线索引和
``timeline_layout.json`` 面板布局。只写本体的话，剪映 9.x/10.x 打开就是一条空时间轴
（它读 ``Timelines/<ID>/draft_content.json``），或者干脆在工程列表里不出现。

所以本模块的口径是**先整盘复制随包模板骨架、再覆盖内容**：骨架带着那四十来个空数组和
所有侧文件，内容层由 ``schema.build_draft_content`` 生成，时间线 ID 用草稿自己的 ``id``
重命名目录并把各索引文件对齐。写完后按调用方意愿整盘复制进本机剪映工程目录（Windows
的 ``JianyingPro`` 与 ``CapCut`` 两个安装根都探，macOS 走容器目录），并清掉残留的
``.locked``——那是剪映打开工程时留的锁，留着会让新工程打不开。

只做文件读写，不出网、不调模型、不启动剪映；模板缺失或目录不可写时返回带中文原因的
失败结果，绝不抛栈给上层 runner。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.freezone.motion_draft.schema import MotionDraftSpec, build_draft_content

# 10.2 骨架里那套「内容本体 + 镜像」文件名——缺一个镜像，剪映的自动保存可能把内容覆盖回空。
CONTENT_FILE = "draft_content.json"
CONTENT_MIRRORS = ("draft_content.json.bak", "template-2.tmp")
TIMELINE_CONTENT_FILE = "template.tmp"
META_FILE = "draft_meta_info.json"
SETTINGS_FILE = "draft_settings"
_TEMPLATE_MARKER = Path("services") / "vectcut" / "template_jianying_10_2"


@dataclass(slots=True)
class DraftWriteResult:
    """一次落盘的结果：草稿目录 + 时间线 ID + 实际写出的文件，失败时给中文原因。"""

    ok: bool
    draft_dir: str = ""
    timeline_id: str = ""
    deployed_dir: str = ""
    error: str = ""
    files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "draft_dir": self.draft_dir,
            "timeline_id": self.timeline_id,
            "deployed_dir": self.deployed_dir,
            "error": self.error,
            "files": list(self.files),
        }


def _project_root() -> Path:
    env_root = os.environ.get("NOVELVIDEO_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    here = Path(__file__).resolve()
    # 认「能找到模板骨架」的那一层，而不是认 pyproject.toml：桌面安装包里没有
    # pyproject.toml，布局是 <资源根>/app/{src,services}，只按 pyproject 找会一路落到
    # "/app"，Windows 上那是个不存在的目录 → 模板永远探不到。
    for parent in here.parents:
        if (parent / _TEMPLATE_MARKER / CONTENT_FILE).exists():
            return parent
    return Path("/app")


def template_dir() -> Path | None:
    """随包 10.2 模板骨架目录；探不到返回 None（调用方据此给配置提示）。"""
    env_dir = (os.environ.get("MOTION_DRAFT_TEMPLATE_DIR") or "").strip()
    if env_dir:
        candidate = Path(env_dir)
        if (candidate / CONTENT_FILE).exists():
            return candidate
    candidate = _project_root() / _TEMPLATE_MARKER
    if (candidate / CONTENT_FILE).exists():
        return candidate
    return None


def jianying_projects_dir() -> Path | None:
    """本机剪映/CapCut 的工程根目录；没装或路径不对返回 None。

    Windows 上国内版落在 ``JianyingPro``、海外版落在 ``CapCut``，两个都探（先国内版）；
    macOS 只有容器目录一种。
    """
    if os.name == "nt":
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if not local:
            return None
        bases = [
            Path(local) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
            Path(local) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
        ]
    else:
        bases = [
            Path.home()
            / "Library/Containers/com.lemon.lvpro/Data/Documents/JianyingPro/User Data/Projects/com.lveditor.draft"
        ]
    for base in bases:
        if base.is_dir():
            return base
    return None


def load_template() -> dict[str, Any]:
    """读取模板骨架内容本体；模板缺失时抛 ``FileNotFoundError`` 由调用方兜住。"""
    root = template_dir()
    if root is None:
        raise FileNotFoundError(
            "没找到随包的剪映 10.2 草稿模板（services/vectcut/template_jianying_10_2）："
            "请确认安装包资源完整，或用 MOTION_DRAFT_TEMPLATE_DIR 指定模板目录"
        )
    return json.loads((root / CONTENT_FILE).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any], *, indent: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(payload, ensure_ascii=False, indent=2)
        if indent
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    path.write_text(text, encoding="utf-8")


def _clear_locks(draft_dir: Path) -> None:
    """清掉工程目录里残留的 ``.locked``：那是剪映打开工程时的锁，留着新工程打不开。"""
    for path in draft_dir.rglob(".locked"):
        try:
            path.unlink()
        except OSError:
            pass


def _retime_sidecars(draft_dir: Path, now_seconds: int) -> None:
    """``draft_settings`` 的时间戳用本次落盘时间：全零会让剪映工程列表把排序落到最末。"""
    settings = draft_dir / SETTINGS_FILE
    if not settings.exists():
        return
    text = settings.read_text(encoding="utf-8", errors="replace")
    replaced = text
    for key in ("draft_create_time", "draft_last_edit_time"):
        replaced = _replace_ini_value(replaced, key, str(now_seconds))
    settings.write_text(replaced, encoding="utf-8")


def _replace_ini_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    hit = False
    for index, line in enumerate(lines):
        name, sep, _ = line.partition("=")
        if sep and name.strip() == key:
            lines[index] = f"{key}={value}"
            hit = True
    if hit:
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text


def _write_meta(draft_dir: Path, spec: MotionDraftSpec, draft_id: str, now_seconds: int) -> None:
    """``draft_meta_info.json``：工程索引里的名称/路径/时间是剪映工程列表显示的那几列。"""
    meta_path = draft_dir / META_FILE
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except ValueError:
            meta = {}
    forward = str(draft_dir).replace("\\", "/")
    meta["draft_id"] = draft_id
    meta["draft_name"] = spec.name
    meta["draft_fold_path"] = forward
    # ``draft_root_path`` 是工程根（com.lveditor.draft），剪映用它拼相对路径。
    meta["draft_root_path"] = forward[: forward.rfind("/")]
    meta["tm_draft_cloud_modified"] = now_seconds
    meta.setdefault("draft_cloud_materials", [])
    meta.setdefault("draft_cloud_last_action_download", False)
    _write_json(meta_path, meta, indent=False)


def write_motion_draft(
    spec: MotionDraftSpec,
    *,
    out_dir: Path | str,
    deploy: bool = False,
    project_name: str = "",
    platform: dict[str, Any] | None = None,
) -> DraftWriteResult:
    """把 ``spec`` 落成 ``out_dir`` 里的一盘剪映工程，可选再复制进本机剪映工程目录。

    ``out_dir`` 是本次草稿的目录（不存在会创建；已存在则覆盖同名文件，不删多余文件）。
    ``deploy`` 为真时整盘复制进 ``jianying_projects_dir()``，目录名取 ``project_name``
    或草稿名；本机没装剪映时只告警不失败——工程目录本身已经可用。
    """
    target = Path(out_dir)
    skeleton = template_dir()
    if skeleton is None:
        return DraftWriteResult(
            ok=False,
            error=(
                "没找到随包的剪映 10.2 草稿模板（services/vectcut/template_jianying_10_2）："
                "请确认安装包资源完整，或用 MOTION_DRAFT_TEMPLATE_DIR 指定模板目录"
            ),
        )
    try:
        template = json.loads((skeleton / CONTENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return DraftWriteResult(ok=False, error=f"读取草稿模板失败：{exc}")

    try:
        doc = build_draft_content(spec, template, platform=platform)
        target.mkdir(parents=True, exist_ok=True)
        # 先铺骨架（含 Timelines/ 与全部侧文件），再覆盖内容层——顺序不能反，否则侧文件
        # 会被骨架里的占位内容盖回去。
        shutil.copytree(skeleton, target, dirs_exist_ok=True)
    except (OSError, ValueError) as exc:
        return DraftWriteResult(ok=False, draft_dir=str(target), error=f"准备草稿目录失败：{exc}")

    timeline_id = str(doc.get("id") or "")
    now_seconds = int(time.time())
    content = json.dumps(doc, ensure_ascii=False, indent=2)
    written: list[str] = []
    try:
        for name in (CONTENT_FILE, *CONTENT_MIRRORS, TIMELINE_CONTENT_FILE):
            path = target / name
            path.write_text(content, encoding="utf-8")
            written.append(str(path))

        timelines_dir = target / "Timelines"
        if timeline_id:
            timeline_dir = timelines_dir / timeline_id
            # 骨架自带一条占位时间线目录，复制进来后 Timelines/ 里就有两份候选；按
            # iterdir() 的顺序取第一份的话，重导一次会留下一份装着旧内容的时间线，
            # 而剪映读的恰好是 Timelines/<ID>/draft_content.json。只保留我们要的那一份。
            if timelines_dir.exists():
                for stale in timelines_dir.iterdir():
                    if stale.is_dir() and stale != timeline_dir:
                        shutil.rmtree(stale)
            timeline_dir.mkdir(parents=True, exist_ok=True)
            for name in (CONTENT_FILE, *CONTENT_MIRRORS, TIMELINE_CONTENT_FILE):
                path = timeline_dir / name
                path.write_text(content, encoding="utf-8")
                written.append(str(path))
            project = {
                "config": {
                    "color_space": -1,
                    "render_index_track_mode_on": False,
                    "use_float_render": False,
                },
                "create_time": now_seconds,
                "id": timeline_id,
                "main_timeline_id": timeline_id,
                "timelines": [
                    {
                        "create_time": now_seconds,
                        "id": timeline_id,
                        "is_marked_delete": False,
                        "name": "时间线01",
                        "update_time": now_seconds,
                    }
                ],
                "update_time": now_seconds,
                "version": 0,
            }
            for name in ("project.json", "project.json.bak"):
                path = timelines_dir / name
                _write_json(path, project, indent=False)
                written.append(str(path))
            layout_path = target / "timeline_layout.json"
            if layout_path.exists():
                try:
                    layout = json.loads(layout_path.read_text(encoding="utf-8"))
                    for item in layout.get("dockItems", []):
                        item["timelineIds"] = [timeline_id]
                        item["timelineNames"] = ["时间线01"]
                    _write_json(layout_path, layout, indent=False)
                    written.append(str(layout_path))
                except ValueError:
                    pass

        _write_meta(target, spec, timeline_id or str(target.name), now_seconds)
        _retime_sidecars(target, now_seconds)
        _clear_locks(target)
    except OSError as exc:
        return DraftWriteResult(
            ok=False,
            draft_dir=str(target),
            timeline_id=timeline_id,
            error=f"写入草稿文件失败：{exc}",
            files=written,
        )

    result = DraftWriteResult(
        ok=True, draft_dir=str(target), timeline_id=timeline_id, files=written
    )
    if not deploy:
        return result

    projects = jianying_projects_dir()
    if projects is None:
        # 没装剪映不影响交付：工程目录已经落好，用户拷进有剪映的机器即可。
        return result
    name = project_name or spec.name or target.name
    dest = projects / name
    renamed = False
    if dest.exists() and _draft_id_in(dest) != timeline_id:
        # 同名但不是我们这盘工程：那是用户自己的草稿，删它等于删用户的活。换个不撞的名字
        # 落，并把落成的名字如实报出去（ok 仍然 ok，只是位置不是用户预期的那一个）。
        renamed = True
        for index in range(1, 100):
            tail = target.name if index == 1 else f"{target.name}-{index}"
            candidate = projects / f"{name}-{tail}"
            if not candidate.exists() or _draft_id_in(candidate) == timeline_id:
                dest = candidate
                break
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(target, dest)
        _clear_locks(dest)
        result.deployed_dir = str(dest)
        if renamed:
            result.error = f"剪映工程目录里已有同名工程，本次草稿落成为 {dest.name}"
    except OSError as exc:
        result.error = f"草稿已生成在 {target}，但复制进剪映工程目录失败：{exc}"
    return result


def _draft_id_in(draft_dir: Path) -> str:
    """已存在工程记录的 ``draft_id``；读不出就当不是我们的。"""
    try:
        meta = json.loads((draft_dir / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(meta.get("draft_id") or "") if isinstance(meta, dict) else ""
