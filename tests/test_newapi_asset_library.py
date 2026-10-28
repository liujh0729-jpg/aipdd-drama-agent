"""[aipdd fork delta req47] LocalAPI v2 素材库客户端与能力门控（零网络，全 fake httpx）。

钉住的三件事，正是用户口径「素材库仅支持 LocalAPI 支持素材库的情况，并不是所有 LocalAPI 都支持」
在代码层面的落点：

1. **支持性只能探测**：探测用差分判据（真实 Action 的响应形态 + 一条绝不该存在的控制路径），
   把「路由存在但 Key 被拒」和「这台部署根本没有这组路由」分开，含糊结论不缓存、不放开写操作。
2. **接口按上游原样对照**：``POST {站点根}/v2/assets?Action=&Version=``、PascalCase 字段、
   ``{ResponseMetadata, Result}`` 信封；上游错误码与原文一律透出，不改写不吞。
3. **地址不写死**：目标 ``(站点根, Key)`` 只从「显式传入 > 生效网关 > 已连 LocalAPI 渠道」推导。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import pytest

from novelvideo import newapi_asset_library as al
from novelvideo.newapi_asset_library import (
    AssetLibraryError,
    AssetLibraryNotConfigured,
    AssetLibraryTransportError,
    AssetLibraryUnauthorized,
    AssetLibraryUnsupported,
)

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class Resp:
    """最小 httpx.Response 替身：形态字段够用就行，不求全。"""

    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        text: str = "",
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        self._payload = payload
        self.status_code = status
        self.text = text if text else ("" if payload is None else "json-body")
        self.headers = {"content-type": content_type}
        self.is_error = status >= 400

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:  # pragma: no cover - 本模块不使用
        if self.is_error:
            raise httpx.HTTPStatusError("rejected", request=None, response=self)  # type: ignore[arg-type]


def _envelope(result: object) -> dict:
    return {
        "ResponseMetadata": {
            "RequestId": "req-1",
            "Action": "ListAssetGroups",
            "Version": "2024-01-01",
            "Service": "ark",
            "Region": "cn-beijing",
        },
        "Result": result,
    }


def _error_envelope(code: str, message: str, *, status: int = 400) -> Resp:
    return Resp(
        {
            "ResponseMetadata": {
                "RequestId": "req-err",
                "Action": "ListAssetGroups",
                "Error": {"Code": code, "Message": message},
            }
        },
        status=status,
    )


SPA_HTML = "<!doctype html><html lang=\"en\"><head><title>LocalAPI</title></head></html>"


def _fake_http(
    monkeypatch: pytest.MonkeyPatch, responses: list[Resp]
) -> list[dict[str, object]]:
    """按调用顺序吐预设响应；``responses`` 耗尽即测试失败（不许偷偷多发）。"""

    calls: list[dict[str, object]] = []
    pending = iter(responses)

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, params=None, json=None, headers=None, **kwargs):
            calls.append({"url": url, "params": params, "json": json, "headers": headers})
            return next(pending)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    return calls


def _fail_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomClient:
        def __init__(self, *args, **kwargs) -> None:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "Client", BoomClient)


def _target(origin: str = "https://gw.example", key: str = "sk-gw") -> al.AssetLibraryTarget:
    return al.AssetLibraryTarget(origin=origin, api_key=key, source="test")


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """每条用例都从「无 env 引导 + 无探测缓存」开始，避免互相串味。"""

    al.invalidate_asset_library_probe()
    monkeypatch.delenv("SEEDANCE_ASSET_BASE_URL", raising=False)
    monkeypatch.delenv("SEEDANCE_ASSET_API_KEY", raising=False)
    yield
    al.invalidate_asset_library_probe()


# ---------------------------------------------------------------------------
# 目标解析：地址一律推导，不写死
# ---------------------------------------------------------------------------


def test_origin_strips_the_api_prefix_and_accepts_bare_host() -> None:
    assert al._asset_api_origin("https://gw.example/v1") == "https://gw.example"
    assert al._asset_api_origin("https://gw.example/") == "https://gw.example"
    assert al._asset_api_origin("gw.example/v1") == "https://gw.example"
    assert al._asset_api_origin("") == ""
    assert al._asset_api_origin("not a url") == ""


def test_target_prefers_explicit_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SEEDANCE_ASSET_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("SEEDANCE_ASSET_API_KEY", "sk-env")
    target = al.resolve_asset_library_target(base_url="https://manual.example/v1", api_key="sk-manual")
    assert (target.origin, target.api_key, target.source) == (
        "https://manual.example",
        "sk-manual",
        "manual",
    )


def test_env_bootstrap_wins_over_gateway(monkeypatch) -> None:
    """三级凭据解析的第一级：env 引导齐了就不再往下看网关/渠道。"""

    import novelvideo.model_gateway_settings as mgs

    monkeypatch.setenv("SEEDANCE_ASSET_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("SEEDANCE_ASSET_API_KEY", "sk-env")

    def _boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(mgs, "get_effective_newapi_config", _boom)
    target = al.resolve_asset_library_target()
    assert (target.origin, target.api_key, target.source) == (
        "https://env.example",
        "sk-env",
        "environment",
    )


def test_target_uses_gateway_when_env_absent(monkeypatch) -> None:
    import novelvideo.model_gateway_settings as mgs

    @dataclass
    class _Cfg:
        mode: str = "custom"
        source: str = "custom"
        base_url: str = "https://newapi.internal:3000/v1"
        api_key: str = "sk-gateway"

    monkeypatch.setattr(mgs, "get_effective_newapi_config", lambda: _Cfg())
    target = al.resolve_asset_library_target()
    assert target.origin == "https://newapi.internal:3000"
    assert target.api_key == "sk-gateway"
    assert target.source == "gateway[custom/custom]"


def test_target_falls_back_to_connected_newapi_channel(monkeypatch) -> None:
    import novelvideo.model_gateway_settings as mgs
    import novelvideo.newapi_provisioner as prov

    def _no_gateway():
        raise RuntimeError("no gateway")

    monkeypatch.setattr(mgs, "get_effective_newapi_config", _no_gateway)
    monkeypatch.setattr(
        prov,
        "get_newapi_provider_channels",
        lambda: [
            {"provider": "ali", "baseUrl": "https://other.example/v1", "upstreamKey": "sk-x"},
            {
                "provider": "newapi",
                "name": "DC-newapi",
                "baseUrl": "https://relay.example/v1",
                "upstreamKey": "sk-relay",
            },
        ],
    )
    target = al.resolve_asset_library_target()
    assert (target.origin, target.api_key) == ("https://relay.example", "sk-relay")
    assert target.source == "channel[DC-newapi]"


def test_target_raises_when_nothing_configured(monkeypatch) -> None:
    import novelvideo.model_gateway_settings as mgs
    import novelvideo.newapi_provisioner as prov

    def _no_gateway():
        raise RuntimeError("gateway settings unreadable")

    monkeypatch.setattr(mgs, "get_effective_newapi_config", _no_gateway)
    monkeypatch.setattr(prov, "get_newapi_provider_channels", lambda: [])
    with pytest.raises(AssetLibraryNotConfigured) as exc:
        al.resolve_asset_library_target()
    assert exc.value.kind == "unconfigured"
    # 指路必须指到真实栏目：菜单叫「模型与渠道」，不叫「模型网关」。
    assert "设置 → 模型与渠道" in str(exc.value)


def test_half_filled_manual_input_reuses_resolved_counterpart(monkeypatch) -> None:
    """只填一半也能跑：缺的那半沿用解析结果（req44 同一口径）。"""

    import novelvideo.model_gateway_settings as mgs

    @dataclass
    class _Cfg:
        mode: str = "custom"
        source: str = "custom"
        base_url: str = "https://gw.internal/v1"
        api_key: str = "sk-gw"

    monkeypatch.setattr(mgs, "get_effective_newapi_config", lambda: _Cfg())
    only_base = al.resolve_asset_library_target(base_url="https://other.example/v1")
    assert (only_base.origin, only_base.api_key) == ("https://other.example", "sk-gw")

    only_key = al.resolve_asset_library_target(api_key="sk-manual")
    assert (only_key.origin, only_key.api_key) == ("https://gw.internal", "sk-manual")


# ---------------------------------------------------------------------------
# 请求形态：按上游文档原样
# ---------------------------------------------------------------------------


def test_post_action_uses_legacy_action_query_on_site_root(monkeypatch) -> None:
    calls = _fake_http(
        monkeypatch, [Resp(_envelope({"Items": [], "TotalCount": 0}))]
    )
    al.get_asset("asset-1", target=_target("https://gw.example/v1"))

    call = calls[0]
    assert call["url"] == "https://gw.example/v2/assets"
    assert call["params"] == {"Action": "GetAsset", "Version": "2024-01-01"}
    assert call["json"] == {"Id": "asset-1"}
    assert call["headers"]["Authorization"] == "Bearer sk-gw"


def test_list_asset_groups_payload_is_pascal_case_and_typed(monkeypatch) -> None:
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    al.list_asset_groups(
        group_type="LivenessFace",
        group_ids=["g1", " ", "g2"],
        name="  张三",
        max_results=20,
        next_token="tk",
        sort_by="UpdateTime",
        sort_order="Asc",
        target=_target(),
    )

    body = calls[0]["json"]
    assert body["Filter"] == {"GroupType": "LivenessFace", "GroupIds": ["g1", "g2"], "Name": "张三"}
    assert body["MaxResults"] == 20
    assert body["NextToken"] == "tk"
    assert body["SortBy"] == "UpdateTime"
    assert body["SortOrder"] == "Asc"
    # 两套分页互斥：给了 MaxResults 就不该出现 PageNumber/PageSize。
    assert "PageNumber" not in body and "PageSize" not in body


def test_list_asset_groups_paged_variant(monkeypatch) -> None:
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    al.list_asset_groups(page_number=2, page_size=10, target=_target())
    body = calls[0]["json"]
    assert body["PageNumber"] == 2
    assert body["PageSize"] == 10
    assert "MaxResults" not in body and "NextToken" not in body


def test_list_assets_filters_type_and_statuses(monkeypatch) -> None:
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    al.list_assets(
        group_ids=["g1"],
        asset_type="Video",
        statuses=["Active", "Processing"],
        sort_by="GroupId",
        target=_target(),
    )
    body = calls[0]["json"]
    assert body["Filter"]["AssetType"] == "Video"
    assert body["Filter"]["Statuses"] == ["Active", "Processing"]
    assert body["SortBy"] == "GroupId"


def test_group_type_is_required_by_document_contract() -> None:
    with pytest.raises(ValueError, match="GroupType"):
        al.list_asset_groups(group_type="Face", target=_target())
    with pytest.raises(ValueError, match="AssetType"):
        al.list_assets(asset_type="image", target=_target())
    with pytest.raises(ValueError, match="SortOrder"):
        al.list_assets(sort_order="desc", target=_target())


def test_create_asset_url_only_and_name_limits(monkeypatch) -> None:
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Id": "asset-new"}))])
    result = al.create_asset(
        group_id="g1", asset_type="Image", url="https://oss.example/a.png", target=_target()
    )
    assert result == {"Id": "asset-new"}
    assert calls[0]["json"] == {"GroupId": "g1", "AssetType": "Image", "URL": "https://oss.example/a.png"}

    with pytest.raises(ValueError, match="Name"):
        al.create_asset(
            group_id="g1",
            asset_type="Image",
            url="https://oss.example/a.png",
            name="x" * 65,
            target=_target(),
        )
    with pytest.raises(ValueError, match="URL"):
        al.create_asset(group_id="g1", asset_type="Image", url="  ", target=_target())
    with pytest.raises(ValueError, match="GroupId"):
        al.create_asset(group_id="", asset_type="Image", url="https://a/b.png", target=_target())


def test_validate_session_and_result_actions(monkeypatch) -> None:
    calls = _fake_http(
        monkeypatch,
        [
            Resp(_envelope({"BytedToken": "bt-1", "H5Link": "https://h5.example/x"})),
            Resp(_envelope({"GroupId": "group-face"})),
        ],
    )
    session = al.create_visual_validate_session(
        callback_url="https://app.example/", project_name="p1", target=_target()
    )
    assert session == {"BytedToken": "bt-1", "H5Link": "https://h5.example/x"}
    assert calls[0]["params"]["Action"] == "CreateVisualValidateSession"
    assert calls[0]["json"] == {"CallbackURL": "https://app.example/", "ProjectName": "p1"}

    result = al.get_visual_validate_result("bt-1", target=_target())
    assert result == {"GroupId": "group-face"}
    assert calls[1]["params"]["Action"] == "GetVisualValidateResult"
    assert calls[1]["json"] == {"BytedToken": "bt-1"}
    with pytest.raises(ValueError, match="BytedToken"):
        al.get_visual_validate_result("", target=_target())


# ---------------------------------------------------------------------------
# 信封与错误：上游原文一律透出
# ---------------------------------------------------------------------------


def test_success_envelope_returns_result_only(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp(_envelope({"Items": [{"Id": "g1"}], "TotalCount": 1}))])
    data = al.list_asset_groups(target=_target())
    assert data["Items"][0]["Id"] == "g1"


def test_empty_result_dict_is_not_an_error(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp({"ResponseMetadata": {"RequestId": "r"}, "Result": None})])
    assert al.get_asset_group("g1", target=_target()) == {}


def test_upstream_error_envelope_keeps_code_message_and_request_id(monkeypatch) -> None:
    _fake_http(monkeypatch, [_error_envelope("ModelNotOpen", "该账号未开通此模型", status=400)])
    with pytest.raises(AssetLibraryError) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.code == "ModelNotOpen"
    assert exc.value.message == "该账号未开通此模型"
    assert exc.value.request_id == "req-err"
    assert exc.value.status == 400
    assert exc.value.envelope is True


def test_envelope_401_is_unauthorized_but_still_an_asset_envelope(monkeypatch) -> None:
    _fake_http(monkeypatch, [_error_envelope("InvalidAuthorization", "key 无效", status=401)])
    with pytest.raises(AssetLibraryUnauthorized) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.kind == "unauthorized"
    assert exc.value.envelope is True


def test_relay_token_error_is_verbatim_and_not_an_envelope(monkeypatch) -> None:
    """实测形态：LocalAPI 令牌层用 OpenAI 风格错误体（无效的令牌），原文照透出。"""

    _fake_http(
        monkeypatch,
        [
            Resp(
                {"error": {"code": "", "message": "无效的令牌 (request id: 2026)", "type": "new_api_error"}},
                status=401,
            )
        ],
    )
    with pytest.raises(AssetLibraryUnauthorized) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.message.startswith("无效的令牌")
    assert exc.value.code == "new_api_error"
    assert exc.value.envelope is False


def test_spa_page_means_no_asset_library(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp(None, status=200, text=SPA_HTML, content_type="text/html; charset=utf-8")])
    with pytest.raises(AssetLibraryUnsupported) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.kind == "unsupported"


def test_spa_page_without_content_type_header_still_detected() -> None:
    class _NoCt:
        headers: dict[str, str] = {}
        text = SPA_HTML

    assert al._looks_like_html(_NoCt()) is True


def test_json_404_is_unsupported_too(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp({"detail": "not found"}, status=404)])
    with pytest.raises(AssetLibraryUnsupported):
        al.get_asset("a1", target=_target())


def test_unparsable_body_is_reported_not_swallowed(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp(None, status=500, text="boom")])
    with pytest.raises(AssetLibraryError) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.code == "BadEnvelope"
    assert "boom" in exc.value.message


def test_non_object_json_body_keeps_snippet(monkeypatch) -> None:
    _fake_http(monkeypatch, [Resp(["unexpected"], status=200)])
    with pytest.raises(AssetLibraryError) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.code == "BadEnvelope"


def test_transport_failure_is_a_transport_error(monkeypatch) -> None:
    _fail_transport(monkeypatch)
    with pytest.raises(AssetLibraryTransportError) as exc:
        al.list_asset_groups(target=_target())
    assert exc.value.kind == "transport"


# ---------------------------------------------------------------------------
# 能力探测：差分判据（这是 req47 的门控正身）
# ---------------------------------------------------------------------------


def _patch_probe_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        al, "resolve_asset_library_target", lambda **kw: _target()
    )


def test_probe_supported_when_groups_are_readable(monkeypatch) -> None:
    _patch_probe_target(monkeypatch)
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Items": [], "TotalCount": 0}))])
    status = al.probe_asset_library_support()
    assert status["supported"] is True
    assert status["state"] == al.STATE_SUPPORTED
    assert status["baseUrl"] == "https://gw.example"
    assert status["keyPreview"]  # 面板要能看出用的是哪把 Key（脱敏）
    assert len(calls) == 1  # 成功路径不需要控制组


def test_probe_unsupported_when_endpoint_returns_spa_page(monkeypatch) -> None:
    """内置网关的实测形态：/v2/assets 落 SPA 兜底 → 判不支持，且不发写操作。"""

    _patch_probe_target(monkeypatch)
    _fake_http(monkeypatch, [Resp(None, text=SPA_HTML, content_type="text/html")])
    status = al.probe_asset_library_support()
    assert status["supported"] is False
    assert status["state"] == al.STATE_UNSUPPORTED
    assert "没有暴露" in status["reason"]


def test_probe_unauthorized_uses_control_path_to_prove_route_exists(monkeypatch) -> None:
    """susciyuan 的实测形态：资产路径 401（网关错误层）、控制路径回 SPA → 路由在、Key 被拒。

    只看状态码会把这台误判成「不支持」；差分判据给的是「有路由但未证实」，写操作照样拦。
    """

    _patch_probe_target(monkeypatch)
    calls = _fake_http(
        monkeypatch,
        [
            Resp(
                {"error": {"message": "无效的令牌", "type": "new_api_error"}},
                status=401,
            ),
            Resp(None, text=SPA_HTML, content_type="text/html"),
        ],
    )
    status = al.probe_asset_library_support()
    assert status["supported"] is False
    assert status["state"] == al.STATE_UNAUTHORIZED
    assert "无效的令牌" in status["reason"]
    assert calls[1]["url"] == "https://gw.example/v2/assets-probe-control"


def test_probe_ambiguous_when_whole_prefix_is_gated(monkeypatch) -> None:
    """控制路径也回 JSON：整段前缀被中间件接管，无法判定 → 不判支持，也不缓存。"""

    _patch_probe_target(monkeypatch)
    responses = [
        Resp({"error": {"message": "无效的令牌", "type": "new_api_error"}}, status=401),
        Resp({"error": {"message": "无效的令牌", "type": "new_api_error"}}, status=401),
        Resp(_envelope({"Items": []})),
    ]
    _fake_http(monkeypatch, responses)
    status = al.probe_asset_library_support()
    assert status["state"] == al.STATE_AMBIGUOUS
    assert status["supported"] is False
    # 含糊结论不进缓存：下一条用例应当重新发请求。
    again = al.probe_asset_library_support()
    assert again["cached"] is False


def test_probe_supported_when_upstream_rejects_with_asset_envelope(monkeypatch) -> None:
    """账号未开通这类上游错证明「接口可达」：判支持，但把原文留成 warning。"""

    _patch_probe_target(monkeypatch)
    _fake_http(monkeypatch, [_error_envelope("InvalidAction", "action 不存在", status=404)])
    status = al.probe_asset_library_support()
    assert status["supported"] is True
    assert status["warning"] == "InvalidAction: action 不存在"
    assert status["upstreamCode"] == "InvalidAction"


def test_probe_unconfigured_makes_no_request(monkeypatch) -> None:
    import novelvideo.model_gateway_settings as mgs
    import novelvideo.newapi_provisioner as prov

    def _no_gateway():
        raise RuntimeError("unreadable")

    monkeypatch.setattr(mgs, "get_effective_newapi_config", _no_gateway)
    monkeypatch.setattr(prov, "get_newapi_provider_channels", lambda: [])
    calls = _fake_http(monkeypatch, [])
    status = al.probe_asset_library_support()
    assert status["state"] == al.STATE_UNCONFIGURED
    assert status["supported"] is False
    assert calls == []


def test_probe_unreachable_is_not_cached(monkeypatch) -> None:
    """连不上不能灰着脸记住 —— 网关恢复后面板要能立刻再问一次。"""

    _patch_probe_target(monkeypatch)
    _fail_transport(monkeypatch)
    status = al.probe_asset_library_support()
    assert status["state"] == al.STATE_UNREACHABLE
    assert status["supported"] is False

    _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    ok = al.probe_asset_library_support()
    assert ok["state"] == al.STATE_SUPPORTED


def test_probe_result_is_cached_per_credential_and_refresh_bypasses(monkeypatch) -> None:
    _patch_probe_target(monkeypatch)
    calls = _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    first = al.probe_asset_library_support()
    second = al.probe_asset_library_support()
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1

    # Key 变了就是另一台机器：缓存键含凭据指纹，必须重探。
    monkeypatch.setattr(
        al, "resolve_asset_library_target", lambda **kw: _target(key="sk-other")
    )
    _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    assert al.probe_asset_library_support()["cached"] is False

    al.invalidate_asset_library_probe()
    _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    assert al.probe_asset_library_support()["cached"] is False


def test_probe_ttl_expiry_forces_recheck(monkeypatch) -> None:
    _patch_probe_target(monkeypatch)
    _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    al.probe_asset_library_support()

    class _Later:
        @staticmethod
        def monotonic() -> float:
            return 10 ** 6

    monkeypatch.setattr(al, "time", _Later())
    _fake_http(monkeypatch, [Resp(_envelope({"Items": []}))])
    assert al.probe_asset_library_support()["cached"] is False


def test_write_gate_blocks_every_non_supported_state(monkeypatch) -> None:
    _patch_probe_target(monkeypatch)
    _fake_http(monkeypatch, [Resp(None, text=SPA_HTML, content_type="text/html")])
    with pytest.raises(AssetLibraryUnsupported):
        al.require_asset_library_supported()

    al.invalidate_asset_library_probe()
    _fake_http(
        monkeypatch,
        [
            Resp({"error": {"message": "无效的令牌", "type": "new_api_error"}}, status=401),
            Resp(None, text=SPA_HTML, content_type="text/html"),
        ],
    )
    with pytest.raises(AssetLibraryUnauthorized):
        al.require_asset_library_supported()


# ---------------------------------------------------------------------------
# 路由层：契约、闸门与错误映射（直接调协程，不起 TestClient）
# ---------------------------------------------------------------------------


def _routes():
    from novelvideo.api.routes.model_gateway import seedance_assets as routes

    return routes


def test_status_route_returns_probe_payload(monkeypatch) -> None:
    routes = _routes()
    seen: dict[str, object] = {}

    def _probe(**kwargs):
        seen.update(kwargs)
        return {"supported": True, "state": "supported"}

    monkeypatch.setattr(al, "probe_asset_library_support", _probe)
    out = asyncio.run(routes.seedance_asset_library_status(refresh=True))
    assert out == {"ok": True, "data": {"supported": True, "state": "supported"}}
    assert seen == {"force": True}


def test_groups_route_maps_upstream_error_to_http_detail(monkeypatch) -> None:
    from fastapi import HTTPException

    routes = _routes()

    def _boom(**kwargs):
        raise AssetLibraryError("该账号未开通此模型", code="ModelNotOpen", status=400)

    monkeypatch.setattr(al, "list_asset_groups", _boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.seedance_asset_groups())
    assert exc.value.status_code == 502
    assert exc.value.detail == "[ModelNotOpen] 该账号未开通此模型"


def test_create_asset_route_is_gated_before_calling_upstream(monkeypatch) -> None:
    """门控在「发请求之前」：不支持时一次上游写请求都不该发出去。"""

    from fastapi import HTTPException

    routes = _routes()
    called: list[dict] = []

    def _gate():
        raise AssetLibraryUnsupported("这台 LocalAPI 没有暴露 v2 素材库接口", code="RouteNotFound")

    def _create(**kwargs):
        called.append(kwargs)
        return {"Id": "a1"}

    monkeypatch.setattr(al, "require_asset_library_supported", _gate)
    monkeypatch.setattr(al, "create_asset", _create)
    body = routes.CreateAssetBody(groupId="g1", assetType="Image", url="https://o/a.png")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.seedance_asset_create(body))
    assert exc.value.status_code == 503
    assert called == []


def test_create_asset_route_forwards_body_fields(monkeypatch) -> None:
    routes = _routes()
    captured: dict[str, object] = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return {"Id": "a1"}

    monkeypatch.setattr(al, "require_asset_library_supported", lambda: {"supported": True})
    monkeypatch.setattr(al, "create_asset", _create)
    body = routes.CreateAssetBody(
        groupId="g1", assetType="Video", url="https://o/a.mp4", name="主角", projectName="p"
    )
    out = asyncio.run(routes.seedance_asset_create(body))
    assert out["data"] == {"Id": "a1"}
    assert captured["group_id"] == "g1"
    assert captured["asset_type"] == "Video"
    assert captured["name"] == "主角"


def test_validate_session_defaults_callback_to_browser_origin(monkeypatch) -> None:
    routes = _routes()
    captured: dict[str, object] = {}

    class _Req:
        headers = {"x-forwarded-host": "public.example:8443", "x-forwarded-proto": "https"}
        url = None

        def __init__(self) -> None:
            from types import SimpleNamespace

            self.url = SimpleNamespace(scheme="http")

    def _create(**kwargs):
        captured.update(kwargs)
        return {"BytedToken": "bt", "H5Link": "https://h5/x"}

    monkeypatch.setattr(al, "require_asset_library_supported", lambda: {"supported": True})
    monkeypatch.setattr(al, "create_visual_validate_session", _create)
    out = asyncio.run(
        routes.seedance_validate_session_create(_Req(), routes.ValidateSessionBody())
    )
    assert captured["callback_url"] == "https://public.example:8443/"
    assert out["data"]["H5Link"] == "https://h5/x"
    assert out["data"]["callbackUrl"] == "https://public.example:8443/"


def test_validate_session_refuses_to_guess_origin(monkeypatch) -> None:
    """反代没给 Host 时不猜地址（req41 同一口径），而是要求调用方显式传。"""

    from fastapi import HTTPException

    routes = _routes()

    class _Req:
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            from types import SimpleNamespace

            self.url = SimpleNamespace(scheme="http")

    monkeypatch.setattr(al, "require_asset_library_supported", lambda: {"supported": True})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.seedance_validate_session_create(_Req(), routes.ValidateSessionBody()))
    assert exc.value.status_code == 400
    assert "callbackUrl" in exc.value.detail


def test_validate_result_route_maps_value_error_to_400(monkeypatch) -> None:
    from fastapi import HTTPException

    routes = _routes()
    monkeypatch.setattr(al, "require_asset_library_supported", lambda: {"supported": True})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.seedance_validate_result_query("   "))
    assert exc.value.status_code == 400


def test_probe_refresh_route_clears_cache_then_forces(monkeypatch) -> None:
    routes = _routes()
    events: list[str] = []

    monkeypatch.setattr(al, "invalidate_asset_library_probe", lambda: events.append("clear"))
    monkeypatch.setattr(
        al,
        "probe_asset_library_support",
        lambda **kw: events.append(f"probe:{kw.get('force')}") or {"state": "supported"},
    )
    out = asyncio.run(routes.seedance_asset_probe_refresh())
    assert events == ["clear", "probe:True"]
    assert out["data"]["state"] == "supported"


def test_seedance_routes_are_mounted_on_the_gateway_router() -> None:
    """路由必须真的挂进 ``/model-gateway`` 整组（带登录闸门），漏了 include 就失效。"""

    from novelvideo.api.routes import model_gateway as gateway
    from novelvideo.api.routes.model_gateway import seedance_assets

    assert gateway.router.prefix == "/model-gateway"
    included = [getattr(entry, "original_router", None) for entry in gateway.router.routes]
    assert seedance_assets.router in included
    assert {route.path for route in seedance_assets.router.routes} >= {
        "/seedance-assets/status",
        "/seedance-assets/groups",
        "/seedance-assets/assets",
        "/seedance-assets/assets/{asset_id}",
        "/seedance-assets/validate-sessions",
        "/seedance-assets/validate-results",
        "/seedance-assets/probe/refresh",
    }


def test_asset_reference_builder_accepts_bare_id_and_already_prefixed() -> None:
    """面板复制出来的是 ``asset://<Id>``，用户手填的往往是裸 Id：两种都收，且只包一层。"""

    assert al.asset_reference("asset-20260318071009-abcde") == (
        "asset://asset-20260318071009-abcde"
    )
    assert al.asset_reference(" asset://asset-20260318071009-abcde ") == (
        "asset://asset-20260318071009-abcde"
    )
    assert al.is_asset_reference("asset://asset-1") is True
    assert al.is_asset_reference("https://gw.example/a.png") is False
    assert al.is_asset_reference("") is False


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "asset://",
        "asset:///double-slash",
        "asset://two words",
        "asset://back\\slash",
    ],
)
def test_asset_reference_builder_rejects_unusable_ids(bad: str) -> None:
    """形态不对就当场报错：递上去只会被上游拒，还可能照扣一次生成。"""

    with pytest.raises(ValueError):
        al.asset_reference(bad)
