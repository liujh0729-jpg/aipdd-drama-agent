"""Dual-track knowledge pipeline boundary.

The contract this file defends:

* legacy projects behave exactly as before, including projects that predate the
  ``knowledge_pipeline`` field entirely;
* structured_v1 projects can still use the store as a plain SQLite facade,
  because most runners and API routes depend on it that way;
* structured_v1 projects can never reach Cognee, an embedding binding, or the
  graph — the attempt must raise rather than silently fall back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.knowledge_pipeline import (
    COGNEE_LEGACY,
    KNOWLEDGE_PIPELINE_KEY,
    KnowledgePipelineUnsupported,
    KNOWLEDGE_PIPELINE_STRUCTURED,
    is_structured_pipeline,
    knowledge_pipeline_from_state_dir,
)


def _write_config(state_dir: Path, config: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project_config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )


# ── track resolution ─────────────────────────────────────────────────────────


def test_missing_config_file_is_legacy(tmp_path):
    assert knowledge_pipeline_from_state_dir(tmp_path) == COGNEE_LEGACY


def test_project_without_the_field_is_legacy(tmp_path):
    """Projects created before the field existed must not be reclassified."""
    _write_config(tmp_path, {"user": "someone", "spine_template": "drama"})
    assert knowledge_pipeline_from_state_dir(tmp_path) == COGNEE_LEGACY


def test_embedding_bound_project_is_legacy(tmp_path):
    _write_config(
        tmp_path,
        {
            "user": "someone",
            "cognee_embedding_model": "DC-cognee-embedding-v2",
            "cognee_embedding_dimension": 1024,
        },
    )
    assert knowledge_pipeline_from_state_dir(tmp_path) == COGNEE_LEGACY


def test_explicit_structured_field_is_structured(tmp_path):
    _write_config(tmp_path, {KNOWLEDGE_PIPELINE_KEY: KNOWLEDGE_PIPELINE_STRUCTURED})
    assert is_structured_pipeline(tmp_path)


def test_unknown_pipeline_value_falls_back_to_legacy(tmp_path):
    """An unrecognised value must never be treated as structured."""
    _write_config(tmp_path, {KNOWLEDGE_PIPELINE_KEY: "something-else"})
    assert knowledge_pipeline_from_state_dir(tmp_path) == COGNEE_LEGACY


def test_absent_embedding_keys_do_not_imply_structured(tmp_path):
    """Missing embedding keys are not a usable signal.

    ``ensure_cognee_embedding_binding_in_state_dir`` backfills them for legacy
    projects, so only the explicit field can be trusted.
    """
    _write_config(tmp_path, {"user": "someone"})
    assert not is_structured_pipeline(tmp_path)


def test_default_project_config_does_not_carry_the_field():
    """The field must never reach a project through the effective-config merge.

    ``_effective_project_config`` merges the defaults over stored values, so a
    default entry would silently reclassify every legacy project.
    """
    from novelvideo.project_config import _default_project_config

    assert KNOWLEDGE_PIPELINE_KEY not in _default_project_config()


def test_effective_config_merge_cannot_make_a_legacy_project_structured(tmp_path):
    from novelvideo.project_config import load_project_config_from_state_dir

    _write_config(tmp_path, {"user": "someone", "spine_template": "drama"})
    effective = load_project_config_from_state_dir(tmp_path)
    assert effective.get(KNOWLEDGE_PIPELINE_KEY, COGNEE_LEGACY) == COGNEE_LEGACY


# ── store behaviour ──────────────────────────────────────────────────────────


@pytest.fixture
def structured_store(tmp_path):
    from novelvideo.cognee.store import CogneeStore

    state_dir = tmp_path / "user" / "structured"
    _write_config(state_dir, {KNOWLEDGE_PIPELINE_KEY: KNOWLEDGE_PIPELINE_STRUCTURED})
    return CogneeStore(
        "user/structured",
        output_dir=str(state_dir),
        state_dir=str(state_dir),
    )


@pytest.fixture
def legacy_store(tmp_path):
    from novelvideo.cognee.store import CogneeStore

    state_dir = tmp_path / "user" / "legacy"
    _write_config(state_dir, {"user": "user"})
    return CogneeStore(
        "user/legacy",
        output_dir=str(state_dir),
        state_dir=str(state_dir),
    )


async def test_structured_initialize_never_binds_an_embedding_model(
    structured_store, monkeypatch
):
    """The sentinel: initialize() must not reach any Cognee/embedding entry."""
    import novelvideo.cognee.store as store_module

    def _boom(*args, **kwargs):
        raise AssertionError("structured_v1 must not touch Cognee or embeddings")

    monkeypatch.setattr(
        store_module, "ensure_cognee_embedding_binding_in_state_dir", _boom
    )
    monkeypatch.setattr(store_module, "init_cognee", _boom)
    monkeypatch.setattr(store_module, "setup", _boom)

    try:
        await structured_store.initialize()
    finally:
        await structured_store.close()


async def test_structured_initialize_writes_no_embedding_fields(structured_store):
    from novelvideo.embedding_models import (
        PROJECT_EMBEDDING_DIMENSION_KEY,
        PROJECT_EMBEDDING_MODEL_KEY,
    )

    config_path = Path(structured_store.state_dir) / "project_config.json"
    try:
        await structured_store.initialize()
    finally:
        await structured_store.close()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert PROJECT_EMBEDDING_MODEL_KEY not in config
    assert PROJECT_EMBEDDING_DIMENSION_KEY not in config
    assert config[KNOWLEDGE_PIPELINE_KEY] == KNOWLEDGE_PIPELINE_STRUCTURED


async def test_structured_store_still_works_as_a_sqlite_facade(structured_store):
    """Runners that only need SQLite must keep working after the default flips.

    Portrait, prop/scene reference, script, sketch, video, verification and the
    Freezone routes all construct a CogneeStore purely for SQLite access.
    """
    from novelvideo.cognee.pipeline import NovelCharacter

    try:
        await structured_store.initialize()
        await structured_store.load_graph_state()

        await structured_store.add_character(NovelCharacter(name="林默"))
        assert structured_store.get_character("林默") is not None
        assert structured_store.character_count == 1

        structured_store.save_novel_content("正文")
        assert structured_store.load_novel_content() == "正文"
    finally:
        await structured_store.close()


@pytest.mark.parametrize(
    "operation",
    [
        "search",
        "ingest_novel_fast",
        "build_characters_from_graph",
        "build_scenes_from_graph",
        "build_props_from_graph",
        "get_graph_snapshot",
        "materialize_graph_preview",
    ],
)
async def test_structured_graph_capabilities_raise(structured_store, operation):
    try:
        await structured_store.initialize()
        with pytest.raises(KnowledgePipelineUnsupported):
            await getattr(structured_store, operation)("query")
    finally:
        await structured_store.close()


async def test_structured_embedding_scope_raises(structured_store):
    """Guarded too: entering the scope is how a binding gets created."""
    try:
        await structured_store.initialize()
        with pytest.raises(KnowledgePipelineUnsupported):
            structured_store.embedding_model_scope()
    finally:
        await structured_store.close()


def test_uninitialized_structured_store_is_still_gated(structured_store):
    """Stores built without initialize() must resolve the track lazily."""
    with pytest.raises(KnowledgePipelineUnsupported):
        structured_store.embedding_model_scope()


def test_legacy_store_keeps_graph_capabilities(legacy_store):
    """The gate must be invisible to legacy projects."""
    assert legacy_store._cognee_enabled
    legacy_store._require_cognee("graph search")
