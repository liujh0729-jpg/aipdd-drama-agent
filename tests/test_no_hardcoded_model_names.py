"""[aipdd fork delta req40] 静态守卫：模型名只能来自渠道目录，代码里不许再写死兜底名。

req40 的原则是「渠道有什么模型，平台不该预先知道，只能拉了才知道」。运行期已经改成
「目录 + 用户设置」解析（``model_slots`` / ``upstream_catalog``），本用例负责让这条
规矩**在代码层面也守得住** —— 否则下一次有人图省事在调用点补一个 ``"gemini-3.5-flash"``，
「从网关摘掉的模型还会跑到一半才死」就会原地复发。

四条规则：

1. **目录核心模块零真实模型名**：目录层 / 解析层 / 注册层 / 投影层文件里不许出现任何
   真实模型名字面量（``DC-*`` 槽位键除外 —— 它是路由键，不是模型名）。
2. **其余真实模型名必须逐条登记**：白名单之外的字面量一律失败；白名单里的条目若已不在
   代码里出现同样失败（棘轮，不留死条目）。
3. **解析入口调用点禁止真名**：``resolve_slot`` / ``get_newapi_text_*`` 的字符串实参只能
   是 env 键、``DC-*`` 槽位键或能力档名，绝不能是被当作兜底的模型名。
4. **``*_MODEL`` 模块常量的字面默认值**必须是 ``DC-*`` 槽位键，或登记过的直连供应商参数。

直连供应商（火山方舟 ARK / HuiMeng / DashScope CosyVoice / OpenRouter / OpenAI 图像 /
Google 直连 / codex CLI / ComfyUI 工作流）不经过渠道目录，它们是「后端选择键」而不是
网关兜底名，因此进白名单并写明原因，而不是让守卫视而不见。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "novelvideo"

# 模型族前缀（小写、后面必须紧跟 ``-``：这样 ``seedance2-assets`` 这类工作流键名不会
# 被误判成模型名，而 ``seedance-2.0`` 会）。
_MODEL_FAMILIES = (
    "ap",
    "gpt",
    "o1",
    "o3",
    "o4",
    "dall-e",
    "claude",
    "gemini",
    "google",
    "doubao",
    "deepseek",
    "qwen",
    "kimi",
    "moonshot",
    "minimax",
    "glm",
    "grok",
    "mistral",
    "llama",
    "seedream",
    "seededit",
    "seedance",
    "indextts",
    "index-tts",
    "funasr",
    "paraformer",
    "cosyvoice",
    "sensevoice",
    "whisper",
    "sora",
    "veo",
    "kling",
    "hailuo",
    "text-embedding",
    "bge",
    "m3e",
    "jiomni",
    "omni",
)
_FAMILY_RE = re.compile(r"^(?:%s)-" % "|".join(re.escape(f) for f in _MODEL_FAMILIES))
# 明显不是「模型名」的字符：URL / 句子 / 正则 / 蛇形键 / 缓存前缀。
_NON_MODEL_CHARS = " \t\n_:;{}?&=+*'\"()（）／、，。："
# ``*_MODEL`` / ``*_MODELS`` / ``DEFAULT_*_MODEL_后缀``（后缀限一格，避免把
# ``_DEFAULT_HERMES_MODEL_CONTEXT_LENGTH`` 这类无关常量卷进来）。
_MODULE_NAME_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*MODEL(_?[A-Z0-9]{0,10})$")

# 目录 / 解析 / 注册 / 投影链路的核心文件：真实模型名零容忍。
CATALOG_CORE_FILES = frozenset(
    {
        "official_defaults.py",
        "model_slots.py",
        "embedding_models.py",
        "catalog/upstream_catalog.py",
        "model_gateway_settings.py",
        "newapi_provisioner.py",
        "catalog/media_model_discovery.py",
        "model_plaza.py",
    }
)

# 槽位解析入口：调用点传的第二个参数从「兜底模型名」变成「兜底槽位键」，本规则钉住它。
RESOLVER_FUNCTIONS = frozenset(
    {
        "resolve_slot",
        "resolve_media_slot",
        "get_newapi_text_model_name",
        "get_newapi_text_pydantic_model",
        "get_newapi_chat_model",
    }
)
# env 键（``HERMES_MODEL``）、内部槽位键（``DC-*``）、能力档（``text.generate.agent``）
# 是解析入口唯一允许的字符串实参。
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLOT_KEY_RE = re.compile(r"^DC-[A-Za-z0-9._-]+$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9._]*$")

# 真实模型名的合法残留面：不经过渠道目录的**直连供应商**参数与后端选择键。
# 每条都必须写原因；代码里消失后要从这里删掉（否则 ``test_..._allowlist_is_not_stale`` 失败）。
DIRECT_VENDOR_ALLOWLIST: dict[str, tuple[str, frozenset[str]]] = {
    # ---- 直连视频后端（HuiMeng / 火山方舟 ARK / Grok Imagine / Seedance2 工作流）：
    # 这些字符串是「选哪个直连 backend / 该后端的工作流键」，不是网关兜底名。
    "api/routes/generation/backends.py": (
        "直连视频后端选项与渠道内 grok 通道名",
        frozenset({"grok-video-channel", "seedance-2.0-mini"}),
    ),
    "api/routes/generation/prepare.py": (
        "直连视频后端别名 → 后端键映射",
        frozenset(
            {
                "grok-video-channel",
                "seedance-1.5-pro",
                "seedance-2.0",
                "seedance-2.0-fast",
                "seedance-2.0-fast-value",
                "seedance-2.0-value",
            }
        ),
    ),
    "freezone/video_node.py": (
        "freezone 视频节点的直连后端别名表",
        frozenset(
            {
                "seedance-2.0",
                "seedance-2.0-fast",
                "seedance-2.0-fast-value",
                "seedance-2.0-value",
            }
        ),
    ),
    "generators/huimengi.py": (
        "HuiMeng 直连支持的模型名清单（厂商 API 参数）",
        frozenset({"seedance-1.0-pro-fast", "seedance-1.5-pro", "seedance-2.0-fast"}),
    ),
    "generators/video_generator/backend_utils.py": (
        "各直连后端的后端键与 google 直连翻译参数",
        frozenset(
            {
                "gemini-3.5-flash",
                "grok-video-channel",
                "seedance-1.0-pro-fast",
                "seedance-1.5-pro",
                "seedance-2.0",
                "seedance-2.0-fast",
                "seedance-2.0-fast-value",
                "seedance-2.0-mini",
                "seedance-2.0-value",
                "seedance-2.5",
            }
        ),
    ),
    "generators/video_generator/backends/grok.py": (
        "xAI Grok Imagine 直连模型参数",
        frozenset({"grok-imagine-video"}),
    ),
    "generators/video_generator/backends/huimeng.py": (
        "HuiMeng 直连后端名判定（含前缀片段）",
        frozenset({"seedance-1.0-pro-fast", "seedance-1.5", "seedance-2."}),
    ),
    "generators/video_generator/backends/newapi.py": (
        "网关视频后端串里内嵌的直连通道名与前缀判定",
        frozenset({"grok-video-channel", "seedance-2."}),
    ),
    "generators/video_generator/backends/seedance2.py": (
        "Seedance2 直连工作流模型参数",
        frozenset({"seedance-2.0", "seedance-2.0-i2v"}),
    ),
    "seedance2_i2v/pipeline.py": (
        "Seedance2 直连流程默认工作流参数",
        frozenset({"seedance-2.0"}),
    ),
    "usage/video_duration.py": (
        "按已选中直连模型名派生的时长区间键",
        frozenset({"seedance-2.0-mini"}),
    ),
    # ---- 直连图像 / 语音供应商（OpenAI 图像、OpenRouter、火山方舟、DashScope）：
    # 这些是厂商 API 的 model 参数，不经渠道目录。
    "config.py": (
        "直连供应商 env 默认值 + EE 官方网关随包清单 + 名字前缀判定",
        frozenset(
            {
                "cosyvoice-v3-flash",
                "doubao-seedance-1-0-pro-fast-251015",
                "doubao-seedance-1-5-pro-251215",
                "doubao-seededit-3-0-i2i-250628",
                "doubao-seedream-4-5-251128",
                "gemini-",
                "gemini-3.1-flash-image-preview",
                "gpt-5.4-image-2",
                "gpt-image-2",
                "seedance-1.0-pro-fast",
                "seedance-1.5-pro",
                "seedance-2.0",
                "seedance-2.0-fast",
                "seedance-2.0-fast-value",
                "seedance-2.0-mini",
                "seedance-2.0-value",
                "seedance-2.5",
            }
        ),
    ),
    "freezone/jobs.py": (
        "OpenAI 图像直连的 model 参数",
        frozenset({"gpt-image-2"}),
    ),
    "generators/grid_generation/image_sizes.py": (
        "图像尺寸表按厂商模型名前缀分档（含前缀片段）",
        frozenset({"gpt-image", "gpt-image-2", "seedream-"}),
    ),
    "generators/grid_generation/provider_text_api.py": (
        "OpenAI 图像直连的 model 参数",
        frozenset({"gpt-image-2"}),
    ),
    "generators/scene_reference_images.py": (
        "OpenAI 图像直连的 model 参数",
        frozenset({"gpt-image-2"}),
    ),
    "generators/tts_generator.py": (
        "DashScope CosyVoice 直连 model 参数",
        frozenset({"cosyvoice-v3-plus"}),
    ),
    "stage_asset_tasks.py": (
        # [aipdd fork delta wave2] 场景 360 / 资产图那个内置名已随「按用途钉图」的 env 一起退出
        # 解析链，只剩空间契约直连 OpenRouter 的 --model 参数。
        "空间契约直连参数（不经网关，见本段头的 wave3 归档说明）",
        frozenset({"gpt-5.5"}),
    ),
    # ---- 不经网关的宿主 CLI / 直连 LLM 参数：codex CLI 与 google / OpenRouter 校验位。
    #
    # [aipdd fork delta wave3 归档] 下面这些名字**故意**不迁进「设置 › 模型」，不是漏网的写死：
    #
    # * 部署内不可达（实测）：运行中的 aipdd-drama-api 里 OPENROUTER_API_KEY / MODEL_PROVIDER /
    #   MODEL_NAME / SCENE_SPATIAL_CONTRACT_MODEL / OPENROUTER_VISION_MODEL / SKETCH_GATE_MODEL /
    #   VOXEL_VLM_MODEL / BLOCK_WORLD_MODEL 全为空，compose 也不透传；空间契约与重叠分析在调用点
    #   还额外 gating（stage_asset_tasks 要求 OPENROUTER_API_KEY 在场），所以这些默认名当前
    #   一个都不会被送到上游 —— 它们是死默认名，不是隐蔽在跑的网关调用。
    # * 边界另有用例钉着，不缺安全性：test_p0g4l_scene_360_subprocess_egress 断言 org 轨直接
    #   DENY 不起子进程、平台轨起进程但平台密钥被从子进程 env 剥掉。
    # * 真正的障碍是**名字与端点解耦**：`model_slots.resolve_slot` 给的是「网关可路由真名」，
    #   把它打给 OpenRouter / Gemini / 宿主 codex CLI 的直连端点只会制造 404/400 的新故障，
    #   且这几处覆盖极薄，坏掉表现为「偶发质量下降」而非报错 —— 半迁移比不迁移更糟。
    #
    # 要动就一次动完（名字 / base_url / 鉴权同一个 commit 成对出现，做完删掉对应登记条目）：
    # ① 先把 OpenRouter 之类在「设置 › 模型网关」配成一条 OpenAI 兼容渠道（baseUrl 归一会自动补
    # /v1，清单走 /v1/models，不需要改代码）；② 调用点整体改走网关，凭证按
    # task_backend.subprocesses.build_model_child_env 的收口注入子进程，不走「透传父环境」；
    # ③ 名字改用槽位键 + 目录绑定解析，禁止再传字面量（规则 3 的调用点用例也会跟着放行）。
    #
    # 触发条件（任一成立才动）：部署里真的配了该供应商 Key 并要启用这条能力；org 多用户要把它
    # 收进网关；或这个默认名下架导致 503/404。
    "chat/service.py": (
        "宿主 codex CLI 的 --model 参数（不是网关调用名）",
        frozenset({"gpt-5.4"}),
    ),
    "chat/backend_sdk.py": (
        "Anthropic 直连 SDK 模型前缀判定",
        frozenset({"claude-"}),
    ),
    "director_world/block_world_builder.py": (
        "codex CLI 直调的 --model 参数（宿主 CLI，不经网关，见 wave3 归档说明）",
        frozenset({"gpt-5.4"}),
    ),
    "director_world/scene_overlap_analyzer.py": (
        "google 直连（GOOGLE_API_KEY）的 model 参数（部署内未配该密钥，见 wave3 归档说明）",
        frozenset({"gemini-3.5-flash"}),
    ),
    "director_world/scene_spatial_contract.py": (
        "OpenRouter 直连的 model 参数（部署内未配该密钥，见 wave3 归档说明）",
        frozenset({"gpt-5.5"}),
    ),
    "generators/scene_voxel/codegen_agent.py": (
        "OpenRouter 直连的 model 参数（部署内未配该密钥，见 wave3 归档说明）",
        frozenset({"gpt-5.5"}),
    ),
    "verification/sketch_visual_gate.py": (
        "google / OpenRouter 直连校验位的 model 参数（密钥缺失时整道门跳过，见 wave3 归档说明）",
        frozenset({"gemini-3.5-flash"}),
    ),
    # ---- 遗留分类兜底：渠道没声明元数据时按名字归类（计划 §5 明确保留为兜底）。
    "api/routes/model_gateway/media.py": (
        "面板未传 mediaType 时的名字特征归类兜底 + official 渠道独占名",
        frozenset(
            {
                "doubao-seedream",
                "index-tts-2",
                "seedance-2.0-fast-value",
                "seedance-2.0-value",
                "seedream-",
            }
        ),
    ),
    "api/routes/model_gateway/comfyui.py": (
        "ComfyUI 工作流按渠道模型名分档（type=63 无 OpenAI 目录）",
        frozenset({"minimax-h3-local"}),
    ),
    "freezone/douyin_copywrite.py": (
        "ASR 端点/能力按名字前缀判定的兜底",
        frozenset({"doubao-asr-", "funasr-"}),
    ),
    "generators/character_generation.py": (
        "图像模型名前缀判定（风格族识别）",
        frozenset({"gemini-3"}),
    ),
    "generators/grid_generation/dispatch.py": (
        "图像模型名前缀判定（派发分档）",
        frozenset({"gemini-3"}),
    ),
    "generators/grid_generation/grid_generator.py": (
        "图像模型名前缀判定（网格尺寸分档）",
        frozenset({"gemini-3"}),
    ),
    "generators/prop_generation.py": (
        "图像模型名前缀判定（道具参考图分档）",
        frozenset({"gemini-3"}),
    ),
}

# 规则 4 的例外：模块级 ``*_MODEL`` 常量保留直连供应商默认值（文件 → 常量名）。
MODULE_CONSTANT_ALLOWLIST: dict[str, frozenset[str]] = {
    "config.py": frozenset(
        {
            "SEEDREAM_MODEL",
            "SEEDEDIT_MODEL",
            "COSYVOICE_MODEL",
            "SEEDANCE_FAST_MODEL",
            "SEEDANCE_PRO_MODEL",
            "NANOBANANA_MODEL",
            "OPENAI_IMAGE_MODEL",
            "OPENROUTER_GPT_IMAGE2_MODEL",
            "OPENROUTER_NANOBANANA2_MODEL",
            "HUIMENG_IMAGE_MODEL",
            "HUIMENG_IMAGE_OFFICIAL_MODEL",
            "HUIMENG_NANOBANANA2_MODEL",
            # EE 官方网关随包清单与「按已选中模型名派生」的运营参数：CE 下不参与
            # 候选（候选只来自渠道目录），仍允许运维用 env 覆盖。
            "NEWAPI_VIDEO_MODELS",
            "NEWAPI_VIDEO_AUDIO_MODELS",
        }
    ),
    "director_world/scene_overlap_analyzer.py": frozenset({"DEFAULT_MODEL"}),
    "director_world/scene_spatial_contract.py": frozenset({"DEFAULT_MODEL"}),
    "generators/scene_voxel/codegen_agent.py": frozenset({"_MODEL"}),
    "stage_asset_tasks.py": frozenset({"SPATIAL_CONTRACT_DEFAULT_MODEL"}),
    "verification/sketch_visual_gate.py": frozenset(
        {"DEFAULT_GATE_MODEL_GOOGLE", "DEFAULT_GATE_MODEL_OPENROUTER"}
    ),
}


def _iter_module_files() -> list[Path]:
    return sorted(
        path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


def model_id_tokens(value: str) -> list[str]:
    """一条字符串字面量里像**真实模型名**的片段（逗号清单会逐个拆）。"""

    text = str(value).strip()
    if not text or len(text) > 240:
        return []
    if any(char in text for char in _NON_MODEL_CHARS):
        return []
    if text.startswith("/") or text.startswith("http") or text.count("/") > 1:
        return []
    tokens: list[str] = []
    for chunk in (item.strip() for item in text.split(",")):
        if not chunk or chunk != chunk.lower() or chunk.startswith("dc-"):
            continue
        segment = chunk.split("/")[-1]
        if _FAMILY_RE.match(segment):
            tokens.append(segment)
    return tokens


def _string_literal_tokens(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(model_id_tokens(node.value))
    return found


def _literal_default(node: ast.AST) -> str | None:
    """常量赋值里的字符串默认值（含 ``os.environ.get(KEY, "<默认>")`` 形态）。"""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and len(node.args) > 1:
        last = node.args[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            return last.value
        return None
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        for value in reversed(node.values):
            got = _literal_default(value)
            if got:
                return got
    return None


def _scanner_found_by_file() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _iter_module_files():
        tokens = _string_literal_tokens(_parse(path))
        if tokens:
            found[_module_rel(path)] = tokens
    return found


# ---------------------------------------------------------------------------
# 规则 1：目录 / 解析 / 注册 / 投影核心文件里不许出现任何真实模型名。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("file_name", sorted(CATALOG_CORE_FILES))
def test_catalog_core_module_has_no_hardcoded_model_names(file_name: str) -> None:
    path = SRC_ROOT / file_name
    assert path.exists(), f"目录核心文件不存在（被改名/删除请同步本守卫）：{path}"
    tokens = _string_literal_tokens(_parse(path))
    assert not tokens, (
        f"{file_name} 属于「模型名只能来自渠道目录」的核心链路，出现真实模型名字面量"
        f"就意味着渠道下架该模型后平台还会去调它：{sorted(tokens)}"
    )


def test_catalog_core_files_are_not_in_direct_vendor_allowlist() -> None:
    """两条规则不能互相打折：核心文件一旦进白名单，规则 1 就形同虚设。"""

    overlap = sorted(CATALOG_CORE_FILES & set(DIRECT_VENDOR_ALLOWLIST))
    assert not overlap, f"核心链路文件不应出现在直连白名单里：{overlap}"


# ---------------------------------------------------------------------------
# 规则 2：全库真实模型名逐条登记，不留未登记面、也不留死条目。
# ---------------------------------------------------------------------------


def test_every_hardcoded_model_name_is_declared_as_direct_vendor() -> None:
    found = _scanner_found_by_file()
    unexpected = {
        rel: sorted(tokens - set(DIRECT_VENDOR_ALLOWLIST.get(rel, ("", frozenset()))[1]))
        for rel, tokens in found.items()
        if rel not in CATALOG_CORE_FILES
    }
    offenders = {rel: tokens for rel, tokens in unexpected.items() if tokens}
    assert not offenders, (
        "这些真实模型名字面量没有登记在直连供应商白名单里。渠道目录才是模型名的"
        "真源：如果它参与网关调用，必须改成槽位键 + 目录绑定；确认是直连供应商参数"
        "（不经渠道目录）才加进 DIRECT_VENDOR_ALLOWLIST 并写清原因。"
        f"\n{offenders}"
    )


def test_direct_vendor_allowlist_is_not_stale() -> None:
    found = _scanner_found_by_file()
    stale = {
        rel: sorted(tokens - found.get(rel, set()))
        for rel, (_reason, tokens) in DIRECT_VENDOR_ALLOWLIST.items()
    }
    stale = {rel: tokens for rel, tokens in stale.items() if tokens}
    assert not stale, (
        f"白名单里有已经不存在于代码中的模型名（顺手删掉，别留给下一个人误会）：{stale}"
    )


# ---------------------------------------------------------------------------
# 规则 3：解析入口调用点绝不传真实模型名当兜底。
# ---------------------------------------------------------------------------


def _resolver_call_literals(tree: ast.Module) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in RESOLVER_FUNCTIONS:
            continue
        literals = [
            argument.value
            for argument in node.args
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        ]
        literals += [
            keyword.value.value
            for keyword in node.keywords
            if isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ]
        for literal in literals:
            rows.append((node.lineno, str(name), literal))
    return rows


def _is_allowed_resolver_literal(literal: str) -> bool:
    return bool(
        _ENV_KEY_RE.match(literal)
        or _SLOT_KEY_RE.match(literal)
        or _CAPABILITY_RE.match(literal)
    )


def test_resolver_call_sites_only_pass_env_keys_slot_keys_or_capabilities() -> None:
    offenders: list[str] = []
    total = 0
    for path in _iter_module_files():
        tree = _parse(path)
        for lineno, func_name, literal in _resolver_call_literals(tree):
            if func_name == "resolve_media_slot" and literal in {"image", "video", "audio", "asr"}:
                continue
            total += 1
            if not _is_allowed_resolver_literal(literal):
                offenders.append(f"{_module_rel(path)}:{lineno} {func_name}({literal!r})")
    assert total >= 20, f"解析入口调用点扫描异常（只找到 {total} 处），守卫可能失效"
    assert not offenders, (
        "这些解析入口调用点传了真实模型名当兜底 —— req40 之后第二参数只能是 "
        "``DC-*`` 槽位键（target 由「设置 › 模型」+ 渠道目录决定），"
        "否则网关摘掉名字时又会跑到一半才死：\n" + "\n".join(offenders)
    )


def test_resolver_functions_are_the_only_place_slot_resolution_happens() -> None:
    """核心解析函数必须存在且导出：守卫的名单不能对着空气生效。"""

    from novelvideo import config as app_config
    from novelvideo import model_slots

    for name in ("resolve_slot", "resolve_media_slot"):
        assert hasattr(model_slots, name), f"model_slots 缺少 {name}"
    for name in ("get_newapi_text_model_name", "get_newapi_text_pydantic_model"):
        assert hasattr(app_config, name), f"config 缺少 {name}"


# ---------------------------------------------------------------------------
# 规则 4：``*_MODEL`` 模块常量的字面默认值只能是槽位键或登记过的直连参数。
# ---------------------------------------------------------------------------


def test_module_level_model_constants_are_slot_keys_or_direct_vendor() -> None:
    offenders: list[str] = []
    for path in _iter_module_files():
        rel = _module_rel(path)
        allowed = MODULE_CONSTANT_ALLOWLIST.get(rel, frozenset())
        tree = _parse(path)
        for node in tree.body:
            targets: list[ast.AST]
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            for target in targets:
                if not (isinstance(target, ast.Name) and _MODULE_NAME_RE.match(target.id)):
                    continue
                value = (_literal_default(node.value) or "").strip()
                if not value:
                    continue
                if value.lower().startswith("dc-"):
                    continue
                # 设置项 / 缓存键之类的蛇形值（``cognee_embedding_model``）不是模型名。
                if re.match(r"^[a-z][a-z0-9_]*$", value) or value.startswith("http"):
                    continue
                if target.id in allowed:
                    continue
                offenders.append(f"{rel}:{node.lineno} {target.id} = {value!r}")
    assert not offenders, (
        "模块级 ``*_MODEL`` 常量带了非槽位键的字面默认值。它一旦成为兜底路径，"
        "就等于把模型名写死回代码：请改成 ``DC-*`` 槽位键（并让 target 走渠道目录），"
        "确认是直连供应商参数才加进 MODULE_CONSTANT_ALLOWLIST：\n" + "\n".join(offenders)
    )


def test_module_constant_allowlist_is_not_stale() -> None:
    for rel, names in MODULE_CONSTANT_ALLOWLIST.items():
        path = SRC_ROOT / rel
        assert path.exists(), f"白名单指向的文件不存在：{rel}"
        tree = _parse(path)
        present = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        missing = sorted(name for name in names if name not in present)
        assert not missing, f"{rel} 的常量白名单里有已不存在的名字：{missing}"


def test_official_defaults_no_longer_ships_a_per_env_model_table() -> None:
    """旧 ``DEFAULT_TEXT_MODEL_BY_ENV`` 是「摘掉的模型还会被注册」的源头，不得回归。"""

    from novelvideo import official_defaults

    assert not hasattr(official_defaults, "DEFAULT_TEXT_MODEL_BY_ENV")
    tree = _parse(SRC_ROOT / "official_defaults.py")
    assert _string_literal_tokens(tree) == set()


# 供人工核对用：当前登记在册的残留面（不做断言，只在 -v 输出里能看见）。
def test_direct_vendor_allowlist_shape_is_sane() -> None:
    assert DIRECT_VENDOR_ALLOWLIST, "白名单不该是空的"
    for rel, (reason, tokens) in DIRECT_VENDOR_ALLOWLIST.items():
        assert reason.strip(), f"{rel} 的白名单必须写原因"
        assert tokens, f"{rel} 的白名单没有模型名，删掉这条"
        assert rel.endswith(".py"), rel
        assert (SRC_ROOT / rel).exists(), f"白名单指向的文件不存在：{rel}"
