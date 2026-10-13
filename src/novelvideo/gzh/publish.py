# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号发布（官方 API：草稿箱 / 可选直发），自研实现。

对微信开放平台的公开接口做最小封装：access_token（本地缓存规避每日限额）→ 封面
永久素材 → ``draft/add`` 建草稿 → 可选 ``freepublish/submit`` 直发。**凭证由用户自行
配置，未配置不阻塞排版**——返回 ``needs_config`` 状态与「去公众号后台获取 AppID/Secret
+ 配 IP 白名单」的操作指引，交给上层展示。

凭证优先级：显式参数 / 项目 ``config.json`` > ``~/.wechat_publish.json`` > 环境变量
``WECHAT_APPID`` / ``WECHAT_SECRET``。secret 绝不写日志、绝不入返回体。

错误码 ``48001``（未认证不支持直发）走「API 已建草稿 + 后台手动发布」半自动说明。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.weixin.qq.com"
TOKEN_CACHE = os.path.expanduser("~/.wechat_publish_token.json")
HOME_CONFIG = os.path.expanduser("~/.wechat_publish.json")
# token 提前 5 分钟视为过期，避免边界用上刚失效的 token。
_TOKEN_MARGIN = 300

# 已知错误码 → 处置提示；之外的错误按「接口异常」透出。
_KNOWN_ERRORS: dict[int, str] = {
    40001: "access_token 失效或 AppSecret 不对，请核对凭证。",
    40013: "AppID 不合法，请核对公众号后台「基本配置」里的 AppID。",
    40125: "AppSecret 无效，请到后台重新生成后填写。",
    40164: "调用方 IP 不在白名单：请到后台「设置与开发-基本配置」把服务器出口 IP 加入白名单。",
    41001: "缺少 access_token 参数。",
    45009: "接口调用超过每日限额，请稍后再试。",
    48001: "该公众号未认证或接口未开放直发：已尽力创建草稿，请到后台「草稿箱」手动发布。",
    53503: "仅限测试号或权限不足。",
}

SETUP_GUIDE = (
    "公众号发布需先在公众号侧完成配置："
    "①后台「设置与开发-基本配置」获取 AppID / AppSecret；"
    "②把本机出口 IP 加入 IP 白名单；"
    "③发布图文需认证服务号，未认证只能 API 建草稿后到后台手动发布。"
    "凭证填写位置：项目 config.json、~/.wechat_publish.json 或环境变量 "
    "WECHAT_APPID / WECHAT_SECRET。"
)


class WechatApiError(RuntimeError):
    def __init__(self, code: int, msg: str, api: str) -> None:
        self.code = code
        self.msg = msg
        self.api = api
        super().__init__(f"[{api}] errcode={code} {msg}")


@dataclass(slots=True)
class WechatCredentials:
    appid: str
    secret: str
    source: str


@dataclass(slots=True)
class PublishOutcome:
    status: str  # published | draft | needs_config | dry_run | error
    message: str = ""
    draft_media_id: str = ""
    publish_id: str = ""
    article_url: str = ""
    guidance: list[str] = field(default_factory=list)
    error_code: int | None = None


def resolve_credentials(
    *,
    project_dir: Path | None = None,
    appid: str = "",
    secret: str = "",
) -> WechatCredentials | None:
    """按优先级解析凭证：显式参数 > 项目 config.json > ~/.wechat_publish.json > env。"""
    appid_clean = str(appid or "").strip()
    secret_clean = str(secret or "").strip()
    if appid_clean and secret_clean:
        return WechatCredentials(appid_clean, secret_clean, "显式参数")

    candidates: list[Path] = []
    if project_dir is not None:
        candidates.append(Path(project_dir) / "config.json")
    candidates.append(Path(HOME_CONFIG))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        a = str(data.get("appid", "") or "").strip()
        s = str(data.get("secret", "") or "").strip()
        if a and s:
            return WechatCredentials(a, s, str(path))

    env_a = os.environ.get("WECHAT_APPID", "").strip()
    env_s = os.environ.get("WECHAT_SECRET", "").strip()
    if env_a and env_s:
        return WechatCredentials(env_a, env_s, "环境变量")
    return None


def _read_cached_token(appid: str) -> str | None:
    if not os.path.isfile(TOKEN_CACHE):
        return None
    try:
        cached = json.loads(Path(TOKEN_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if cached.get("appid") == appid and float(cached.get("expire_at", 0)) > (
        time.time() + _TOKEN_MARGIN
    ):
        return str(cached.get("token") or "") or None
    return None


def _write_cached_token(appid: str, token: str, expire_at: float) -> None:
    try:
        Path(TOKEN_CACHE).write_text(
            json.dumps({"appid": appid, "token": token, "expire_at": expire_at}),
            encoding="utf-8",
        )
        os.chmod(TOKEN_CACHE, 0o600)
    except OSError:
        pass  # 缓存写失败不影响本次发布


async def _get_access_token(client: httpx.AsyncClient, creds: WechatCredentials) -> str:
    cached = _read_cached_token(creds.appid)
    if cached:
        return cached
    resp = await client.get(
        f"{API_BASE}/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": creds.appid,
            "secret": creds.secret,
        },
    )
    data = resp.json()
    if data.get("errcode"):
        raise WechatApiError(int(data["errcode"]), data.get("errmsg", ""), "cgi-bin/token")
    token = data["access_token"]
    expire_at = time.time() + float(data.get("expires_in", 7200))
    _write_cached_token(creds.appid, token, expire_at)
    return token


async def _upload_thumb(
    client: httpx.AsyncClient, token: str, img_path: Path
) -> str:
    if not img_path.is_file():
        raise FileNotFoundError(f"封面图不存在: {img_path}")
    files = {"media": (img_path.name, img_path.read_bytes(), "application/octet-stream")}
    resp = await client.post(
        f"{API_BASE}/cgi-bin/material/add_material",
        params={"access_token": token, "type": "image"},
        files=files,
    )
    data = resp.json()
    if data.get("errcode"):
        raise WechatApiError(
            int(data["errcode"]), data.get("errmsg", ""), "material/add_material"
        )
    return str(data["media_id"])


def _build_article(
    *, content: str, title: str, author: str, digest: str, thumb_media_id: str
) -> dict[str, Any]:
    plain = _strip_tags(content)
    return {
        "title": title or (plain[:24] if plain else "未命名文章"),
        "author": author or "",
        "digest": digest or plain[:120],
        "content": content,
        "content_source_url": "",
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }


def _strip_tags(html_text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", str(html_text or "")).strip()


async def publish_article(
    html: str,
    *,
    title: str = "",
    author: str = "",
    digest: str = "",
    thumb_path: str | Path | None = None,
    project_dir: Path | None = None,
    appid: str = "",
    secret: str = "",
    do_publish: bool = False,
    dry_run: bool = False,
) -> PublishOutcome:
    """把公众号 HTML 发到草稿箱（可选直发）。凭证缺失 → ``needs_config``（不抛错）。"""
    creds = resolve_credentials(project_dir=project_dir, appid=appid, secret=secret)
    if creds is None:
        return PublishOutcome(
            status="needs_config",
            message="未检测到公众号凭证，已跳过发布（排版结果不受影响）。",
            guidance=[SETUP_GUIDE],
        )

    if dry_run:
        return PublishOutcome(
            status="dry_run",
            message="dry-run：仅预览，未发送任何真实请求。",
            guidance=["正式发布需 --thumb 封面图（官方要求图文草稿封面为永久素材）。"]
            if not thumb_path
            else [],
        )

    thumb = Path(thumb_path) if thumb_path else None
    if thumb is None:
        return PublishOutcome(
            status="error",
            message="图文草稿封面必填：请提供封面图（会自动上传为永久素材）。",
            guidance=[SETUP_GUIDE],
            error_code=None,
        )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            token = await _get_access_token(client, creds)
            thumb_media_id = await _upload_thumb(client, token, thumb)
            article = _build_article(
                content=html,
                title=title,
                author=author,
                digest=digest,
                thumb_media_id=thumb_media_id,
            )
            resp = await client.post(
                f"{API_BASE}/cgi-bin/draft/add",
                params={"access_token": token},
                json={"articles": [article]},
            )
            data = resp.json()
            if data.get("errcode"):
                code = int(data["errcode"])
                raise WechatApiError(code, data.get("errmsg", ""), "draft/add")
            draft_media_id = str(data["media_id"])

            if not do_publish:
                return PublishOutcome(
                    status="draft",
                    message="草稿已创建：请到公众号后台「草稿箱」核对标题/摘要/封面后发布。",
                    draft_media_id=draft_media_id,
                )

            resp = await client.post(
                f"{API_BASE}/cgi-bin/freepublish/submit",
                params={"access_token": token},
                json={"media_id": draft_media_id},
            )
            data = resp.json()
            if data.get("errcode"):
                code = int(data["errcode"])
                raise WechatApiError(code, data.get("errmsg", ""), "freepublish/submit")
            publish_id = str(data.get("publish_id", ""))
            return PublishOutcome(
                status="published",
                message="已提交发布（提交成功≠发布完成，审核/原创声明仍可能影响结果）。",
                draft_media_id=draft_media_id,
                publish_id=publish_id,
            )
    except WechatApiError as exc:
        hint = _KNOWN_ERRORS.get(
            exc.code, "接口返回异常，请到公众号后台核对凭证、IP 白名单与接口权限。"
        )
        # 48001：草稿可能已建，提示半自动发布。
        if exc.code == 48001:
            return PublishOutcome(
                status="draft",
                message=hint,
                guidance=[SETUP_GUIDE],
                error_code=exc.code,
            )
        return PublishOutcome(
            status="error",
            message=f"{exc.api} 调用失败：{exc.msg}",
            guidance=[hint, SETUP_GUIDE],
            error_code=exc.code,
        )
    except httpx.HTTPError as exc:
        return PublishOutcome(
            status="error",
            message=f"网络请求失败：{exc}",
            guidance=[SETUP_GUIDE],
        )


__all__ = [
    "publish_article",
    "resolve_credentials",
    "PublishOutcome",
    "WechatCredentials",
    "WechatApiError",
    "SETUP_GUIDE",
]
