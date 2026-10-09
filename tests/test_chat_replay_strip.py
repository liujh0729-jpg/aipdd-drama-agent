from novelvideo.chat import service as chat_service


def test_suppresses_partial_labeled_transcript_replay_before_current_prompt():
    replay = "User: 之前的问题\nAssistant: 之前的回答\nUser: 另一条旧问题"

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=[],
            current_prompt="现在的问题",
            suppress_partial_replay=True,
        )
        == ""
    )


def test_keeps_reply_after_current_prompt_in_replayed_transcript():
    replay = (
        "User: 之前的问题\n"
        "Assistant: 之前的回答\n"
        "User: 现在的问题\n"
        "Assistant: 这是新的回复"
    )

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=[],
            current_prompt="现在的问题",
            suppress_partial_replay=True,
        )
        == "这是新的回复"
    )


def test_final_replay_strip_still_returns_unlabeled_content():
    assert (
        chat_service._strip_replayed_chat_response(
            "正常的新回复",
            previous_assistant=[],
            current_prompt="现在的问题",
        )
        == "正常的新回复"
    )


def test_strips_unlabeled_assistant_history_sequence_before_new_reply():
    previous = [
        "你好！有什么我可以帮你的吗？",
        "你好！我是 Hermes Agent，你的 AI 助手。",
    ]
    replay = "".join(previous) + "当前任务失败了，我建议先重试脚本生成。"

    assert (
        chat_service._strip_replayed_chat_response(
            replay,
            previous_assistant=previous,
            current_prompt="继续",
            suppress_partial_replay=True,
        )
        == "当前任务失败了，我建议先重试脚本生成。"
    )


def test_keeps_complete_repeated_short_reply_on_final_strip():
    assert (
        chat_service._strip_replayed_chat_response(
            "你好！有什么可以帮你？",
            previous_assistant=["你好！有什么可以帮你？"],
            current_prompt="你好",
            suppress_partial_replay=False,
        )
        == "你好！有什么可以帮你？"
    )


def test_suppresses_complete_repeated_short_reply_during_streaming():
    assert (
        chat_service._strip_replayed_chat_response(
            "你好！有什么可以帮你？",
            previous_assistant=["你好！有什么可以帮你？"],
            current_prompt="你好",
            suppress_partial_replay=True,
        )
        == ""
    )


def test_hides_internal_skill_tool_events_from_chat_cards():
    assert chat_service._is_hidden_chat_tool_event(
        "skill",
        "→ skill view (dramaclaw)\n内容: Loading skill 'dramaclaw'",
    )
    assert not chat_service._is_hidden_chat_tool_event(
        "aipdd_drama_pipeline_status",
        "→ aipdd_drama_pipeline_status\ncompleted",
    )
