"""[aipdd fork delta] 替换人声执行叶聚焦测试。

钉住 ``novelvideo.freezone.voice_replace.run_freezone_voice_replace`` 的编排决策：
音频源直接用、视频源先经 ``run_freezone_audio_separate`` 解封装抽音轨；本地音频
送 ASR 取台词；参考音色走零样本合成（voice_ref）；产物搬到本任务专属目录。所有
网络 / 媒体叶子均打桩，不触网。出网分类（NETWORK / EG-15a、组织直连 fail-closed）
由 ``test_p0g4i_freezone_leaf_classification`` 覆盖。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone import voice_replace as module
from novelvideo.freezone.paths import outputs_dir
from novelvideo.ports.authz import BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference

# 默认候选清单：只有一个已就绪的网关候选（不含豆包），用于不关心选择顺序的用例。
_GATEWAY_ONLY_OPTIONS = [
    {"id": "gateway:volcengine:whisper-1", "kind": "gateway", "ready": True},
]


def _organization_context() -> TrustedEgressContext:
    return TrustedEgressContext(
        envelope_id="envelope-1",
        project_id="project-1",
        task_type="freezone_voice_replace",
        requester_user_id="user-1",
        root_task_id="task-1",
        admission_id="admission-1",
        admitted_at="2026-10-03T04:05:00Z",
        membership_id="membership-1",
        authz_version=11,
        billing_principal=BillingPrincipal(kind="organization", id="org-1"),
        credential=CredentialReference(
            source="organization",
            credential_id="credential-1",
            key_version=7,
            org_id="org-1",
        ),
    )


def _patch_leaf(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_dir: Path,
    asr_text: str = "原始的台词内容",
    candidate: dict | None = None,
    options: list[dict] | None = None,
) -> dict:
    """把外部叶子换成桩实现，返回记录调用与产物的字典。

    ASR 候选选择不桩掉（走真实的 ``pick_asr_candidate_for_local_audio``），只桩
    它依赖的两个只读接口：候选清单 ``list_douyin_asr_models`` 与候选解析
    ``resolve_douyin_asr_candidate``，这样选择口径也能被钉住。
    """

    seen: dict = {"transcribe": None, "speech": None, "separate": None}
    resolved_options = _GATEWAY_ONLY_OPTIONS if options is None else options

    # TTS 产物写在 audio_speech 目录（此桩直接落在临时目录，交给叶搬运）。
    speech_dir = project_dir / "speech_stem"
    speech_dir.mkdir(parents=True, exist_ok=True)
    speech_out = speech_dir / "out.mp3"

    async def fake_separate(**kwargs):
        seen["separate"] = kwargs
        extracted = speech_dir / "extracted.mp3"
        extracted.write_bytes(b"audio")
        return {"audio_path": extracted}

    async def fake_transcribe(cand, **kwargs):
        seen["transcribe"] = {"candidate": cand, **kwargs}
        return asr_text, "语音识别（Whisper-1）"

    async def fake_speech(**kwargs):
        seen["speech"] = kwargs
        speech_out.write_bytes(b"newvoice")
        return SimpleNamespace(
            audio_path=speech_out,
            duration_ms=1234,
            mime_type="audio/mpeg",
            model="indextts2",
            voice_source="user_custom",
            voice_sha256="abc",
        )

    def fake_resolve(model: str) -> dict:
        if candidate is not None and str(model) == candidate["id"]:
            return dict(candidate)
        for opt in resolved_options:
            if str(model) == str(opt.get("id") or ""):
                return {
                    "id": str(opt["id"]),
                    "kind": str(opt.get("kind") or ""),
                    "provider": str(opt.get("provider") or ""),
                    "model": str(opt.get("model") or opt.get("id") or ""),
                    "display": str(opt.get("label") or opt.get("id") or ""),
                }
        raise ValueError(f"无法识别的 ASR 模型候选: {model}")

    monkeypatch.setattr(
        "novelvideo.freezone.jobs.run_freezone_audio_separate", fake_separate
    )
    monkeypatch.setattr(
        "novelvideo.freezone.media_copywrite._transcribe", fake_transcribe
    )
    monkeypatch.setattr(
        "novelvideo.freezone.audio_node.generate_freezone_audio_speech", fake_speech
    )
    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.resolve_douyin_asr_candidate",
        fake_resolve,
    )
    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.list_douyin_asr_models",
        lambda: list(resolved_options),
    )
    seen["speech_out"] = speech_out
    return seen


async def test_audio_source_synthesizes_new_voice(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    source = project_dir / "voice.mp3"
    source.write_bytes(b"original")
    seen = _patch_leaf(monkeypatch, project_dir=project_dir)

    result = await module.run_freezone_voice_replace(
        store=object(),
        username="alice",
        project="demo",
        account_voice_username="alice",
        project_dir=project_dir,
        job_id="job-1",
        source_path=str(source),
        source_kind="audio",
        voice_ref={"scope": "user_custom", "voice_id": "fv_x"},
        asr_model="gateway:volcengine:whisper-1",
        model="",
    )

    # 音频源不再解封装。
    assert seen["separate"] is None
    # ASR 拿到本地音频、无公网 URL。
    assert Path(seen["transcribe"]["audio_path"]) == source
    assert seen["transcribe"]["audio_url"] == ""
    # 合成拿原始台词与 voice_ref。
    assert seen["speech"]["text"] == "原始的台词内容"
    assert seen["speech"]["voice_ref"] == {"scope": "user_custom", "voice_id": "fv_x"}
    # 产物搬到本任务专属目录。
    expected = outputs_dir(project_dir, "freezone_voice_replace") / "job-1.mp3"
    assert Path(result["audio_path"]) == expected
    assert expected.exists()
    assert result["text"] == "原始的台词内容"
    assert result["duration_ms"] == 1234
    assert result["voice_source"] == "user_custom"


async def test_video_source_demuxes_audio_track_first(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    video = project_dir / "clip.mp4"
    video.write_bytes(b"video")
    seen = _patch_leaf(monkeypatch, project_dir=project_dir)

    await module.run_freezone_voice_replace(
        store=object(),
        username="alice",
        project="demo",
        account_voice_username="alice",
        project_dir=project_dir,
        job_id="job-2",
        source_path=str(video),
        source_kind="video",
        voice_ref={"scope": "user_custom", "voice_id": "fv_y"},
        asr_model="",
    )

    # 视频先解封装抽音轨，再把抽出的音轨送 ASR。
    assert seen["separate"] is not None
    assert seen["separate"]["source_path"] == str(video)
    assert Path(seen["transcribe"]["audio_path"]).name == "extracted.mp3"


async def test_missing_voice_ref_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="voice_ref"):
        await module.run_freezone_voice_replace(
            store=object(),
            username="alice",
            project="demo",
            account_voice_username="alice",
            project_dir=tmp_path,
            job_id="job-3",
            source_path=str(tmp_path / "a.mp3"),
            source_kind="audio",
            voice_ref=None,
            asr_model="",
        )


async def test_organization_falls_back_to_gateway_from_direct_channel(
    tmp_path: Path, monkeypatch
) -> None:
    """组织任务选了直连候选：自动退回网关，而不是报错。

    旧口径是直连候选 + 组织任务直接 ORG_EGRESS_DENIED 抛错；现在候选选择在
    执行时统一走 ``pick_asr_candidate_for_local_audio``，直连 kind 在组织任务下
    被过滤掉，自然落到网关 multipart 上传。
    """

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    source = project_dir / "voice.mp3"
    source.write_bytes(b"original")
    seen = _patch_leaf(
        monkeypatch,
        project_dir=project_dir,
        options=[
            {"id": "funasr:aipdd:fun-asr", "kind": "funasr", "ready": True},
            {"id": "doubao_asr:openspeech:seedasr", "kind": "doubao_asr", "ready": True},
            *_GATEWAY_ONLY_OPTIONS,
        ],
    )

    await module.run_freezone_voice_replace(
        store=object(),
        username="alice",
        project="demo",
        account_voice_username="alice",
        project_dir=project_dir,
        job_id="job-4",
        source_path=str(source),
        source_kind="audio",
        voice_ref={"scope": "user_custom", "voice_id": "fv_z"},
        asr_model="funasr:aipdd:fun-asr",
        egress_context=_organization_context(),
    )

    assert seen["transcribe"]["candidate"]["kind"] == "gateway"
    assert seen["transcribe"]["candidate"]["id"] == "gateway:volcengine:whisper-1"


async def test_no_usable_asr_channel_raises(tmp_path: Path, monkeypatch) -> None:
    """没有任何可用通道（只剩需公网直链的 FunASR / 未就绪候选）：给出可行动报错。"""

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    source = project_dir / "voice.mp3"
    source.write_bytes(b"original")
    _patch_leaf(
        monkeypatch,
        project_dir=project_dir,
        options=[
            {"id": "funasr:aipdd:fun-asr", "kind": "funasr", "ready": True},
            {"id": "gateway:volcengine:whisper-1", "kind": "gateway", "ready": False},
        ],
    )

    with pytest.raises(RuntimeError, match="没有可用的 ASR 通道"):
        await module.run_freezone_voice_replace(
            store=object(),
            username="alice",
            project="demo",
            account_voice_username="alice",
            project_dir=project_dir,
            job_id="job-4b",
            source_path=str(source),
            source_kind="audio",
            voice_ref={"scope": "user_custom", "voice_id": "fv_z"},
            asr_model="",
        )


async def test_empty_asr_text_fails(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    source = project_dir / "voice.mp3"
    source.write_bytes(b"original")
    _patch_leaf(monkeypatch, project_dir=project_dir, asr_text="   ")

    with pytest.raises(RuntimeError, match="未能识别原始台词"):
        await module.run_freezone_voice_replace(
            store=object(),
            username="alice",
            project="demo",
            account_voice_username="alice",
            project_dir=project_dir,
            job_id="job-5",
            source_path=str(source),
            source_kind="audio",
            voice_ref={"scope": "user_custom", "voice_id": "fv_w"},
            asr_model="",
        )


# ============================================================
# 本地音频的统一 ASR 候选选择（douyin_copywrite.pick_asr_candidate_for_local_audio）
# ============================================================

_MIXED_OPTIONS = [
    {"id": "funasr:aipdd:fun-asr", "kind": "funasr", "ready": True},
    {"id": "gateway:volc:whisper", "kind": "gateway", "ready": True},
    {"id": "doubao_asr:openspeech:seedasr", "kind": "doubao_asr", "ready": True},
]


def _pick(
    monkeypatch: pytest.MonkeyPatch,
    options: list[dict] | Exception,
    *,
    asr_model: str = "",
    is_organization: bool = False,
):
    """只桩候选清单，走真实的解析与选择口径。"""

    import novelvideo.freezone.douyin_copywrite as douyin

    if isinstance(options, Exception):

        def boom() -> list:
            raise options

        monkeypatch.setattr(douyin, "list_douyin_asr_models", boom)
    else:
        monkeypatch.setattr(douyin, "list_douyin_asr_models", lambda: list(options))
    return douyin.pick_asr_candidate_for_local_audio(
        asr_model, is_organization=is_organization
    )


def test_auto_pick_prefers_doubao_over_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    # 关键口径：网关候选的 ready 只代表「上游快照里有这个模型名」，不代表本地网关
    # 给它绑了可用渠道（实测 503 model_not_found），所以豆包直连就绪时优先走豆包。
    picked = _pick(monkeypatch, _MIXED_OPTIONS)
    assert picked["id"] == "doubao_asr:openspeech:seedasr"
    assert picked["kind"] == "doubao_asr"


def test_auto_pick_uses_gateway_for_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    # 组织任务不能用平台侧凭据的直连通道，只留网关 multipart 上传。
    picked = _pick(monkeypatch, _MIXED_OPTIONS, is_organization=True)
    assert picked["id"] == "gateway:volc:whisper"


def test_auto_pick_falls_back_to_gateway_without_doubao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = [
        {"id": "funasr:aipdd:fun-asr", "kind": "funasr", "ready": True},
        {"id": "gateway:volc:whisper", "kind": "gateway", "ready": True},
    ]
    # FunASR 只吃公网音频直链，本地音频场景永不参与。
    assert _pick(monkeypatch, options)["id"] == "gateway:volc:whisper"


def test_explicit_candidate_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 显式选了网关候选，即使豆包就绪也不改。
    picked = _pick(monkeypatch, _MIXED_OPTIONS, asr_model="gateway:volc:whisper")
    assert picked["id"] == "gateway:volc:whisper"


def test_explicit_unusable_candidate_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 显式指定 FunASR（本地音频吃不下）→ 退回自动选择而不是失败。
    picked = _pick(monkeypatch, _MIXED_OPTIONS, asr_model="funasr:aipdd:fun-asr")
    assert picked["id"] == "doubao_asr:openspeech:seedasr"

    # 组织任务显式指定豆包直连同样退回网关。
    org_pick = _pick(
        monkeypatch,
        _MIXED_OPTIONS,
        asr_model="doubao_asr:openspeech:seedasr",
        is_organization=True,
    )
    assert org_pick["id"] == "gateway:volc:whisper"


def test_unrecognized_candidate_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _pick(monkeypatch, _MIXED_OPTIONS, asr_model="nonsense:model")["id"] == (
        "doubao_asr:openspeech:seedasr"
    )


def test_returns_none_when_nothing_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只剩未就绪候选 / 只剩 FunASR：返回 None，由调用方决定报错或跳过。
    assert _pick(monkeypatch, [{"id": "gateway:volc:whisper", "kind": "gateway", "ready": False}]) is None
    assert _pick(monkeypatch, [{"id": "funasr:aipdd:fun-asr", "kind": "funasr", "ready": True}]) is None


def test_returns_none_when_catalog_read_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # 清单读取异常不抛出，按「无通道」降级返回 None。
    assert _pick(monkeypatch, RuntimeError("catalog unavailable")) is None
