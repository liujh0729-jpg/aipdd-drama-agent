# [aipdd fork delta req40] 目录驱动模型配置：渠道有什么模型，平台不预先知道，只能拉了
# 才知道。本文件钉住改造后的四条硬口径：
#
# 1. **目录是唯一真源**：上游 ``/v1/models`` 的元数据（kind / endpoint / modalities /
#    embedding_dimensions / available）必须被结构化吸收；``available=false`` 直接丢弃；
#    没有元数据的通用渠道才落成 ``inferred`` 条目供关键词兜底。
# 2. **配置不能制造模型**：注册表 / 编译表只从目录取名，功能位把已下架名字写成 target
#    时既不注册也不编译 —— 这正是「从网关彻底摘掉还会报错」的回归用例。
# 3. **失效绑定自动取消**：目录刷新后剪掉指向已消失模型的绑定，并回传 ``{槽位: [名字]}``；
#    拉取失败时**不动**快照也不剪（一次抖动不该取消用户全部默认）。
# 4. **解析不到就前置拒绝**：``resolve_slot`` 三层来源（单独 env → 兜底 env → 目录内
#    绑定）都落空时，先用两档自动兜底接住（同类功能位里用户已绑过的那个 → 渠道目录
#    首项的复合名），再兜不出名字才抛 :class:`ModelSlotNotConfigured`，API 出口转
#    HTTP 400 中文。未绑定的 ``DC-*`` 槽位键在任何情况下都不会被直接发给网关。
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from novelvideo import config, newapi_provisioner
from novelvideo.model_gateway_settings import (
    MODE_CUSTOM,
    _decode_upstream_models_snapshot,
    build_channel_model_mapping,
    build_channel_registrations,
    catalog_names_for_provider,
    get_newapi_embedding_model_config,
    get_newapi_media_model_mappings,
    get_newapi_model_alias_mappings,
    get_upstream_catalog,
    get_upstream_catalog_meta,
    get_upstream_models_snapshot,
    prune_stale_bindings,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_newapi_model_alias_mappings,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
    set_model_gateway_mode,
    upstream_catalog_entry,
)
from novelvideo.model_slots import (
    REASON_CATALOG_EMPTY,
    REASON_DELISTED,
    REASON_NOT_CONFIGURED,
    REASON_UNBOUND,
    ModelSlotNotConfigured,
    alias_availability,
    auto_fallback_call_name,
    describe_slots,
    resolve_media_slot,
    resolve_slot,
)
from novelvideo.catalog.upstream_catalog import (
    EP_AUDIO_TRANSCRIPTIONS,
    MEDIA_ASR,
    MEDIA_AUDIO,
    MEDIA_DECISION,
    MEDIA_EMBEDDING,
    MEDIA_IMAGE,
    MEDIA_TEXT,
    coerce_entry,
    declares_endpoint,
    dimensions_for,
    endpoint_for,
    parse_catalog_response,
    supports_asr,
    supports_vision,
    vision_entries,
)

PROVIDER = "aipdd"
BASE_URL = "https://api.aipdd.example/v1"

# 上游真实形态抽样：带 ``aipdd`` 元数据块（AIPDD 网关），外加一个只回名字的通用条目。
UPSTREAM_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "ap-live-llm",
            "aipdd": {
                "kind": "llm",
                "endpoint": ["/v1/chat/completions"],
                "display_name": "在线文本位",
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
        {
            # 名字里有 vision 但渠道声明只吃文本：关键词会误判，声明必须压过它。
            "id": "ap-text-only-but-looks-vision",
            "aipdd": {
                "kind": "llm",
                "endpoint": "/v1/chat/completions",
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
        {
            "id": "ap-embed",
            "aipdd": {
                "kind": "embedding",
                "endpoint": "/v1/embeddings",
                "embedding_dimensions": 1536,
            },
        },
        {
            "id": "ap-asr",
            "aipdd": {"kind": "audio", "endpoint": ["/v1/audio/transcriptions"]},
        },
        {
            "id": "ap-tts",
            "aipdd": {"kind": "audio", "endpoint": ["/v1/audio/speech"]},
        },
        {
            "id": "ap-decision",
            "aipdd": {"kind": "decision", "endpoint": ["/v1/systemone"]},
        },
        {
            "id": "ap-image",
            "aipdd": {
                "kind": "image",
                "endpoint": ["https://api.aipdd.example/v1/images/edits"],
                "capabilities": {
                    "protocols": ["openai"],
                    "operations": ["edit"],
                    "media_metadata": {"resolutions": ["2K"]},
                },
            },
        },
        {
            # 渠道已下架：available=false 的条目不进目录（进了就是可被选中的死名）。
            "id": "ap-delisted",
            "aipdd": {"kind": "llm", "available": False},
        },
        {
            # 通用 OpenAI 兼容形态：无元数据 → inferred 条目，由调用方退回关键词推断。
            "id": "plain-name-no-metadata",
        },
    ]
}


@pytest.fixture(autouse=True)
def _ce_local_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """CE + 本地设置库：目录 / 绑定 / 槽位判定全部走自有存储，零远程。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    # [aipdd fork delta] 解析链已完全不读进程环境；这里继续清一遍只是防御：万一宿主
    # 环境里有同名变量，也不让它在别的读取点（媒体映射等）里污染用例。
    for key in (
        "HERMES_MODEL",
        "COGNEE_LLM_MODEL",
        "FREEZONE_TRANSLATION_MODEL",
        "TEXT_MODEL_FALLBACK",
        "VISION_MODEL_FALLBACK",
        "NEWAPI_IMAGE_MODEL",
        "NEWAPI_VIDEO_MODEL",
        "NEWAPI_AUDIO_MODEL",
        "NEWAPI_ASR_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    set_model_gateway_mode(MODE_CUSTOM)
    save_newapi_provider_channels(
        [{"provider": PROVIDER, "baseUrl": BASE_URL, "upstreamKey": "sk-secret"}]
    )


def _seed_catalog(provider: str = PROVIDER, names: list[str] | None = None) -> None:
    """把目录灌成结构化条目（走的是生产同一个写入口，含归一化）。"""
    entries = parse_catalog_response(UPSTREAM_PAYLOAD)
    if names is not None:
        entries = [{"name": name, "kind": "llm"} for name in names]
    save_upstream_models_snapshot(provider, entries, base_url=BASE_URL)


def _entry(name: str) -> dict[str, Any]:
    return next(item for item in parse_catalog_response(UPSTREAM_PAYLOAD)
                if item["name"] == name)


# --------------------------------------------------------------------------- #
# §1 结构化目录
# --------------------------------------------------------------------------- #


def test_catalog_absorbs_declared_metadata_and_drops_delisted() -> None:
    entries = parse_catalog_response(UPSTREAM_PAYLOAD)
    names = [str(item["name"]) for item in entries]

    assert "ap-delisted" not in names  # available=false 不入库
    assert names == sorted(names)  # 归一后按名字排序，快照与候选都稳定
    by_name = {str(item["name"]): item for item in entries}

    llm = by_name["ap-live-llm"]
    assert llm["kind"] == "llm"
    assert llm["mediaType"] == MEDIA_TEXT
    assert llm["endpoints"] == ["chat/completions"]  # /v1 前缀已剥掉
    assert llm["inputModalities"] == ["text", "image"]
    assert llm["displayName"] == "在线文本位"
    assert llm["inferred"] is False  # 渠道自己声明过，不是猜的

    embedding = by_name["ap-embed"]
    assert embedding["mediaType"] == MEDIA_EMBEDDING
    assert dimensions_for(embedding) == 1536

    # audio 一个 kind 里合成与转写共存，靠端点分流。
    assert by_name["ap-asr"]["mediaType"] == MEDIA_ASR
    assert by_name["ap-tts"]["mediaType"] == MEDIA_AUDIO
    assert by_name["ap-decision"]["mediaType"] == MEDIA_DECISION

    image = by_name["ap-image"]
    assert image["mediaType"] == MEDIA_IMAGE
    assert image["endpoints"] == ["images/edits"]  # 绝对 URL 只取相对路径
    assert image["protocols"] == ["openai"]
    assert image["operations"] == ["edit"]
    assert image["mediaMetadata"] == {"resolutions": ["2K"]}

    plain = by_name["plain-name-no-metadata"]
    assert plain["inferred"] is True
    assert plain["kind"] == ""
    assert plain["endpoints"] == []


def test_declared_modalities_outrank_name_keyword_guess() -> None:
    """遗留项「视觉能力位关键词漏网面」的收口：渠道声明优先，名字里带 vision 不算。"""
    declared_text_only = _entry("ap-text-only-but-looks-vision")
    assert supports_vision(declared_text_only) is False
    assert supports_vision(_entry("ap-live-llm")) is True

    entries = parse_catalog_response(UPSTREAM_PAYLOAD)
    assert [item["name"] for item in vision_entries(entries)] == ["ap-live-llm"]

    # 无元数据的条目没有声明可依：调用方按 inferred 自行决定是否退回关键词。
    assert _entry("plain-name-no-metadata")["inferred"] is True


def test_endpoint_lookup_prefers_declared_purpose() -> None:
    image = _entry("ap-image")
    assert endpoint_for(image, "images/edits") == "images/edits"
    assert endpoint_for(image, "images/generations") == "images/edits"  # 回首个
    assert declares_endpoint(image, "images/edits") is True
    assert declares_endpoint(image, "videos") is False

    assert supports_asr(_entry("ap-asr")) is True
    assert supports_asr(_entry("ap-tts")) is False  # 只有合成端点，不能当转写用
    assert declares_endpoint(_entry("ap-tts"), EP_AUDIO_TRANSCRIPTIONS) is False


def test_coerce_entry_is_idempotent_for_normalized_entries() -> None:
    """快照读回来必须原样保留声明：再解析一次不许把元数据抹平成「按名字猜」。"""
    once = _entry("ap-image")
    twice = coerce_entry(once)
    assert twice == once

    assert coerce_entry({"name": ""}) is None
    assert coerce_entry({"mediaType": MEDIA_TEXT, "available": False}) is None
    assert coerce_entry("  ") is None


def test_legacy_name_only_snapshot_migrates_on_read() -> None:
    """旧格式（``models: ["名字"]``）就地补成推断条目：升级即清空目录是不可接受的。"""
    snapshot = _decode_upstream_models_snapshot(
        json.dumps({"aipdd": ["glm-4.6", "doubao-embedding"]})
    )
    entries = snapshot["aipdd"]["models"]
    assert [str(item["name"]) for item in entries] == ["doubao-embedding", "glm-4.6"]
    assert all(bool(item["inferred"]) for item in entries)

    # 更旧的形态：{provider: [...]} 直接就是数组。
    assert _decode_upstream_models_snapshot('{"newapi": ["a-model"]}')["newapi"][
        "models"
    ][0]["name"] == "a-model"
    assert _decode_upstream_models_snapshot("not-json") == {}
    assert _decode_upstream_models_snapshot('{"aipdd": {"models": []}}') == {}


def test_snapshot_overwrites_wholesale_per_provider() -> None:
    save_upstream_models_snapshot(PROVIDER, [{"name": "old-one"}, {"name": "shared"}])
    save_upstream_models_snapshot("newapi", [{"name": "other-provider-model"}])
    save_upstream_models_snapshot(
        PROVIDER, [{"name": "shared"}, {"name": "new-one"}], base_url=BASE_URL
    )

    assert catalog_names_for_provider(PROVIDER) == {"new-one", "shared"}
    # 别的渠道不受本次覆盖影响。
    assert catalog_names_for_provider("newapi") == {"other-provider-model"}
    meta = get_upstream_catalog_meta()[PROVIDER]
    assert meta["baseUrl"] == "https://api.aipdd.example/v1".rstrip("/")
    assert meta["count"] == 2
    assert meta["fetchedAt"]
    assert get_upstream_models_snapshot()[PROVIDER] == ["new-one", "shared"]
    assert get_upstream_catalog(PROVIDER)[0]["inferred"] is True


# --------------------------------------------------------------------------- #
# §2/§3 配置不制造模型 + 失效绑定自动取消
# --------------------------------------------------------------------------- #


def test_delisted_name_is_not_registered_even_when_still_bound() -> None:
    """回归 ``ap-doubao-seed-2.1-turbo``：配置把已摘模型写成 target 也不许复活。"""
    _seed_catalog()
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-delisted-model"}},
        merge=True,
    )
    save_newapi_media_model_mappings(
        {
            "dead-image": {
                "provider": PROVIDER,
                "upstreamModel": "ap-delisted-model",
                "mediaType": "image",
            }
        }
    )

    registrations = build_channel_registrations(PROVIDER)
    assert "ap-delisted-model" not in registrations.values()
    assert f"{PROVIDER}::ap-delisted-model" not in registrations
    # 目录里的名字照常注册（复合名全量 + 裸名单渠道唯一）。
    assert registrations[f"{PROVIDER}::ap-live-llm"] == "ap-live-llm"


def test_channel_mapping_skips_targets_outside_catalog() -> None:
    _seed_catalog()
    save_newapi_model_alias_mappings(
        {
            "DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"},
            "DC-cognee-LLM": {"provider": PROVIDER, "target": "ghost-model"},
        },
        merge=True,
    )
    mapping = build_channel_model_mapping(PROVIDER)
    assert mapping["DC-hermes-LLM"] == "ap-live-llm"
    assert "DC-cognee-LLM" not in mapping


def test_channel_mapping_does_not_adjudicate_without_catalog() -> None:
    """没同步过目录 ≠ 渠道里没有：不裁决，否则 comfyui 这类渠道会被打成不可用。"""
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "whatever"}}, merge=True
    )
    assert build_channel_model_mapping(PROVIDER) == {"DC-hermes-LLM": "whatever"}


def test_prune_stale_bindings_unbinds_every_layer() -> None:
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {
            "DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-gone-llm"},
            "DC-freezone-translator-LLM": {
                "provider": PROVIDER,
                "target": "ap-live-llm",
            },
        },
        merge=True,
    )
    save_newapi_media_model_mappings(
        {
            "gone-image": {
                "provider": PROVIDER,
                "upstreamModel": "ap-gone-image",
                "mediaType": "image",
            },
            "live-image": {
                "provider": PROVIDER,
                "upstreamModel": "ap-live-llm",
                "mediaType": "image",
            },
        }
    )
    save_newapi_embedding_model_config(
        provider=PROVIDER, upstream_model="ap-gone-embed", dimension=1024
    )

    removed = prune_stale_bindings(PROVIDER)

    assert removed == {
        "DC-hermes-LLM": ["ap-gone-llm"],
        "media:image": ["ap-gone-image"],
        "embedding": ["ap-gone-embed"],
    }
    aliases = get_newapi_model_alias_mappings()
    assert "DC-hermes-LLM" not in aliases  # entry 删掉，槽位回到未绑定
    assert aliases["DC-freezone-translator-LLM"]["target"] == "ap-live-llm"
    assert "gone-image" not in get_newapi_media_model_mappings()
    assert "live-image" in get_newapi_media_model_mappings()
    assert get_newapi_embedding_model_config() == {}


def test_prune_is_a_no_op_without_catalog_for_provider() -> None:
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-anything"}}, merge=True
    )
    assert prune_stale_bindings(PROVIDER) == {}
    assert "DC-hermes-LLM" in get_newapi_model_alias_mappings()
    # 别的渠道的绑定不归本次裁决。
    _seed_catalog(names=["ap-live-llm"])
    assert prune_stale_bindings("newapi") == {}
    assert get_newapi_model_alias_mappings()["DC-hermes-LLM"]["target"] == "ap-anything"


def test_refresh_provider_catalog_overwrites_prunes_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_catalog(names=["ap-old-model"])
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-old-model"}}, merge=True
    )
    # 打在最底层的拉取实现上：生产里「成功拉到 = 顺手整体覆盖快照」就发生在这里。
    def _fake_fetch(
        provider: str, *, base_url: str = "", **_kwargs: Any
    ) -> tuple[list[dict[str, Any]], str]:
        entries = [{"name": "ap-new-model", "kind": "llm"}]
        save_upstream_models_snapshot(provider, entries, base_url=base_url)
        return [dict(entry) for entry in entries], ""

    monkeypatch.setattr(newapi_provisioner, "list_upstream_catalog", _fake_fetch)

    result = newapi_provisioner.refresh_provider_catalog(PROVIDER, reproject=False)

    assert result["ok"] is True
    assert not result["error"]
    assert catalog_names_for_provider(PROVIDER) == {"ap-new-model"}
    assert result["pruned"] == {"DC-hermes-LLM": ["ap-old-model"]}
    assert "DC-hermes-LLM" not in get_newapi_model_alias_mappings()


def test_refresh_provider_catalog_keeps_catalog_and_bindings_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉取失败**不动快照**：一次网络抖动不能把用户全部默认绑定抹掉。"""
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )
    monkeypatch.setattr(
        newapi_provisioner,
        "list_upstream_catalog",
        lambda provider, **_kwargs: ([], "upstream 502"),
    )

    result = newapi_provisioner.refresh_provider_catalog(PROVIDER, reproject=False)

    assert result["ok"] is False
    assert result["error"] == "upstream 502"
    assert result.get("stale") is True
    assert catalog_names_for_provider(PROVIDER) == {"ap-live-llm"}
    assert result["pruned"] == {}
    assert get_newapi_model_alias_mappings()["DC-hermes-LLM"]["target"] == "ap-live-llm"


def test_provider_catalog_route_returns_structured_blocks() -> None:
    """设置页首屏读的目录接口：条目 + 出处（baseUrl / fetchedAt）一起给。"""
    _seed_catalog()
    app = _model_gateway_client()

    body = app.get("/model-gateway/custom/newapi/provider-catalog").json()

    assert body["ok"] is True
    block = next(
        item for item in body["data"]["providers"] if item["provider"] == PROVIDER
    )
    names = [entry["name"] for entry in block["entries"]]
    assert "ap-live-llm" in names and "ap-delisted" not in names
    assert block["baseUrl"] and block["fetchedAt"]
    assert body["data"]["meta"][PROVIDER]["count"] == len(block["entries"])


def test_route_package_re_exports_every_gateway_helper_it_resolves() -> None:
    """子模块统一经包命名空间 ``_gateway.<name>`` 解析 patch 敏感依赖。

    [aipdd fork delta req40] 目录接口就是这么炸过一次：``get_upstream_catalog`` /
    ``get_upstream_catalog_meta`` 没在 ``model_gateway/__init__`` 里再导出，路由
    运行期 AttributeError → 500，而单测直接调函数时看不出来。这里把「引用到的名字
    必须在包上存在」钉成用例。
    """
    import pathlib
    import re

    from novelvideo.api.routes import model_gateway as gateway_package

    package_dir = pathlib.Path(gateway_package.__file__).parent
    referenced: set[str] = set()
    for module in package_dir.glob("*.py"):
        referenced |= set(
            re.findall(r"_gateway\.([A-Za-z_][A-Za-z0-9_]*)", module.read_text(encoding="utf-8"))
        )
    missing = sorted(
        name
        for name in referenced
        if name not in {"py", "X"} and not hasattr(gateway_package, name)
    )
    assert missing == []


def _model_gateway_client() -> TestClient:
    from fastapi import FastAPI

    from novelvideo.api.routes import model_gateway

    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# §4 槽位解析：三层来源，无兜底名
# --------------------------------------------------------------------------- #


def test_resolve_slot_ignores_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """[aipdd fork delta] 模型选定的真源是「设置 → 模型与渠道」+ 渠道目录，进程环境一律不参与。

    旧实现按「per-feature env → 全局兜底 env → 槽位绑定」取值。出厂模板把每一位都写成
    ``X=DC-x-LLM``，等于替所有部署把**没绑定过的槽位键**发给网关（网关注册表只从绑定表
    投影，要求 target 非空），稳定拿 503；而部署方后来在设置页换了模型，env 还指着旧名字
    时，面板显示的是一个模型、实际跑的是另一个。现在把 env 全设上，解析结果必须一字不动 ——
    这条断言就是这次改造的守卫。
    """
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )
    assert resolve_slot("HERMES_MODEL") == "DC-hermes-LLM"

    for key, value in {
        "HERMES_MODEL": f"{PROVIDER}::ap-live-llm",
        "DIRECTOR_SCREENWRITER_MODEL": "DC-director-screenwriter-LLM",
        "TEXT_MODEL_FALLBACK": "gateway::fallback",
        "VISION_MODEL_FALLBACK": "gateway::fallback-vision",
        "NEWAPI_TEXT_MODEL": "gateway::echo",
    }.items():
        monkeypatch.setenv(key, value)

    assert resolve_slot("HERMES_MODEL") == "DC-hermes-LLM"
    # 未绑的位仍按两档兜底走（同类已绑位），env 里写的东西没有插队的资格。
    assert resolve_slot("DIRECTOR_SCREENWRITER_MODEL") == "DC-hermes-LLM"
    assert auto_fallback_call_name("text") == "DC-hermes-LLM"


def test_not_configured_message_leads_with_chinese_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报错文案：中文功能名在前（用户能对上设置页的词），内部键在后（只给排错用）。"""
    save_newapi_provider_channels([])
    _seed_catalog(names=["ap-live-llm"])
    monkeypatch.setenv("TEXT_MODEL_FALLBACK", "gateway::fallback")

    with pytest.raises(ModelSlotNotConfigured) as exc:
        resolve_slot("IDENTITY_PLANNER_CAST_MODEL")

    message = exc.value.message
    assert "出场角色筛选" in message
    assert "IDENTITY_PLANNER_CAST_MODEL" in message
    assert "设置 → 模型与渠道" in message
    assert exc.value.display_name == "出场角色筛选"


def test_resolve_slot_rejects_unbound_and_delisted_with_chinese_reason() -> None:
    """两档兜底都接不住时才前置拒绝，中文成因照旧。

    兜底的依据只能是事实：用户已有的绑定 + 渠道目录。这里把渠道撤掉（目录快照还
    留着，是「删了渠道还没重同步」的真实过渡态），两档都兜不出名字，必须拒绝。
    """
    save_newapi_provider_channels([])
    _seed_catalog(names=["ap-live-llm"])

    with pytest.raises(ModelSlotNotConfigured) as unbound:
        resolve_slot("HERMES_MODEL")
    assert unbound.value.reason == REASON_UNBOUND
    assert "DC-hermes-LLM" in unbound.value.message
    assert "未绑定可用模型" in unbound.value.message
    assert "设置 → 模型与渠道" in unbound.value.message

    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-gone"}}, merge=True
    )
    with pytest.raises(ModelSlotNotConfigured) as delisted:
        resolve_slot("HERMES_MODEL")
    assert delisted.value.reason == REASON_DELISTED
    assert "不在渠道清单里" in delisted.value.message
    # 主标签是中文功能名，内部键只作为排错附注。
    assert delisted.value.display_name == "通用文本生成"
    assert "通用文本生成 · HERMES_MODEL" in delisted.value.message


def test_resolve_slot_does_not_adjudicate_when_no_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """还没同步过清单 = 不拦截：功能位别名照发，由网关自己路由（旧行为）。"""
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-whatever"}}, merge=True
    )
    assert resolve_slot("HERMES_MODEL") == "DC-hermes-LLM"
    assert alias_availability("DC-hermes-LLM")[0] is True


def test_unbound_alias_without_catalog_is_unproven_but_still_dispatches() -> None:
    """[aipdd fork delta] 没清单 + 没绑定：裁决「未证明可用」，但不编名字、不硬拦。

    两件事分开看。``alias_availability`` 回答的是「有没有依据说这一笔能调通」—— 没有：
    网关的注册表只从绑定表投影（``build_channel_model_mapping`` 要求 target 非空），所以
    广场与状态回显把这一位报成「未映射」，绝不报成已就绪。``resolve_slot`` 则仍把槽位别名
    交回网关（req40 旧行为）：这里没有任何可提名的事实，编一个名字比放行更糟，硬拦又会把
    路由由控制面 / 人工维护的部署打成 400；真撞上 distributor 的 503，处理指引指向「先同步
    模型清单」。清单同步过之后同一个未绑位就不再是两难 —— 第二档兜底直接给出目录里的复合
    调用名。
    """
    assert alias_availability("DC-hermes-LLM") == (False, REASON_CATALOG_EMPTY, "")
    assert resolve_slot("HERMES_MODEL") == "DC-hermes-LLM"

    _seed_catalog(names=["ap-live-llm"])
    assert alias_availability("DC-hermes-LLM") == (False, REASON_UNBOUND, "")
    assert resolve_slot("HERMES_MODEL") == f"{PROVIDER}::ap-live-llm"


def test_slot_alias_passes_through_when_process_has_no_local_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 CE 进程读不到本地别名表 / 目录（恒为空），没有裁决资格 —— 必须维持放行。

    否则同一个「表里没有」在新口径下会被当成事实，把路由本来由控制面 / 网关自证的
    部署全部打成 400。
    """
    monkeypatch.setenv("ST_EDITION", "ee")
    assert alias_availability("DC-hermes-LLM")[0] is True
    assert resolve_slot("HERMES_MODEL") == "DC-hermes-LLM"


def test_resolve_slot_follows_sibling_bound_alias_when_unbound() -> None:
    """[aipdd fork delta] 没绑的文本位自动跟随同类里用户已绑好的那一个。

    新环境配好 Key、清单也同步了，但 22 个文本功能位全空是出厂状态 —— 把第一次生成
    挡在「请逐个选完 22 个模型」面前不叫开箱即用。绑定表里任意一个文本位就是用户已经
    做过的那个选择，直接沿用它的槽位别名：那个别名已随绑定投影进网关，路由依据现成，
    也不需要代用户写任何设置。
    """
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {
            "DC-freezone-text-writer-LLM": {
                "provider": PROVIDER,
                "target": "ap-live-llm",
            }
        },
        merge=True,
    )
    assert resolve_slot("DIRECTOR_SCREENWRITER_MODEL") == "DC-freezone-text-writer-LLM"
    assert resolve_slot("HERMES_MODEL") == "DC-freezone-text-writer-LLM"
    assert auto_fallback_call_name("text") == "DC-freezone-text-writer-LLM"


def test_resolve_slot_falls_back_to_catalog_composite_when_nothing_bound() -> None:
    """一个都没绑兼容档：从渠道目录取首个可用项，用复合调用名直接路由。

    视觉位只从「能读图」的条目里挑（拿纯文本名去发带图请求就是 400 那条老根）。
    """
    _seed_catalog()
    assert resolve_slot("DIRECTOR_SCREENWRITER_MODEL") == f"{PROVIDER}::ap-live-llm"
    assert resolve_slot("FREEZONE_VISION_MODEL") == f"{PROVIDER}::ap-live-llm"
    # embedding 不参与兜底：没有目录项能代替向量位，也没人知道维度。
    assert auto_fallback_call_name("embedding") == ""


def test_catalog_default_prefers_the_provider_whose_llm_slots_are_bound() -> None:
    """兜底名落在「这类端点真的跑过」的渠道，而不是渠道被写进设置的先后。

    现场形状：一条上游 key 已失效的中转渠道排在列表最前，文本位一个都没绑，兜底
    就从它的首个目录项提名 —— 于是每个文本调用都 401「无效的令牌」，知识图谱抽出
    来是空的。
    """
    dead, live = "relay", "ark"
    save_newapi_provider_channels(
        [
            {"provider": dead, "baseUrl": "https://relay.invalid/v1", "upstreamKey": "sk-dead"},
            {"provider": live, "baseUrl": BASE_URL, "upstreamKey": "sk-live"},
        ]
    )
    _seed_catalog(provider=dead, names=["aaa-dead-llm"])
    _seed_catalog(provider=live, names=["zzz-live-llm"])

    # 一个都没绑：保持存储顺序，行为不变。
    assert auto_fallback_call_name("text") == f"{dead}::aaa-dead-llm"

    # 同类功能位绑到 live：文本兜底立刻改投 live。
    save_newapi_model_alias_mappings(
        {"DC-story-planner-LLM": {"provider": live, "target": "zzz-live-llm", "capability": "text"}},
        merge=True,
    )
    assert auto_fallback_call_name("text") == f"{live}::zzz-live-llm"


def test_catalog_default_ignores_bindings_that_prove_nothing_about_chat() -> None:
    """视频映射与没标 capability 的别名不能替 chat 端点作保。

    上一案的完整版：生产里那条上游 key 已失效的中转渠道并不是没被用过 —— 它挂着
    Seedance 的媒体映射，别名表里也有裸模型名。若「被绑定过」就是全部信号，它照样
    排在最前，文本仍然 401。只有带 capability 的功能位别名（以及文本位的 Embedding）
    才算「这家的语言端点活着」。
    """
    dead, live = "relay", "ark"
    save_newapi_provider_channels(
        [
            {"provider": dead, "baseUrl": "https://relay.invalid/v1", "upstreamKey": "sk-dead"},
            {"provider": live, "baseUrl": BASE_URL, "upstreamKey": "sk-live"},
        ]
    )
    _seed_catalog(provider=dead, names=["aaa-dead-llm"])
    _seed_catalog(provider=live, names=["zzz-live-llm"])
    save_newapi_media_model_mappings(
        {"AP Seedance-2.0": {"provider": dead, "upstreamModel": "aaa-dead-video", "mediaType": "video"}}
    )
    save_newapi_model_alias_mappings(
        {"ap-dead-3.8-flash": {"provider": dead, "target": "aaa-dead-llm"}}, merge=True
    )

    # 只有媒体绑定 / 无 capability 的别名：排序不动，行为与原来一致。
    assert auto_fallback_call_name("text") == f"{dead}::aaa-dead-llm"

    # 把视觉位绑到 live —— 不是同类，但同一条 chat 链路，足以压过纯媒体绑定。
    save_newapi_model_alias_mappings(
        {"DC-video-prompt-optimizer-LLM": {"provider": live, "target": "zzz-live-llm", "capability": "vision"}},
        merge=True,
    )
    assert auto_fallback_call_name("text") == f"{live}::zzz-live-llm"

    # 换成 Embedding 也是同一类端点的证据。
    save_newapi_model_alias_mappings(
        {"DC-video-prompt-optimizer-LLM": {"provider": dead, "target": "aaa-dead-llm"}}, merge=False
    )
    save_newapi_embedding_model_config(provider=live, upstream_model="zzz-live-llm", dimension=1024)
    assert auto_fallback_call_name("text") == f"{live}::zzz-live-llm"


def test_factory_template_slot_keys_are_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """[aipdd fork delta] 出厂模板那 25 行 ``X=DC-x-LLM`` 如今是纯装饰：设不设都一样。

    ``.env.example`` 已不再提供这些键（env 棘轮会把它们判成死键），但已部署环境里的旧
    ``.env`` 还留着。留着的后果必须是零：这一位走自己的兜底，不是走环境。
    """
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {
            "DC-freezone-text-writer-LLM": {
                "provider": PROVIDER,
                "target": "ap-live-llm",
            }
        },
        merge=True,
    )

    monkeypatch.setenv("DIRECTOR_SCREENWRITER_MODEL", "DC-director-screenwriter-LLM")
    monkeypatch.setenv("TEXT_MODEL_FALLBACK", "gateway::fallback")
    assert resolve_slot("DIRECTOR_SCREENWRITER_MODEL") == "DC-freezone-text-writer-LLM"

    monkeypatch.delenv("DIRECTOR_SCREENWRITER_MODEL")
    monkeypatch.delenv("TEXT_MODEL_FALLBACK")
    assert resolve_slot("DIRECTOR_SCREENWRITER_MODEL") == "DC-freezone-text-writer-LLM"


def test_dead_model_name_in_env_is_ignored_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录里根本没有的名字写进 env 后，旧实现会把它直接交出去（然后网关回 503）。

    复现的是 ``HERMES_MODEL=Acme-IMG-2`` / ``SCENE_MASTER_IMAGE_MODEL=Acme-IMG-2``
    那类出厂死名。现在 env 连读都不读：这一位走自己的兜底 —— 渠道目录里的复合调用名。
    （绑定指向死名、且两档都兜不出名字时的 400 由
    ``test_resolve_slot_rejects_unbound_and_delisted_with_chinese_reason`` 守着。）
    """
    _seed_catalog(names=["ap-live-llm"])
    monkeypatch.setenv("HERMES_MODEL", "Acme-IMG-2")

    assert resolve_slot("HERMES_MODEL") == f"{PROVIDER}::ap-live-llm"


def test_caller_named_call_name_still_routes_and_env_is_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用方点名的调用名照常裁决：目录里的真名直通，另一把已绑定的别名照用。

    指向一把**没绑**的别名时按那把别名裁决，并同样享受同类兜底 —— 用户手写别名是想
    「这一位跟那位共用」，不是想拿一个网关不认识的键。同名 env 写别的也不参与。
    """
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )

    assert (
        resolve_slot("DIRECTOR_SCREENWRITER_MODEL", default_alias=f"{PROVIDER}::ap-live-llm")
        == f"{PROVIDER}::ap-live-llm"
    )
    assert (
        resolve_slot("DIRECTOR_SCREENWRITER_MODEL", default_alias="DC-hermes-LLM")
        == "DC-hermes-LLM"
    )
    assert (
        resolve_slot("HERMES_MODEL", default_alias="DC-screenplay-normalizer-LLM")
        == "DC-hermes-LLM"
    )

    monkeypatch.setenv("DIRECTOR_SCREENWRITER_MODEL", "gateway::whatever")
    assert (
        resolve_slot("DIRECTOR_SCREENWRITER_MODEL", default_alias="DC-hermes-LLM")
        == "DC-hermes-LLM"
    )


def test_named_vision_alias_never_falls_back_to_a_text_model() -> None:
    """[aipdd fork delta] 点名的别名是视觉位时，兜底必须留在视觉档。

    env 兜底删除后，带图调用的能力档只能由「点名的别名」或「调用方显式传的 capability」
    决定：走通用位 ``MODEL_NAME``（登记表里是 text）的带图请求如果被纯文本位接管，网关就
    回 400「当前模型不支持请求所需的图片能力」。宁可提交前 400，也不能塞一个纯文本名。
    """
    _seed_catalog(names=["ap-live-llm"])
    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )

    with pytest.raises(ModelSlotNotConfigured) as exc:
        resolve_slot("MODEL_NAME", default_alias="DC-freezone-vision-LLM")
    assert exc.value.reason in {REASON_UNBOUND, REASON_DELISTED}


def test_describe_slots_reports_the_auto_fallback_call_name() -> None:
    """状态回显必须和真实路由一致：没绑的位要带出「实际会跑在哪个调用名上」。"""
    _seed_catalog(names=["ap-live-llm"])
    rows = describe_slots()
    director = rows["DIRECTOR_SCREENWRITER_MODEL"]
    assert director["usable"] is False
    assert director["reason"] == REASON_UNBOUND
    assert director["autoFallback"] == f"{PROVIDER}::ap-live-llm"
    assert director["autoFallbackSource"] == "catalogDefault"

    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )
    rows = describe_slots()
    assert rows["HERMES_MODEL"]["autoFallback"] == ""
    assert rows["DIRECTOR_SCREENWRITER_MODEL"]["autoFallback"] == "DC-hermes-LLM"
    assert rows["DIRECTOR_SCREENWRITER_MODEL"]["autoFallbackSource"] == "boundSibling"


def test_resolve_slot_rejects_bare_model_name_outside_catalog() -> None:
    """调用方点名的真模型同样只由目录裁决 —— 硬编码名走不到这一步。"""
    _seed_catalog(names=["ap-live-llm"])
    with pytest.raises(ModelSlotNotConfigured) as exc:
        resolve_slot("HERMES_MODEL", default_alias="gpt-4o")
    assert exc.value.reason == REASON_DELISTED
    # 主键是功能位（用户去设置页对得上的那一行），点名的死名留在成因里给排错。
    assert exc.value.slot == "HERMES_MODEL"
    assert "gpt-4o" in exc.value.detail
    assert "gpt-4o" in exc.value.message


def test_resolve_slot_unknown_key_is_not_configured() -> None:
    with pytest.raises(ModelSlotNotConfigured) as exc:
        resolve_slot("SOME_UNREGISTERED_MODEL_ENV")
    assert exc.value.reason == REASON_NOT_CONFIGURED
    assert "未登记的槽位" in exc.value.message


def test_resolve_media_slot_reasons_point_at_different_actions() -> None:
    # 没目录：成因是「还没同步」，动作是去渠道上点同步。
    with pytest.raises(ModelSlotNotConfigured) as empty:
        resolve_media_slot("image")
    assert empty.value.reason == REASON_CATALOG_EMPTY
    assert "同步模型清单" in empty.value.message

    # 有目录但这一位没选：动作是去设置里选。
    _seed_catalog(names=["ap-live-llm"])
    with pytest.raises(ModelSlotNotConfigured) as unbound:
        resolve_media_slot("video")
    assert unbound.value.reason == REASON_UNBOUND


def test_get_newapi_text_model_name_never_falls_back_to_hardcoded_name() -> None:
    """没绑定时的兜底名来自目录（复合名），不是代码里写死的真名。"""
    save_newapi_provider_channels([])
    _seed_catalog(names=["ap-live-llm"])
    with pytest.raises(ModelSlotNotConfigured):
        config.get_newapi_text_model_name("HERMES_MODEL", "DC-hermes-LLM")

    # 渠道在、目录里有名字：兜到复合名（拍平后每个目录名都按复合名注册，必能路由）。
    save_newapi_provider_channels(
        [{"provider": PROVIDER, "baseUrl": BASE_URL, "upstreamKey": "sk-secret"}]
    )
    assert config.get_newapi_text_model_name("HERMES_MODEL") == f"{PROVIDER}::ap-live-llm"

    save_newapi_model_alias_mappings(
        {"DC-hermes-LLM": {"provider": PROVIDER, "target": "ap-live-llm"}}, merge=True
    )
    assert config.get_newapi_text_model_name("HERMES_MODEL") == "DC-hermes-LLM"


def test_model_slot_not_configured_exits_as_http_400_chinese() -> None:
    """异常出口：400 + 中文 + 槽位/成因，任务不入库不启动（不再 500 / 不再跑到一半 404）。"""
    from novelvideo.api.app import create_app

    app = create_app()

    @app.get("/__slot-unresolved")
    async def _raise() -> None:
        raise ModelSlotNotConfigured("DC-hermes-LLM（HERMES_MODEL）", REASON_DELISTED)

    response = TestClient(app, raise_server_exceptions=False).get("/__slot-unresolved")

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert "未绑定可用模型" in body["error"]
    assert body["data"] == {
        "slot": "DC-hermes-LLM（HERMES_MODEL）",
        "reason": REASON_DELISTED,
    }


# --------------------------------------------------------------------------- #
# §5 ASR 候选：资格与输入形态都由目录回答，不再拿名字正则当事实
# --------------------------------------------------------------------------- #


def test_asr_candidate_qualification_prefers_declared_catalog() -> None:
    from novelvideo.freezone import douyin_copywrite

    _seed_catalog()
    # 目录声明了 audio/transcriptions：是转写候选；只有合成端点的不是。
    assert douyin_copywrite.is_asr_candidate_model("ap-asr", PROVIDER) is True
    assert douyin_copywrite.is_asr_candidate_model("ap-tts", PROVIDER) is False
    # 目录里没有这个名字 = 渠道没声明过它是转写模型，退回名字正则判定。
    assert douyin_copywrite.is_asr_candidate_model("not-in-catalog", PROVIDER) is False
    assert (
        douyin_copywrite.is_asr_candidate_model("doubao-seed-asr-1.0", PROVIDER)
        is True
    )

    # inferred 条目没有声明可依：退回名字正则，候选不清空。
    save_upstream_models_snapshot(PROVIDER, [{"name": "doubao-seed-asr-1.0"}])
    assert (
        douyin_copywrite.is_asr_candidate_model("doubao-seed-asr-1.0", PROVIDER) is True
    )


def test_asr_local_upload_acceptance_follows_declared_endpoint() -> None:
    from novelvideo.freezone import douyin_copywrite

    _seed_catalog()
    assert douyin_copywrite.asr_accepts_local_upload("ap-asr", PROVIDER) is True
    # 只声明了别的端点的条目 = 渠道没说能吃本地文件。
    assert douyin_copywrite.asr_accepts_local_upload("ap-tts", PROVIDER) is False
    # 无目录 / inferred 条目维持旧行为，不能把功能整个关掉。
    save_upstream_models_snapshot(PROVIDER, [{"name": "mystery-model"}])
    assert douyin_copywrite.asr_accepts_local_upload("mystery-model", PROVIDER) is True
    assert douyin_copywrite.asr_accepts_local_upload("whatever", "newapi") is True


def test_upstream_catalog_entry_lookup_scans_all_providers() -> None:
    _seed_catalog()
    save_upstream_models_snapshot("newapi", [{"name": "np-model", "kind": "llm"}])
    assert upstream_catalog_entry("ap-embed") is not None
    assert upstream_catalog_entry("ap-embed", PROVIDER)["dimensions"] == 1536
    assert upstream_catalog_entry("np-model", PROVIDER) is None
    assert upstream_catalog_entry("np-model", "newapi") is not None
    assert upstream_catalog_entry("") is None


# ---------------------------------------------------------------------------
# [aipdd fork delta] 按网关清单自动补目录（基础原则）：拉回的 image/video/audio/music
# 直接出现在画布对应目录，无需人工逐条曝光；且**只读派生、不写映射表**。钉住：
# 只补媒体类型（embedding/text 不进）、显式映射为权威（禁用则隐藏）、开关可关。
# ---------------------------------------------------------------------------


def test_media_auto_import_exposes_snapshot_in_catalog() -> None:
    from novelvideo.model_gateway_settings import get_ce_media_model_catalog

    save_upstream_models_snapshot(
        PROVIDER,
        [
            {"name": "ap-image-2.0"},
            {"name": "AP Seedance-2.0 -2"},
            {"name": "ap-qwen3-embedding:0.6b"},
            {"name": "ap-chat-3.8-flash"},
        ],
        base_url=BASE_URL,
    )
    image = get_ce_media_model_catalog("image")
    video = get_ce_media_model_catalog("video")
    image_ids = {entry["id"] for entry in image}
    video_ids = {entry["id"] for entry in video}
    # 图片、视频被自动补进画布目录；唯一拥有该名字的渠道沿用裸模型名做入口 id。
    assert "ap-image-2.0" in image_ids
    assert "AP Seedance-2.0 -2" in video_ids
    # 调用名同样走复合名（网关注册的唯一调用名），视频沿用 newapi_ 前缀。
    by_id = {entry["id"]: entry for entry in image + video}
    assert by_id["ap-image-2.0"]["apiModel"] == f"{PROVIDER}::ap-image-2.0"
    assert by_id["AP Seedance-2.0 -2"]["apiModel"] == (
        f"newapi_{PROVIDER}::AP Seedance-2.0 -2"
    )
    # 旧值（裸模型名）仍可解析到这一行，历史默认不会失效。
    assert "ap-image-2.0" in by_id["ap-image-2.0"]["aliases"]
    # embedding / 纯文本绝不进图片/视频目录。
    assert "ap-qwen3-embedding:0.6b" not in image_ids | video_ids
    assert "ap-chat-3.8-flash" not in image_ids | video_ids
    # 只读补全：映射表一行都没写（不污染配置真源）。
    assert get_newapi_media_model_mappings() == {}


def test_explicit_mapping_overrides_auto_and_disabled_hides() -> None:
    """显式映射为权威：同一渠道的同名条目被禁用后，即便清单里有也不出现在画布目录。"""
    from novelvideo.model_gateway_settings import get_ce_media_model_catalog

    save_newapi_media_model_mappings(
        {
            "ap-image-2.0": {
                "provider": PROVIDER,
                "mediaType": "image",
                "enabled": False,
                "label": "我的图片模型",
                "config": {
                    "request": {"endpoint": "images/generations", "parameters": []}
                },
            }
        }
    )
    save_upstream_models_snapshot(
        PROVIDER,
        [{"name": "ap-image-2.0"}, {"name": "AP Seedance-2.0 -2"}],
        base_url=BASE_URL,
    )
    image_ids = {entry["id"] for entry in get_ce_media_model_catalog("image")}
    # 显式禁用覆盖同一渠道的自动补全 → 图片目录里没有它。
    assert "ap-image-2.0" not in image_ids
    # 未被显式配置的视频模型仍自动出现。
    assert "AP Seedance-2.0 -2" in {
        entry["id"] for entry in get_ce_media_model_catalog("video")
    }


def test_media_catalog_lists_same_model_per_channel() -> None:
    """同名模型在两个渠道各列一行，调用名各自复合；裸名 id 仍归先配的渠道。"""
    from novelvideo.model_gateway_settings import get_ce_media_model_catalog

    save_newapi_provider_channels(
        [
            {"provider": PROVIDER, "baseUrl": BASE_URL, "upstreamKey": "sk-a"},
            {
                "provider": "newapi",
                "baseUrl": "https://newapi.example/v1",
                "upstreamKey": "sk-b",
            },
        ]
    )
    save_upstream_models_snapshot(
        PROVIDER, [{"name": "ap-image-2.0"}], base_url=BASE_URL
    )
    save_upstream_models_snapshot(
        "newapi",
        [{"name": "ap-image-2.0"}],
        base_url="https://newapi.example/v1",
    )
    entries = get_ce_media_model_catalog("image")
    by_channel = {entry["channel"]: entry for entry in entries}
    assert set(by_channel) == {PROVIDER, "newapi"}
    assert by_channel[PROVIDER]["apiModel"] == f"{PROVIDER}::ap-image-2.0"
    assert by_channel["newapi"]["apiModel"] == "newapi::ap-image-2.0"
    # 入口 id 不重复：先配的渠道沿用裸模型名，后配的渠道用复合键。
    assert by_channel[PROVIDER]["id"] == "ap-image-2.0"
    assert by_channel["newapi"]["id"] == "newapi::ap-image-2.0"
    # 裸名只挂在先配的渠道那一行：历史默认的解析口径不变。
    assert "ap-image-2.0" in by_channel[PROVIDER]["aliases"]
    assert "ap-image-2.0" not in by_channel["newapi"]["aliases"]


def test_media_auto_import_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings, "_media_auto_import_enabled", lambda: False
    )
    save_upstream_models_snapshot(
        PROVIDER, [{"name": "ap-image-2.0"}], base_url=BASE_URL
    )
    assert model_gateway_settings.get_ce_media_model_catalog("image") == []
