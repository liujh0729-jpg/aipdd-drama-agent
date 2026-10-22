"""Non-secret official defaults used when no local .env is present.

[aipdd fork delta req40] 这里只剩下**渠道地址**与**内部功能位槽位键**两类常量，不再
有「默认模型名」表：渠道有什么模型只能从 BaseURL 拉，用哪个模型由用户在
「设置 → 模型与渠道」里绑定（见 :mod:`novelvideo.model_slots`）。旧
``DEFAULT_TEXT_MODEL_BY_ENV`` 表已并入 ``model_slots.SLOTS`` —— 它原先只是把功能 env
指向 ``DC-*`` 槽位键，现在那层语义由槽位登记表唯一持有，且解析时还要校验绑定目标
仍存在于渠道目录里。
"""

from __future__ import annotations

import os

from novelvideo.model_slots import (
    SLOT_COGNEE_EMBEDDING,
    SLOT_COGNEE_LLM,
    SLOT_FREEZONE_STORY_SCRIPT,
    SLOT_FREEZONE_TEXT_WRITER,
    SLOT_FREEZONE_TRANSLATION,
    SLOT_FREEZONE_VISION,
    SLOT_VIDEO_PROMPT_OPTIMIZER,
)

# [aipdd fork delta req41] 官方模式地址**不写死**：本 fork 里它就是「部署者自己的
# 网关地址」，真源在数据库（设置 → 模型与渠道的渠道 BaseURL）或 .env；代码不预设任何站点
# 地址，否则换网关地址 / 换机器部署时旧地址会继续被当成有效值打出去。
# 旧实现把某个具体域名钉在源码常量里（上一版是 ``https://susciyuan.com/v1``），
# 那等于「代码比部署更早知道网关在哪」—— 正是 req40/req41 要消灭的那类硬编码。
OFFICIAL_NEWAPI_BASE_URL_ENVS = ("OFFICIAL_NEWAPI_BASE_URL",)


def official_newapi_base_url() -> str:
    """官方模式网关地址：只读专用 env，未配置就返回空（由调用面报「未配置」）。

    [aipdd fork delta req41] 这里故意**不**看 ``LOCALAPI_BASE_URL``：那个变量在 CE 里是
    「自配网关」那一栏的部署级 env，官方模式要是顺着它读，任何写了 LOCALAPI_BASE_URL 的
    环境都能把官方模式的地址顶掉。EE 分支在 ``get_effective_newapi_config`` 里已经优
    先读 LOCALAPI_BASE_URL，不需要这里再兜一层。
    """

    for env_name in OFFICIAL_NEWAPI_BASE_URL_ENVS:
        value = str(os.environ.get(env_name) or "").strip().rstrip("/")
        if value:
            return value
    return ""

DEFAULT_COGNEE_LLM_PROVIDER = "newapi"
DEFAULT_COGNEE_LLM_MODEL = SLOT_COGNEE_LLM
DEFAULT_COGNEE_EMBEDDING_PROVIDER = "newapi"
DEFAULT_COGNEE_EMBEDDING_MODEL = SLOT_COGNEE_EMBEDDING
# [aipdd fork delta req40] 维度不再是「写死的 1024」：优先读渠道目录里该 embedding
# 模型自带的 ``dimensions``，这个值只在目录没给时作为表单初值（不是运行期兜底模型名）。
DEFAULT_COGNEE_EMBEDDING_DIM = "1024"
DEFAULT_EMBEDDING_BATCH_SIZE = "10"

# 以下全部是**槽位键**（网关 model_mapping 的路由名），不是模型名。
DEFAULT_FREEZONE_TRANSLATION_MODEL = SLOT_FREEZONE_TRANSLATION
DEFAULT_FREEZONE_TEXT_WRITER_MODEL = SLOT_FREEZONE_TEXT_WRITER
DEFAULT_FREEZONE_STORY_SCRIPT_MODEL = SLOT_FREEZONE_STORY_SCRIPT
DEFAULT_FREEZONE_VISION_MODEL = SLOT_FREEZONE_VISION
DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL = SLOT_VIDEO_PROMPT_OPTIMIZER
