"""[aipdd fork delta] 一键配齐功能位：把「一次选择」铺开成整链要用的全部绑定。

背景（生产环境实测）：配好渠道 Key、清单也同步了，但 22 个文本功能位 + 4 个视觉理解
位仍是空的 —— 一条整链（故事 → 剧本 → 资产 → 分镜 → 风格）要逐位踩过解析链，任何一位
掉在「未绑定」上就把用户按在报错面前（``Pass 0 出场角色筛选失败: 未绑定可用模型
（DC-identity-cast-planner-LLM（IDENTITY_PLANNER_CAST_MODEL））`` 就是这条）。运行期的
两档兜底（见 :mod:`novelvideo.model_slots`）能接住，但「接住」不等于「用户知道自己
在用哪个模型」：创建剧集的地方就该让人一次挑定，挑完把整链补齐。

于是本模块只做一件事：**把用户选的文本 / 视觉 / 图片 / 视频模型展开成绑定与默认**，
并且严格遵守两条边界。

1. 只写用户已经做过选择的东西，不替他编模型名。每一个落库的名字都必须是**渠道目录里
   真实存在**的（``catalog_names_for_provider`` 裁决），挑不出来就明确报告「这一位没
   配上」，交回运行期兜底 —— 静默写一个网关不认的名字，下场就是 distributor 那句
   ``No available channel``（503，报错来自网关、和用户无关）。
2. 不改调用链、不改网关协议：写的仍是那两个真源（功能位绑定表
   ``custom_newapi_model_alias_mappings``、媒体默认目录
   ``custom_newapi_media_model_mappings``），投影照旧由路由层的
   ``_project_provider_channel_mappings`` 负责。

视觉理解位为什么也要一起配：那 4 位（``FREEZONE_VISION_MODEL`` 等）发的是**带图请求**，
沿用纯文本模型会撞上游「不支持图片能力」的 400。所以先看用户选的文本模型自己能不能
读图，不能就从同一渠道目录里挑首个「视觉理解」候选，再没有就留空报成因 —— 绝不硬塞。
"""

from __future__ import annotations

from typing import Any, Callable

from novelvideo.catalog.media_model_discovery import is_vision_candidate_name
from novelvideo.catalog.upstream_catalog import (
    entry_available,
    supports_vision,
    vision_entries,
)
from novelvideo.model_gateway_settings import (
    catalog_names_for_provider,
    get_newapi_media_model_mappings,
    get_newapi_provider_channels,
    get_upstream_catalog,
    get_upstream_models_snapshot,
    provider_spot_model,
    save_newapi_media_model_mappings,
    save_newapi_model_alias_mappings,
    split_provider_spot_model,
)
from novelvideo.model_slots import (
    FEATURE_SLOTS,
    alias_availability,
    catalog_is_known,
    embedding_slot_config,
)

__all__ = ["FeatureProvisionError", "provision_feature_routes"]

#: 能力 → 该能力的槽位别名集合。去重是必要的：``SCENE_HEADER_AUTOGEN_MODEL`` 与通用文本
#: 位共用一把 ``DC-hermes-LLM``，写两遍没有意义（``FEATURE_SLOTS`` 里是两个 env 一个别名）。
_TEXT_ALIASES: tuple[str, ...] = tuple(
    dict.fromkeys(
        slot.alias for slot in FEATURE_SLOTS.values() if slot.capability == "text"
    )
)
_VISION_ALIASES: tuple[str, ...] = tuple(
    dict.fromkeys(
        slot.alias for slot in FEATURE_SLOTS.values() if slot.capability == "vision"
    )
)


class FeatureProvisionError(ValueError):
    """用户点名的模型不可用（渠道不明 / 不在目录里）。

    继承 ``ValueError`` 是照路由层既有约定：那边把 ``ValueError`` 直接转成 HTTP 400
    中文，而不是把一个注定路由不到的名字塞进绑定表里。
    """


def resolve_call_name(name: str, *, label: str) -> tuple[str, str]:
    """把候选调用名解析成 ``(provider, 上游真名)``；核不过抛 :class:`FeatureProvisionError`。

    候选只有两个来源，都是事实：设置页功能位下拉（``{provider}::{model}`` 复合名）与
    「默认生成模型」目录。裸名只有在**恰好一个渠道**目录里有它时才敢认渠道 —— 拍平语义
    下同名多渠道一律必须由调用方显式带渠道位，这里替用户做一次隐式裁决就是埋雷。
    """

    clean = str(name or "").strip()
    if not clean:
        raise FeatureProvisionError(f"{label}：还没有选择模型")
    provider, target = split_provider_spot_model(clean)
    if not target:
        raise FeatureProvisionError(f"{label}：{clean} 不是一个可用的调用名")
    if not provider:
        owners = [
            str(key or "").strip().lower()
            for key, names in get_upstream_models_snapshot().items()
            if str(key or "").strip() and target in (names or [])
        ]
        if len(owners) > 1:
            raise FeatureProvisionError(
                f"{label}：{target} 同时存在于 {'、'.join(sorted(owners))} 等多个渠道，"
                "请从下拉里选择带渠道的那一项"
            )
        provider = owners[0] if owners else ""
    if not provider:
        if catalog_is_known():
            raise FeatureProvisionError(
                f"{label}：{target} 不在任何渠道的模型清单里，请先在渠道上点「同步模型清单」"
            )
        # 一次都没同步过清单：没有任何裁决依据，只能把用户给的名字原样认下（归属哪个
        # 渠道由他在设置页选的渠道决定）。这不是放行错名，而是此刻没有「错」的证据。
        return "", target
    if catalog_is_known(provider) and target not in catalog_names_for_provider(provider):
        raise FeatureProvisionError(
            f"{label}：{target} 不在渠道 {provider} 的模型清单里（已从渠道下架或尚未开通），"
            "请重新选择"
        )
    return provider, target


def _not_granted_lookup(capability: str) -> Callable[[str, str], bool]:
    """``(provider, model) -> 上游是否明确不可用``；探测子系统不可用时一律放行。"""

    try:
        from novelvideo.text_model_probe import build_not_granted_lookup

        return build_not_granted_lookup(capability)
    except Exception:  # noqa: BLE001 - 实测子系统再不可靠也不能挡配齐
        return lambda *_args: False


def _configured_providers(preferred: str = "") -> list[str]:
    """已配置渠道的 provider 键；排除 comfyui（它没有「目录里的模型名」这回事）。"""

    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 渠道配置读不到就没有候选
        return []
    providers = [
        str(channel.get("provider") or "").strip().lower() for channel in channels
    ]
    providers = [p for p in providers if p and p != "comfyui"]
    clean = str(preferred or "").strip().lower()
    if clean and clean in providers:
        return [clean] + [p for p in providers if p != clean]
    return providers


def _first_vision_call_name(preferred_provider: str = "") -> str:
    """渠道目录里第一个「视觉理解」调用名：先同渠道，再按渠道登记顺序找其余渠道。

    口径与 ``model_slots`` 的第二档兜底一致（只认目录声明、跳过上游明确没开通的名字），
    差别是这里要落库，所以必须给出**具体**名字，而不是让运行期每次调用各自兜一次。
    """

    not_granted = _not_granted_lookup("vision")
    for provider in _configured_providers(preferred_provider):
        try:
            entries = vision_entries(get_upstream_catalog(provider))
        except Exception:  # noqa: BLE001 - 单渠道目录读失败不影响其余渠道
            continue
        for name in sorted(
            {
                str(entry.get("name") or "").strip()
                for entry in entries
                if str(entry.get("name") or "").strip()
            }
        ):
            if not_granted(provider, name):
                continue
            return provider_spot_model(provider, name)
    return ""


def _is_vision_capable(provider: str, target: str) -> bool:
    """这一位能不能读图：按渠道目录声明判；渠道 / 条目不明时退回名字推断。"""

    if provider and target:
        for entry in get_upstream_catalog(provider) or []:
            if str(entry.get("name") or "").strip() == target:
                return bool(entry_available(entry) and supports_vision(entry))
    return bool(is_vision_candidate_name(target))


def _media_entry_key(mappings: dict[str, Any], media_type: str, model: str) -> str:
    """在媒体默认目录里定位用户选的那一条：条目 id / 上游真名 / ``newapi_`` 前缀调用名。"""

    clean = str(model or "").strip()
    if not clean:
        return ""
    wanted = str(media_type or "").strip().lower()
    item = mappings.get(clean)
    if isinstance(item, dict) and str(item.get("mediaType") or "").strip().lower() == wanted:
        return clean
    for name, entry in mappings.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("mediaType") or "").strip().lower() != wanted:
            continue
        upstream = str(entry.get("upstreamModel") or "").strip()
        if clean in (upstream, f"newapi_{name}"):
            return str(name)
    return ""


def _apply_media_default(media_type: str, model: str) -> dict[str, Any]:
    """把选中的媒体模型顶成该类型默认（``sortOrder`` 最小）。

    默认的判定真源是「同类型已启用、``sortOrder`` 最小」（
    ``model_gateway_settings._default_media_model_from_mappings``），所以这里只调
    ``sortOrder``：不新增条目、不动别人的 ``enabled`` —— 条目本身仍归设置页「默认生成
    模型」面板管，配齐只回答「用哪一条」。同类型其余条目一律抬到 1 以上，否则并列时
    默认会按名字排序飘走。
    """

    result: dict[str, Any] = {"model": "", "ok": False, "hint": ""}
    clean = str(model or "").strip()
    if not clean:
        result["hint"] = "未选择，保持现状"
        return result
    mappings = get_newapi_media_model_mappings()
    key = _media_entry_key(mappings, media_type, clean)
    if not key:
        result["hint"] = (
            f"{clean} 不在「默认生成模型」的{media_type}目录里："
            "先到设置页把该渠道的这个模型加进目录，再来配默认"
        )
        return result
    rewritten: dict[str, Any] = {
        name: dict(entry) for name, entry in mappings.items() if isinstance(entry, dict)
    }
    chosen = dict(rewritten[key])
    chosen["sortOrder"] = 0
    rewritten[key] = chosen
    chosen_type = str(chosen.get("mediaType") or "").strip().lower()
    for name, entry in rewritten.items():
        if name == key:
            continue
        if str(entry.get("mediaType") or "").strip().lower() != chosen_type:
            continue
        try:
            order = int(entry.get("sortOrder") or 100)
        except (TypeError, ValueError):
            order = 100
        entry["sortOrder"] = max(order, 1)
    save_newapi_media_model_mappings(rewritten)
    result["model"] = key
    result["ok"] = True
    return result


def _bind_aliases(
    aliases: tuple[str, ...],
    provider: str,
    target: str,
    *,
    capability: str,
    only_missing: bool = False,
) -> tuple[list[str], int]:
    """把这批槽位别名指向 ``(provider, target)``；回 ``(新写别名, 保留不动的条数)``。

    ``only_missing=True`` 是「只填缺口」：已经绑好且目标还在目录里的位一律不碰。这一档
    是给「系统替用户挑的」名字用的（用户没做过这个选择，不该拿它去覆盖他手动绑过的）；
    用户显式选的那一类才走全量覆盖。
    """

    entries: dict[str, dict[str, Any]] = {}
    preserved = 0
    for alias in aliases:
        if only_missing:
            usable, _reason, _bound = alias_availability(alias)
            if usable:
                preserved += 1
                continue
        entries[alias] = {
            "provider": provider,
            "target": target,
            "capability": capability,
        }
    if entries:
        save_newapi_model_alias_mappings(entries, merge=True)
    return sorted(entries), preserved


def provision_feature_routes(
    *,
    text_model: str = "",
    vision_model: str = "",
    image_model: str = "",
    video_model: str = "",
) -> dict[str, Any]:
    """把一次选择展开成功能位绑定 + 媒体默认，逐项回报（「挑不到」不抛错）。

    任何一项为空就是「这一类不动」：整链在运行期仍由解析链接住（未绑定的位自动跟随同类
    已绑的模型 / 渠道目录首项），所以这里绝不为「用户没选」编一个名字。

    返回形状（路由与前端直接展示）::

        {
          "text":   {"model": "aipdd::glm-4.6", "provider": "aipdd", "target": "glm-4.6",
                     "slots": [...], "bound": 21, "hint": ""},
          "vision": {..., "source": "explicit|same-as-text|catalog", "preserved": 2},
          "image":  {"model": ..., "ok": bool, "hint": ...},
          "video":  {...},
          "hints":  [...],           # 需要用户去设置的成因（向量位没绑等）
          "touched": ["aipdd", ...], # 需要重新投影到网关的渠道
        }
    """

    hints: list[str] = []
    touched: set[str] = set()
    out: dict[str, Any] = {"text": None, "vision": None, "image": None, "video": None}

    text_provider = text_target = ""
    clean_text = str(text_model or "").strip()
    if clean_text:
        text_provider, text_target = resolve_call_name(clean_text, label="文本模型")
        if text_provider:
            bound, _preserved = _bind_aliases(
                _TEXT_ALIASES, text_provider, text_target, capability="text"
            )
            touched.add(text_provider)
            hint = ""
        else:
            # 渠道不明（清单从没同步过）：绑定表要求每条都带 provider，这里既不能编
            # 渠道也不能只写真名 —— 报成因并把这一类交回运行期兜底，比写坏真源更好。
            bound = []
            hint = "渠道清单还没同步过：认不出这个模型属于哪个渠道，请先同步渠道目录"
        out["text"] = {
            "model": provider_spot_model(text_provider, text_target)
            if text_provider
            else text_target,
            "provider": text_provider,
            "target": text_target,
            "slots": bound,
            "bound": len(bound),
            "hint": hint,
        }
        if hint:
            hints.append(hint)

    vision = _plan_vision(
        text_provider=text_provider,
        text_target=text_target,
        vision_model=vision_model,
    )
    if vision is not None:
        out["vision"] = vision["row"]
        if vision["provider"]:
            touched.add(vision["provider"])
        if vision["hint"]:
            hints.append(vision["hint"])

    if str(image_model or "").strip():
        out["image"] = _apply_media_default("image", image_model)
        if out["image"]["hint"]:
            hints.append(str(out["image"]["hint"]))
    if str(video_model or "").strip():
        out["video"] = _apply_media_default("video", video_model)
        if out["video"]["hint"]:
            hints.append(str(out["video"]["hint"]))

    hints.extend(_embedding_health_hints())

    out["hints"] = hints
    out["touched"] = sorted(touched - {""})
    return out


def _plan_vision(
    *,
    text_provider: str,
    text_target: str,
    vision_model: str,
) -> dict[str, Any] | None:
    """决定视觉理解位落在哪：用户显式选的 > 文本模型自己能读图 > 目录首个视觉候选。

    没动这一类（既没选文本模型、文本模型也认不出渠道、又没显式视觉模型）时回 ``None``，
    结果里不留 ``vision`` 行 —— 「没做的事」不该长得像「做过但失败的事」。
    """

    clean = str(vision_model or "").strip()
    provider = target = ""
    call_name = ""
    source = ""
    if clean:
        provider, target = resolve_call_name(clean, label="视觉模型")
        call_name = provider_spot_model(provider, target) if provider else target
        source = "explicit"
    elif text_target and text_provider:
        if _is_vision_capable(text_provider, text_target):
            provider, target = text_provider, text_target
            call_name = provider_spot_model(provider, target)
            source = "same-as-text"
        else:
            found = _first_vision_call_name(text_provider)
            if found:
                provider, target = split_provider_spot_model(found)
                call_name, source = found, "catalog"
    elif text_target:
        # 文本模型连渠道都没认出来：这一类同样没法配（不写没有 provider 的绑定）。
        hint_text = "渠道清单还没同步过，视觉理解位保持现状"
        return {
            "row": {
                "model": "",
                "provider": "",
                "target": "",
                "source": "",
                "slots": [],
                "bound": 0,
                "preserved": 0,
                "hint": hint_text,
            },
            "provider": "",
            "hint": hint_text,
        }
    else:
        return None

    if not call_name or not provider:
        hint_text = "渠道目录里没有能读图的模型：视觉理解位保持现状，请确认渠道已开通视觉模型"
        row = {
            "model": "",
            "provider": "",
            "target": "",
            "source": source,
            "slots": [],
            "bound": 0,
            "preserved": 0,
            "hint": hint_text,
        }
        return {"row": row, "provider": "", "hint": hint_text}

    # 替用户挑的名字（``catalog``）只填缺口；他自己选的（显式视觉模型 / 能读图的
    # 文本模型）才覆盖全数 —— 拿一个没人挑过的模型去冲掉手动绑定，是下一句「为什么我
    # 设的模型被改了」的报错在等着。
    bound, preserved = _bind_aliases(
        _VISION_ALIASES,
        provider,
        target,
        capability="vision",
        only_missing=(source == "catalog"),
    )
    row = {
        "model": call_name,
        "provider": provider,
        "target": target,
        "source": source,
        "slots": bound,
        "bound": len(bound),
        "preserved": preserved,
        "hint": "",
    }
    return {"row": row, "provider": provider, "hint": ""}


def _embedding_health_hints() -> list[str]:
    """向量位没绑 / 绑的名字已下架就明确提示，**只提示不动手**。

    这一位的真源不是功能位绑定表，而是独立的「向量模型」配置（provider + 上游真名 +
    **维度**，见 ``model_slots.embedding_slot_config``）。维度只能由用户或渠道声明给出，
    代码里没有可编的维度：替用户挑一个向量模型并把维度猜错，知识图谱的写入会直接坏掉
    （存量向量与新维度不可比）。这正是「一键配齐」里唯一必须留给人的一类。
    """

    try:
        config = embedding_slot_config()
    except Exception:  # noqa: BLE001 - 提示路径绝不能把配齐打挂
        return []
    provider = str(config.get("provider") or "").strip().lower()
    upstream = str(config.get("upstreamModel") or "").strip()
    if not provider or not upstream:
        return ["向量模型（知识图谱检索用）还没绑定：请到「设置 → 模型与渠道」的「向量化与语义检索（Embedding）」区块选择并填写维度。"]
    if catalog_is_known(provider) and upstream not in catalog_names_for_provider(provider):
        return ["向量模型原来的绑定已从渠道下架：请到「设置 → 模型与渠道」的「向量化与语义检索（Embedding）」区块重新选择。"]
    return []
