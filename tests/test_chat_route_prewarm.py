import pytest
from starlette.websockets import WebSocketDisconnect

from novelvideo.api.routes import chat as chat_route
from novelvideo.chat.store import ChatScope


@pytest.mark.anyio
async def test_send_scope_changed_returns_none_when_client_disconnected(monkeypatch) -> None:
    class DisconnectedWebSocket:
        async def send_json(self, payload):
            raise WebSocketDisconnect(code=1006)

    async def fake_history(username, scope, *, project_ctx=None):
        return []

    monkeypatch.setattr(chat_route, "_history", fake_history)

    result = await chat_route._send_scope_changed(
        DisconnectedWebSocket(),
        {"username": "admin"},
        "admin",
        ChatScope(kind="home"),
    )

    assert result is None


def test_ws_connect_does_not_prewarm_default_home_scope() -> None:
    assert chat_route._should_prewarm_on_ws_connect(ChatScope(kind="home")) is False


def test_ws_connect_can_prewarm_non_home_scope() -> None:
    assert chat_route._should_prewarm_on_ws_connect(ChatScope(kind="project", id="project_a")) is True


# [aipdd fork delta] WP-D: 聊天入口不再有「余额门禁」。原先这条用例守的是
# ``_require_ai_assistant_access`` 把 ``assistant.chat`` 这个 feature_key 传给余额判定；
# 余额判定整体拆除后，留着它等于守一个不该再存在的钩子。改成守反向：这个门禁不许长回来
# （鉴权仍由 ``_require_ws_scope_access`` 负责，见 tests/test_chat_ws_*_egress.py）。
def test_chat_route_has_no_credit_balance_gate() -> None:
    assert not hasattr(chat_route, "_require_ai_assistant_access")
    assert not hasattr(chat_route, "get_usage_meter")
