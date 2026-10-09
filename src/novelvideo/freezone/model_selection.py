# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] req39：freezone 任务入队前对 chat 模型选型的前置校验。

问题（线上实测）：「视频反推剧本」选了 ``volcengine::doubao-seed-1-6-vision-250815``
后，任务把 ffmpeg 抽帧**全部跑完**才在 55% 的视觉调用上死于 404
``InvalidEndpointOrModel.NotFound`` —— 而这个名字在实测缓存里早就写着 ``not-granted``。
原因是两侧各有道理、合起来留了个洞：

- 工具页 / 画布的模型下拉来自候选目录，目录确实会摘掉实测判死的名字，但前端那份列表
  是**进程内快照**（焦点刷新 5 分钟 TTL），面板也不持久化选择；用户手上完全可能还
  握着旧任务里的名字。
- 后端按 req33 的纪律「不做隐式改选」照常放行 —— 不替用户换模型是对的，但让它跑完
  几分钟本地重活再报错，是把用户的时间当成本。

做法：入队前查一次实测缓存，**只拦缓存里已有明确结论**的名字，命中就返回 400 与可执行
指引，任务不入队。不改选、不猜替代：结论是「这个账号没有这个名字」时，替用户决定用
哪个模型属于隐式裁决。

边界（与 req33/req35/req38 一致）：``unknown``（限流、鉴权、超时、端点临时关闭、这轮
没测过）一律放行；``ok`` 放行；裸名归属判不出放行；缓存读失败放行。未登记的任务类型
零影响 —— 本模块只认**确认过当 chat 用**的 payload 字段。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "FREEZONE_CHAT_MODEL_FIELDS",
    "find_unusable_chat_model",
    "unusable_chat_model_detail",
]


# 任务类型 -> 该任务里当 chat 模型使用的 (payload 字段, 模态)。
#
# 这里的表必须显式登记，**不能按字段名猜**：``payload["model"]`` 在反推剧本里是带图
# chat 的视觉模型，在 freezone_gen / freezone_audio_speech 里是图像或语音后端模型，
# 在 copywrite 类任务里是 ASR 模型。后几类不在 chat 实测的判定范围内（音频经 chat
# 永远拿不到 2xx，既不说明开通也不说明没开通），误登记会让能跑的任务被挡在门口。
# 依据 = task_backend/runners/freezone.py 里各 runner 实际传给 generate_freezone_text /
# generate_freezone_story_script_with_vision 的参数。
FREEZONE_CHAT_MODEL_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    # 逐镜复刻规划（文本步骤）。
    "freezone_video_clone": (("model_text", "text"),),
    # 混剪：由主题生成口播文案。
    "freezone_video_mix": (("model_text", "text"),),
    # 白板动画：分镜规划。
    "freezone_whiteboard_animation": (("model_text", "text"),),
    # 科普讲解：解说词与分镜共用一个文本选型。
    "freezone_explainer_video": (("model_text", "text"),),
    # 创意成片：故事扩写 / 角色 / 分场脚本 / 分镜等文本步骤。
    "freezone_video_idea": (("model_text", "text"),),
    # 视频反推剧本：抽帧后带图 chat 的视觉选型。
    "freezone_video_script_reverse": (("model", "vision"),),
}


def find_unusable_chat_model(
    *, task_type: str, payload: dict[str, Any]
) -> tuple[str, str, str] | None:
    """命中「实测已判死」的 chat 选型时返回 ``(渠道, 调用名, 结论档位)``，否则 ``None``。

    整个函数不许抛：它站在任务入口，任何异常都不得演变成「任务提交不了」。
    """

    from novelvideo.text_model_probe import chat_model_verdict

    for key, kind in FREEZONE_CHAT_MODEL_FIELDS.get(str(task_type or ""), ()):
        try:
            call_name = str(payload.get(key) or "").strip()
        except (AttributeError, TypeError):
            return None
        if not call_name:
            # 空 = 跟随系统默认模型链，没有用户选型可校验。
            continue
        provider, verdict = chat_model_verdict(call_name, kind=kind)
        if verdict:
            return provider, call_name, verdict
    return None


def unusable_chat_model_detail(
    call_name: str, verdict: str, *, provider: str = ""
) -> str:
    """把拦截结论压成一句给用户看的中文（含原因 + 两条可执行出路）。"""

    from novelvideo.model_call_diagnostics import provider_display_name

    if verdict == "not-vision":
        return (
            f"模型「{call_name}」已被实测确认读不了图片，无法执行需要看图的步骤，"
            f"任务未提交：请在模型下拉里改选带视觉能力的候选，或保留「跟随系统默认」。"
        )
    provider_name = provider_display_name(provider)
    return (
        f"模型「{call_name}」已被实测确认 {provider_name} 侧没有开通（清单里有不等于"
        f"能调用），任务未提交：请在 {provider_name} 控制台为该账号开通这个模型的推理"
        f"服务，或在模型下拉里改选其它候选 / 保留「跟随系统默认」；刚刚才开通的话，"
        f"请等下一次实测收敛后重试。"
    )
