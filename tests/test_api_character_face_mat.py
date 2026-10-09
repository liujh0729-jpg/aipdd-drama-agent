"""[aipdd fork delta] 五官垫图（身份级 1:1 画布 4×4 网格设定表）链路契约。

覆盖：路径槽位、runner 参考图优先级与落库、异步派发路由、提示词与统一入口、身份载荷字段，
以及「三视图 + 五官垫图」本地拼接的路由与几何。
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.models import CharacterIdentity, NovelCharacter


def _write_png(path: Path, color: tuple[int, int, int] = (10, 20, 30)) -> Path:
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=color).save(payload, format="PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.getvalue())
    return path


def _character(identity_kwargs: dict | None = None) -> tuple[NovelCharacter, CharacterIdentity]:
    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        face_prompt="oval face, big eyes",
        appearance_details="red palace dress",
        **(identity_kwargs or {}),
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    return character, identity


# ---------------------------------------------------------------------------
# 路径槽位
# ---------------------------------------------------------------------------


def test_identity_face_mat_slot_path(tmp_path):
    from novelvideo.utils.path_resolver import (
        canonical_identity_face_mat_path,
        compute_identity_face_mat_path,
    )

    # 未落盘时只给槽位，不宣称存在
    assert compute_identity_face_mat_path(tmp_path, "秦", "少年") == ""
    canonical = canonical_identity_face_mat_path(tmp_path, "秦", "少年")
    assert canonical == (
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_face_mat.png"
    )
    # 身份名带角色前缀也不重复拼接
    assert canonical_identity_face_mat_path(
        tmp_path, "秦", "秦_少年"
    ) == canonical
    _write_png(canonical)
    assert compute_identity_face_mat_path(tmp_path, "秦", "少年") == str(canonical)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class _UpdateStore:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict]] = []

    async def update_character_identity(self, name: str, identity_id: str, **updates):
        self.updates.append((name, identity_id, updates))
        return True


async def _run_face_mat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, character):
    import novelvideo.generators as generators
    from novelvideo.task_backend.runners import character_image

    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"face-mat")
        return True

    monkeypatch.setattr(generators, "generate_face_mat_image_unified", fake_generate)
    store = _UpdateStore()
    output_path = await character_image._generate_identity_face_mat(
        store=store,
        character=character,
        state_dir=str(tmp_path / "_state"),
        identity_id="秦_少年",
        identity_name="",
        output_dir=tmp_path,
        style="chinese_period_drama",
        model="nanobanana",
        task_type="character_portrait",
        scope="character:秦:identity_face_mat:少年",
        update=lambda *_args: None,
    )
    return captured, store, output_path


@pytest.mark.asyncio
async def test_face_mat_runner_prefers_identity_three_view_reference(
    tmp_path, monkeypatch
):
    """[aipdd fork delta] 参考图优先级：造型三视图 > 角色 Portrait > 身份 Portrait。"""
    character, _identity = _character()
    three_view = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    )
    _write_png(tmp_path / "assets" / "characters" / "秦" / "portrait.png")
    _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_portrait.png"
    )

    captured, store, output_path = await _run_face_mat(monkeypatch, tmp_path, character)

    assert captured["reference_image_path"] == str(three_view)
    assert output_path.name == "秦_少年_face_mat.png"
    assert output_path.exists()
    assert store.updates == [("秦", "秦_少年", {"face_mat_image": str(output_path)})]
    # 临时文件收尾要清干净
    assert not [p for p in output_path.parent.iterdir() if p.name.startswith(".tmp_")]


@pytest.mark.asyncio
async def test_face_mat_runner_falls_back_to_character_portrait(tmp_path, monkeypatch):
    """没有造型三视图时回落到角色 Portrait（身份 Portrait 存在也不抢）。"""
    character, identity = _character()
    character_portrait = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "portrait.png"
    )
    _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_portrait.png"
    )

    captured, _store, _output = await _run_face_mat(monkeypatch, tmp_path, character)

    assert captured["reference_image_path"] == str(character_portrait)
    assert captured["identity_name"] == identity.identity_name
    assert "FACE: oval face, big eyes" in captured["character_prompt"]
    assert "OUTFIT & HAIR: red palace dress" in captured["character_prompt"]


@pytest.mark.asyncio
async def test_face_mat_runner_falls_back_to_identity_portrait(tmp_path, monkeypatch):
    """三视图与角色 Portrait 都没有时，身份 Portrait 兜底。"""
    character, _identity = _character()
    identity_portrait = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_portrait.png"
    )

    captured, _store, _output = await _run_face_mat(monkeypatch, tmp_path, character)

    assert captured["reference_image_path"] == str(identity_portrait)


@pytest.mark.asyncio
async def test_face_mat_runner_requires_face_reference(tmp_path, monkeypatch):
    character, _identity = _character()

    with pytest.raises(RuntimeError, match="请先为角色"):
        await _run_face_mat(monkeypatch, tmp_path, character)


@pytest.mark.asyncio
async def test_face_mat_runner_reports_generation_failure(tmp_path, monkeypatch):
    import novelvideo.generators as generators
    from novelvideo.task_backend.runners import character_image

    character, _identity = _character()
    _write_png(tmp_path / "assets" / "characters" / "秦" / "portrait.png")

    async def failing_generate(**_kwargs):
        return False

    monkeypatch.setattr(generators, "generate_face_mat_image_unified", failing_generate)
    store = _UpdateStore()
    with pytest.raises(RuntimeError, match="五官垫图生成失败"):
        await character_image._generate_identity_face_mat(
            store=store,
            character=character,
            state_dir=str(tmp_path / "_state"),
            identity_id="秦_少年",
            identity_name="",
            output_dir=tmp_path,
            style="chinese_period_drama",
            model="nanobanana",
            task_type="character_portrait",
            scope="character:秦:identity_face_mat:少年",
            update=lambda *_args: None,
        )
    assert store.updates == []


# ---------------------------------------------------------------------------
# 派发路由
# ---------------------------------------------------------------------------


class _Queued:
    def __init__(self) -> None:
        self.task_state = SimpleNamespace(task_id="task-face-mat-1")
        self.backend = "inline"
        self.queue = "default"


@pytest.mark.asyncio
async def test_face_mat_generate_async_enqueues_task(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    character, identity = _character()
    store = SimpleNamespace(get_character=lambda name: character if name == "秦" else None)
    enqueue_calls: list[dict] = []

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (
            SimpleNamespace(
                project_id="proj_demo",
                owner_username="admin",
                project_name="demo",
                output_dir=tmp_path,
                state_dir=tmp_path / "_state",
                runtime_dir=tmp_path / "_runtime",
            ),
            "admin",
            "demo",
            tmp_path,
            str(tmp_path),
            store,
        )

    class _Backend:
        async def enqueue_project_task(self, ctx, **kwargs):
            enqueue_calls.append(kwargs)
            return _Queued()

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)
    monkeypatch.setattr(
        characters, "load_project_config", lambda *_args: {"visual_style": "chinese_period_drama"}
    )
    monkeypatch.setattr(characters, "get_task_backend", lambda: _Backend())

    response = await characters.generate_identity_face_mat_async(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        body=characters.IdentityImageGenRequest(model="nanobanana"),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["task_type"] == "character_portrait"
    assert response["scope"] == "character:秦:identity_face_mat:少年"
    assert response["task_id"] == "task-face-mat-1"
    assert len(enqueue_calls) == 1
    payload = enqueue_calls[0]["payload"]
    assert payload["mode"] == "identity_face_mat"
    assert payload["identity_id"] == "秦_少年"
    assert payload["identity_name"] == identity.identity_name
    assert enqueue_calls[0]["task_type"] == "character_portrait"
    assert enqueue_calls[0]["episode"] == 0


@pytest.mark.asyncio
async def test_face_mat_generate_async_rejects_unknown_identity(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    character, _identity = _character()
    store = SimpleNamespace(get_character=lambda _name: character)

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (None, "admin", "demo", tmp_path, str(tmp_path), store)

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)

    response = await characters.generate_identity_face_mat_async(
        project="demo",
        name="秦",
        identity_id="秦_不存在",
        body=characters.IdentityImageGenRequest(),
        user={"username": "admin"},
    )

    assert response["ok"] is False
    assert "not found" in response["error"]


# ---------------------------------------------------------------------------
# 生成器 / 统一入口
# ---------------------------------------------------------------------------


def test_face_mat_prompt_is_square_grid_reference_sheet():
    from novelvideo.generators.character_generation import CharacterGenerator

    # [aipdd fork delta] 产品给的文案逐字下发、不改语义：
    # 任何对这段文案的改写都必须连本测试一起改，否则就是偏需求。
    verbatim = """将1:1尺寸的画布均匀划分成4x4的网格，严格参考参考图人物全部 8 个面部角度，1:1 完整复刻人物五官特征，8 张头部角度分别为：
1. 正面平视：人物正对镜头，双眼自然看向前方，表情平静端庄。
2. 左侧微侧脸：头部轻微转向画面左侧，保留大部分正面五官。
3. 左侧大侧脸：头部明显转向画面左侧，接近左侧侧面轮廓。
4. 低头俯视：头部低下，视线略微向下，呈沉思或审视状态。
5. 右侧微侧脸：头部轻微转向画面右侧，表情沉稳。
6. 右侧四分之三侧脸：头部向画面右侧转动约四分之三角度，右脸轮廓清晰。
7. 抬头仰视：头部微微抬起，视线向上，神情庄重或若有所思。
8. 低头闭眼：头部低下，双眼闭合，表情安静、平和、内敛。
画面要求：高清电影质感，人物半身头像构图，面部对焦清晰，皮肤细节自然真实纹理清楚，柔和自然光或影视侧光，背景简洁虚化，服装与头饰稳定一致，面部对称自然，无畸形五官，无多余饰品，无现代元素，无文字。【保持人物与参考图完全一致，面部特征100%相同，保持相同的骨骼结构、眼间距和下颚几何形状，禁止美化，禁止改变年龄；保持人物的头肩比与参考图百分百一致；将背景调整为均匀的浅灰色；人物不附带任何面部表情】"""

    generator = CharacterGenerator.__new__(CharacterGenerator)
    prompt = generator._build_face_mat_prompt(
        character_name="秦",
        character_prompt="FACE: oval face",
        character_tag="[秦_a1b2]",
        style_keywords="ink wash",
        negative_keywords="photo",
    )

    assert prompt == verbatim

    # 默认文案不内嵌占位符：身份靠参考图锁定
    assert prompt.startswith("将1:1尺寸的画布均匀划分成4x4的网格")
    assert "均匀划分成4x4的网格" in prompt
    assert "严格参考参考图人物全部 8 个面部角度" in prompt
    assert "8 张头部角度分别为：" in prompt
    assert "1. 正面平视" in prompt
    assert "8. 低头闭眼" in prompt
    assert "人物半身头像构图" in prompt
    assert "保持相同的骨骼结构、眼间距和下颚几何形状，禁止美化，禁止改变年龄" in prompt
    assert "将背景调整为均匀的浅灰色；人物不附带任何面部表情" in prompt
    # 旧版三视图 / 四角度脸型布局已下线
    assert "三视图" not in prompt
    assert "四个不同角度" not in prompt
    # 传进来的角色/风格变量默认不落进正文（用户在提示词库里自行加占位符才会被替换）
    assert "[秦_a1b2]" not in prompt
    assert "ink wash" not in prompt
    assert "{" not in prompt


@pytest.mark.asyncio
async def test_face_mat_unified_uses_reference_locking_channel(monkeypatch):
    from novelvideo.generators import image_generator, character_generation

    captured: dict = {}

    class FakeGenerator:
        def __init__(self, selection=None, egress_context=None) -> None:
            captured["selection"] = selection

        async def generate_face_mat_with_reference(self, **kwargs):
            captured.update(kwargs)
            return character_generation.CharacterReferenceResult(
                success=True,
                character_name=kwargs["character_name"],
                primary_reference=kwargs["output_path"],
            )

    monkeypatch.setattr(character_generation, "CharacterGenerator", FakeGenerator)
    monkeypatch.setattr(
        image_generator, "normalize_character_image_selection", lambda value: value
    )

    ok = await image_generator.generate_face_mat_image_unified(
        character_name="秦",
        character_prompt="FACE: oval face",
        reference_image_path=str(Path("/tmp/portrait.png")),
        output_path="/tmp/face_mat.png",
        model="nanobanana",
        raise_on_error=True,
    )

    assert ok is True
    # 旧值 nanobanana 不传 selection，让生成器回落到默认模型配置
    assert captured["selection"] is None
    assert captured["output_path"] == "/tmp/face_mat.png"


@pytest.mark.asyncio
async def test_face_mat_unified_rejects_models_without_face_locking(monkeypatch):
    from novelvideo.generators import image_generator

    monkeypatch.setattr(
        image_generator, "normalize_character_image_selection", lambda value: value
    )
    monkeypatch.setattr(image_generator, "get_character_image_selection", lambda: "seedream")
    monkeypatch.setattr(image_generator, "_is_valid_image_selection", lambda _value: False)

    with pytest.raises(RuntimeError, match="不支持参考图锁脸"):
        await image_generator.generate_face_mat_image_unified(
            character_name="秦",
            character_prompt="FACE: oval face",
            reference_image_path="/tmp/portrait.png",
            output_path="/tmp/face_mat.png",
            model=None,
            raise_on_error=True,
        )


# ---------------------------------------------------------------------------
# 身份载荷
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_payload_exposes_face_mat_image_url(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from novelvideo.api.routes import characters

    character, identity = _character()
    face_mat = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_face_mat.png"
    )
    store = SimpleNamespace(list_characters=lambda: _already([character]))

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (
            SimpleNamespace(project_id="proj_demo"),
            "admin",
            "demo",
            tmp_path,
            str(tmp_path),
            store,
        )

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.get_character_identities(project="demo", name="秦", user={})

    assert response["ok"] is True
    item = response["data"][0]
    assert item["face_mat_image_path"] == str(face_mat)
    assert item["face_mat_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/秦_少年_face_mat.png"
    )


def _already(value):
    async def _awaitable():
        return value

    return _awaitable()


# ---------------------------------------------------------------------------
# [aipdd fork delta] 三视图 + 五官垫图拼接
# ---------------------------------------------------------------------------


def test_identity_composite_slot_path(tmp_path):
    from novelvideo.utils.path_resolver import (
        canonical_identity_composite_path,
        compute_identity_composite_path,
    )

    assert compute_identity_composite_path(tmp_path, "秦", "少年") == ""
    canonical = canonical_identity_composite_path(tmp_path, "秦", "少年")
    assert canonical == (
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_composite.png"
    )
    # 身份名带角色前缀也不重复拼接
    assert canonical_identity_composite_path(tmp_path, "秦", "秦_少年") == canonical
    _write_png(canonical)
    assert compute_identity_composite_path(tmp_path, "秦", "少年") == str(canonical)


def test_stitch_side_by_side_matches_height_and_keeps_aspect(tmp_path):
    from PIL import Image

    from novelvideo.utils.image_stitch import stitch_side_by_side

    left = tmp_path / "left.png"
    right = tmp_path / "right.png"
    Image.new("RGB", (100, 200), color=(255, 0, 0)).save(left)
    Image.new("RGB", (300, 100), color=(0, 255, 0)).save(right)

    out = stitch_side_by_side(left, right, tmp_path / "out" / "composite.png", gap_px=10)

    assert out == tmp_path / "out" / "composite.png"
    canvas = Image.open(out)
    # 统一到较大高度，各自保持宽高比（不拉变形）
    assert canvas.height == 200
    assert canvas.width == 100 + 10 + 600


async def _call_stitch_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, character, store):
    from novelvideo.api.routes import characters

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (
            SimpleNamespace(project_id="proj_demo"),
            "admin",
            "demo",
            tmp_path,
            str(tmp_path),
            store,
        )

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )
    return await characters.stitch_identity_composite(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )


@pytest.mark.asyncio
async def test_composite_stitch_route_composes_identity_then_face_mat(
    tmp_path, monkeypatch
):
    import novelvideo.utils.image_stitch as image_stitch

    character, _identity = _character()
    identity_image = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    )
    face_mat = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_face_mat.png"
    )
    captured: dict = {}

    def fake_stitch(left_path, right_path, output_path, **kwargs):
        captured["left"] = left_path
        captured["right"] = right_path
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"composite")
        return out

    monkeypatch.setattr(image_stitch, "stitch_side_by_side", fake_stitch)
    store = _UpdateStore()
    store.get_character = lambda _name: character  # type: ignore[attr-defined]

    response = await _call_stitch_route(monkeypatch, tmp_path, character, store)

    assert response["ok"] is True
    # 左边是造型三视图，右边是五官垫图
    assert captured["left"] == str(identity_image)
    assert captured["right"] == str(face_mat)
    target = (
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_composite.png"
    )
    assert response["data"]["composite_image_path"] == str(target)
    assert response["data"]["composite_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/秦_少年_composite.png"
    )
    assert store.updates == [
        ("秦", "秦_少年", {"composite_image": str(target)})
    ]


@pytest.mark.asyncio
async def test_composite_stitch_route_requires_both_sources(tmp_path, monkeypatch):
    character, _identity = _character()
    _write_png(tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png")
    store = _UpdateStore()
    store.get_character = lambda _name: character  # type: ignore[attr-defined]

    response = await _call_stitch_route(monkeypatch, tmp_path, character, store)

    assert response["ok"] is False
    assert "五官垫图" in response["error"]
    assert store.updates == []


@pytest.mark.asyncio
async def test_composite_stitch_route_rejects_unknown_identity(tmp_path, monkeypatch):
    character, _identity = _character()
    store = SimpleNamespace(
        get_character=lambda _name: character,
        update_character_identity=lambda *_a, **_k: _already(True),
    )

    from novelvideo.api.routes import characters

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (None, "admin", "demo", tmp_path, str(tmp_path), store)

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)

    response = await characters.stitch_identity_composite(
        project="demo",
        name="秦",
        identity_id="秦_不存在",
        user={"username": "admin"},
    )

    assert response["ok"] is False
    assert "not found" in response["error"]


@pytest.mark.asyncio
async def test_identity_payload_exposes_composite_image_url(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from novelvideo.api.routes import characters

    character, identity = _character({"composite_image": "stale-path.png"})
    _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_face_mat.png"
    )
    composite = _write_png(
        tmp_path / "assets" / "characters" / "秦" / "identities" / "秦_少年_composite.png"
    )
    store = SimpleNamespace(list_characters=lambda: _already([character]))

    @asynccontextmanager
    async def fake_scope(*_args, **_kwargs):
        yield (
            SimpleNamespace(project_id="proj_demo"),
            "admin",
            "demo",
            tmp_path,
            str(tmp_path),
            store,
        )

    monkeypatch.setattr(characters, "_character_project_scope", fake_scope)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.get_character_identities(project="demo", name="秦", user={})

    item = response["data"][0]
    assert item["composite_image_path"] == str(composite)
    assert item["composite_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/秦_少年_composite.png"
    )
    assert item["face_mat_image_url"]

