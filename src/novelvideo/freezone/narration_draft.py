"""[aipdd fork delta] 口播智能剪辑：后端驱动 VectCut 组装剪映草稿（纯本地服务调用）。

本模块把「剪后正片 + 字幕 + B-roll + BGM」通过 VectCut 草稿服务的 HTTP 端点逐条落
轨、保存成一张剪映/CapCut 草稿。它是**纯本地服务调用**：只连部署侧配置的 VectCut
服务（``VECTCUT_BASE_URL``，compose 注入 / 本机直跑），不出模型网关、不取任何凭证，
因此不经 ``_call_freezone_leaf`` 分发器、不进 ``FREEZONE_LEAF_EGRESS`` 分类表——与
``narration_edit`` 里的本地 ffmpeg 算子同一口径。

调用顺序（缺任一必需要素即跳过该步、绝不阻断其它产物）：

1. ``POST /create_draft``：按画幅建空白画布，拿 ``draft_id``。
2. ``POST /add_video``：逐条保留段引用**源视频**并按 ``start``/``end`` 裁切、按
   ``target_start`` 顺次贴到时间轴（草稿里做的是时间线裁切，不重编码）。
3. ``POST /add_subtitle``：把剪后时间轴的 SRT（临时文件绝对路径）导入字幕轨。
4. ``POST /add_image``：把 B-roll 生图产物按槽位时间贴到图片轨。
5. ``POST /add_audio``：把 BGM 按整片时长垫到音频轨（音量 ``bgm_gain``）。
6. ``POST /aipdd/save_draft``：落盘到配置的剪映草稿目录（``get_vectcut_draft_dir``
   透传 ``draft_folder``；未配置时 VectCut 回退部署默认）。

素材一律传**共享卷内的本地绝对路径**：Dramaclaw 与 VectCut 同机/同 ``/data`` 卷时，
服务能直接读到，无需再走 ``/aipdd/resolve_media``（那层是给 URL/项目相对引用兜底的）。

VectCut 未配置（``VECTCUT_BASE_URL`` 为空）时不猜本机地址，直接返回内联配置提示，
调用方据此在结果里给用户「配 VECTCUT_BASE_URL + 设置→剪映草稿目录」的引导，而
video 直出产物照常。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Sequence

import httpx

# 与 ``chat/dramaclaw_tools.py`` 的 VectCut 工具组同一口径：不兜底本机地址，没配置
# 就直说没配置（compose 注入 http://vectcut:9001，宿主机直跑才是 127.0.0.1）。
VECTCUT_NOT_CONFIGURED_HINT = (
    "未配置 VectCut 服务地址：请设置 VECTCUT_BASE_URL（docker compose 已注入 "
    "http://vectcut:9001；宿主机直跑通常是 http://127.0.0.1:9001），并在「设置 → "
    "剪映草稿目录」配置草稿落盘目录。草稿导出依赖 VectCut 服务，视频直出不受影响。"
)

try:
    _TIMEOUT_SECONDS = max(60, int(os.environ.get("VECTCUT_TIMEOUT_SECONDS", "300")))
except ValueError:
    _TIMEOUT_SECONDS = 300


def _base_url() -> str:
    return (os.environ.get("VECTCUT_BASE_URL") or "").strip().rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    # 可选服务间共享密钥：部署侧设了 VECTCUT_INTERNAL_TOKEN 才附带（与 vectcut 侧
    # 同名变量），未设时 vectcut 不校验，行为不变。
    token = (os.environ.get("VECTCUT_INTERNAL_TOKEN") or "").strip()
    if token:
        headers["X-VectCut-Token"] = token
    return headers


@dataclass(slots=True)
class DraftStep:
    """一条落轨步骤的执行记录，供结果 report 追溯（成功/失败 + 简短错误）。"""

    name: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class DraftBuildResult:
    """``build_vectcut_draft`` 的返回值：成功标志 + draft_id + 逐步记录。"""

    ok: bool
    draft_id: str = ""
    saved: bool = False
    not_configured: bool = False
    hint: str = ""
    error: str = ""
    steps: list[DraftStep] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "draft_id": self.draft_id,
            "saved": self.saved,
            "not_configured": self.not_configured,
            "hint": self.hint,
            "error": self.error,
            "steps": [
                {"name": s.name, "ok": s.ok, "detail": s.detail} for s in self.steps
            ],
        }


def _extract_error(resp: dict[str, Any]) -> str:
    err = resp.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    return f"VectCut 返回 success=False：{resp!r}"[:240]


async def _post(client: httpx.AsyncClient, base: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST 一条 VectCut 端点，解析 JSON；HTTP/网络异常归一成 ``{success:False,error}``。"""
    try:
        resp = await client.post(f"{base}{path}", json=body, headers=_headers())
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"请求 VectCut {path} 失败：{exc}"}
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or "")[:240]
        return {"success": False, "error": f"VectCut {path} 非 JSON 响应（HTTP {resp.status_code}）：{text}"}
    if resp.status_code >= 400 and isinstance(data, dict) and data.get("success") is not False:
        return {"success": False, "error": f"VectCut {path} HTTP {resp.status_code}"}
    return data if isinstance(data, dict) else {"success": True, "output": data}


def _clean_body(body: dict[str, Any]) -> dict[str, Any]:
    """丢掉 None / 空串的可选字段，保持必填项（url / draft_id）恒定传入。"""
    out: dict[str, Any] = {}
    for key, value in body.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[key] = value
    return out


async def build_vectcut_draft(
    *,
    width: int,
    height: int,
    video_clips: Sequence[dict[str, Any]],
    srt_path: str = "",
    broll_images: Sequence[dict[str, Any]] = (),
    bgm_path: str = "",
    bgm_gain: float = 0.2,
    final_duration: float = 0.0,
    draft_folder: str = "",
    project_name: str = "",
) -> DraftBuildResult:
    """后端驱动 VectCut 把正片/字幕/B-roll/BGM 落成一张剪映草稿。

    ``video_clips``：``[{url, source_start, source_end, timeline_start, duration?}]``
    ——逐条引用源视频、按 ``source_start/end`` 裁切、贴到 ``timeline_start``。
    ``broll_images``：``[{url, timeline_start, timeline_end}]``——B-roll 生图槽位。
    ``srt_path``：剪后时间轴 SRT 的本地绝对路径（空则跳过字幕）。
    ``bgm_path``：BGM 本地绝对路径（空则跳过 BGM）。
    ``draft_folder``：草稿落盘目录；空则读「设置 → 剪映草稿目录」，仍空则交给
    VectCut 部署默认。
    任一步失败即中断后续落轨并如实回报（草稿未保存），但绝不抛出——调用方（runner）
    负责把它并进结果、不阻断 video 直出。
    """
    base = _base_url()
    if not base:
        return DraftBuildResult(ok=False, not_configured=True, hint=VECTCUT_NOT_CONFIGURED_HINT)

    clips = [c for c in (video_clips or []) if str(c.get("url") or "").strip()]
    if not clips:
        return DraftBuildResult(ok=False, error="无正片切段可落轨")

    result = DraftBuildResult(ok=False)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
            # 1) create_draft
            create = await _post(client, base, "/create_draft", {"width": int(width), "height": int(height)})
            if not create.get("success"):
                result.error = _extract_error(create)
                result.steps.append(DraftStep("create_draft", False, result.error))
                result.hint = (
                    f"无法创建 VectCut 草稿：{result.error}。请确认 VectCut 服务已启动且 "
                    "VECTCUT_BASE_URL 指向它。"
                )
                return result
            output = create.get("output") or {}
            draft_id = str(output.get("draft_id") or "") if isinstance(output, dict) else ""
            if not draft_id:
                result.error = "create_draft 未返回 draft_id"
                result.steps.append(DraftStep("create_draft", False, result.error))
                return result
            result.draft_id = draft_id
            result.steps.append(DraftStep("create_draft", True, draft_id))

            # 2) add_video：逐保留段按源裁切 + 时间轴顺贴
            for index, clip in enumerate(clips):
                duration = clip.get("duration")
                if duration in (None, ""):
                    duration = round(float(clip.get("source_end", 0.0)) - float(clip.get("source_start", 0.0)), 3)
                body = _clean_body(
                    {
                        "video_url": str(clip["url"]),
                        "draft_id": draft_id,
                        "start": float(clip.get("source_start", 0.0)),
                        "end": float(clip.get("source_end", 0.0)),
                        "target_start": float(clip.get("timeline_start", 0.0)),
                        "duration": float(duration or 0.0),
                        "width": int(width),
                        "height": int(height),
                        "track_name": "video_main",
                    }
                )
                resp = await _post(client, base, "/add_video", body)
                ok = bool(resp.get("success"))
                result.steps.append(
                    DraftStep(f"add_video[{index}]", ok, "" if ok else _extract_error(resp))
                )
                if not ok:
                    result.error = f"落正片切段 #{index} 失败：{_extract_error(resp)}"
                    return result

            # 3) add_subtitle（有 SRT 才做）
            if str(srt_path or "").strip():
                resp = await _post(
                    client, base, "/add_subtitle", {"srt": str(srt_path), "draft_id": draft_id}
                )
                ok = bool(resp.get("success"))
                result.steps.append(
                    DraftStep("add_subtitle", ok, "" if ok else _extract_error(resp))
                )
                if not ok:
                    # 字幕失败不致命：正片已落轨，继续 B-roll/BGM，只在报告标注。
                    result.steps.append(DraftStep("add_subtitle_note", True, "字幕导入失败，已跳过"))

            # 4) add_image：B-roll 槽位
            for index, slot in enumerate(broll_images or []):
                image_url = str(slot.get("url") or "").strip()
                if not image_url:
                    continue
                body = _clean_body(
                    {
                        "image_url": image_url,
                        "draft_id": draft_id,
                        "start": float(slot.get("timeline_start", 0.0)),
                        "end": float(slot.get("timeline_end", 0.0)),
                        "width": int(width),
                        "height": int(height),
                        "track_name": "broll",
                    }
                )
                resp = await _post(client, base, "/add_image", body)
                ok = bool(resp.get("success"))
                result.steps.append(
                    DraftStep(f"add_image[{index}]", ok, "" if ok else _extract_error(resp))
                )
                # 单条 B-roll 失败不致命：其余槽位继续，报告里标注即可。

            # 5) add_audio：BGM 垫底
            if str(bgm_path or "").strip():
                body = _clean_body(
                    {
                        "audio_url": str(bgm_path),
                        "draft_id": draft_id,
                        "target_start": 0.0,
                        "duration": float(final_duration or 0.0) or None,
                        "volume": max(0.0, min(1.0, float(bgm_gain))),
                        "track_name": "bgm",
                    }
                )
                resp = await _post(client, base, "/add_audio", body)
                ok = bool(resp.get("success"))
                result.steps.append(
                    DraftStep("add_audio", ok, "" if ok else _extract_error(resp))
                )
                # BGM 失败同样不致命。

            # 6) save_draft（draft_folder 透传「设置→剪映草稿目录」）
            folder = str(draft_folder or "").strip()
            if not folder:
                try:
                    from novelvideo.model_gateway_settings import get_vectcut_draft_dir

                    folder = get_vectcut_draft_dir()
                except Exception:  # noqa: BLE001 - 设置读取失败不阻断保存草稿
                    folder = ""
            save_body: dict[str, Any] = {"draft_id": draft_id, "auto_deploy": True}
            if folder:
                save_body["draft_folder"] = folder
            if project_name:
                save_body["project_name"] = project_name
            save = await _post(client, base, "/aipdd/save_draft", save_body)
            save_ok = bool(save.get("success"))
            result.steps.append(
                DraftStep("save_draft", save_ok, "" if save_ok else _extract_error(save))
            )
            if not save_ok:
                result.error = f"保存草稿失败：{_extract_error(save)}"
                return result

            result.saved = True
            result.ok = True
            return result
    except httpx.HTTPError as exc:
        result.error = f"访问 VectCut 服务失败：{exc}"
        result.hint = (
            f"无法连接 VectCut 服务（{base}）：{exc}。请确认服务已启动。"
        )
        return result
