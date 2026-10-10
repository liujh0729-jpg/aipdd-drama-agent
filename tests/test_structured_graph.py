"""structured_v1 图谱抽取：证据规则、合并口径与有界快照。

图谱预览是模型抽取出来的，最容易出错的两件事在这里定死：
1. 没有逐字引用原文的实体/关系必须丢掉（模型会编造引用）；
2. 快照必须有界，且被切掉的节点不能留下悬空的边——否则前端画出一个不存在的节点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.graph_preview import load_graph_preview, write_graph_preview
from novelvideo.story_analysis import SourceChunk
from novelvideo.structured_graph import (
    GRAPH_ANALYSIS_VERSION,
    GRAPH_ARTIFACT_TYPE,
    GRAPH_MAX_NODES,
    ChunkGraphOutput,
    GraphEntity,
    GraphRelation,
    MergedEntity,
    MergedRelation,
    _encode_snapshot,
    build_graph_snapshot,
    decode_snapshot,
    merge_graph_candidates,
    normalize_relation,
    quote_supported,
)

pytestmark = pytest.mark.m07


def _chunk(
    text: str,
    *,
    chunk_id: str = "scene-0001",
    index: int = 0,
    label: str = "1-1",
    section_type: str = "scene",
) -> SourceChunk:
    return SourceChunk(
        chunk_id=chunk_id,
        chunk_index=index,
        section_type=section_type,  # type: ignore[arg-type]
        section_label=label,
        source_start=0,
        source_end=len(text),
        text=text,
    )


def _out(entities=(), relations=()) -> ChunkGraphOutput:
    return ChunkGraphOutput(entities=list(entities), relations=list(relations))


def test_quote_must_be_verbatim_in_the_chunk() -> None:
    chunk = _chunk("林默推开门。")

    assert quote_supported(chunk, "林默推开门。") is True
    # 改写一个字都不行：这正是证据规则要拦的东西。
    assert quote_supported(chunk, "林默推开了门。") is False
    # 太短的字串没有鉴别力。
    assert quote_supported(chunk, "林") is False
    assert quote_supported(chunk, "") is False


def test_quote_matching_ignores_line_wrapping() -> None:
    chunk = _chunk("林默\n推开门，看见\n母亲站在窗边。")

    entities, _ = merge_graph_candidates(
        [
            (
                chunk,
                _out(
                    [
                        GraphEntity(
                            name="林默",
                            type="Character",
                            quote="林默推开门，看见母亲站在窗边。",
                        )
                    ]
                ),
            )
        ]
    )

    assert [item.name for item in entities] == ["林默"]


def test_generic_kinship_term_is_not_an_entity_and_its_edge_goes_too() -> None:
    quote = "林默推开门，看见母亲站在窗边。"
    chunk = _chunk(quote)

    entities, relations = merge_graph_candidates(
        [
            (
                chunk,
                _out(
                    [
                        GraphEntity(name="林默", type="Character", quote=quote),
                        GraphEntity(name="母亲", type="Character", quote=quote),
                    ],
                    [
                        GraphRelation(
                            source="林默", target="母亲", relation="son_of", quote=quote
                        )
                    ],
                ),
            )
        ]
    )

    assert [item.name for item in entities] == ["林默"]
    # 端点没活下来的关系必须一起丢掉，否则预览里会出现指向画不出来节点的边。
    assert relations == []


def test_fabricated_quote_drops_the_entity_but_keeps_the_supported_one() -> None:
    chunk = _chunk("林默推开门。苏晚在楼下等。")

    entities, _ = merge_graph_candidates(
        [
            (
                chunk,
                _out(
                    [
                        GraphEntity(name="林默", type="Character", quote="林默推开门。"),
                        GraphEntity(name="陈七", type="Character", quote="陈七也来了。"),
                    ]
                ),
            )
        ]
    )

    assert [item.name for item in entities] == ["林默"]


def test_merge_collapses_names_and_keeps_the_heavier_type() -> None:
    first = _chunk("林默推开门。", chunk_id="scene-0001")
    second = _chunk("林默走出巷口。", chunk_id="scene-0002", index=1, label="1-2")

    entities, _ = merge_graph_candidates(
        [
            (first, _out([GraphEntity(name="林默", type="Concept", quote="林默推开门。")])),
            (
                second,
                _out([GraphEntity(name="「林默」", type="character", quote="林默走出巷口。")]),
            ),
        ]
    )

    assert len(entities) == 1
    node = entities[0]
    assert node.name == "林默"
    # 大小写归一后按权重取胜：Character 盖掉 Concept。
    assert node.type == "Character"
    assert node.mentions == 2
    assert node.chunk_ids == {"scene-0001", "scene-0002"}
    assert len(node.evidence) == 2


def test_evidence_is_capped_per_entity() -> None:
    outcomes = [
        (
            _chunk(f"林默第{n}次出现。", chunk_id=f"scene-{n:04d}", index=n, label=f"1-{n}"),
            _out(
                [
                    GraphEntity(
                        name="林默", type="Character", quote=f"林默第{n}次出现。"
                    )
                ]
            ),
        )
        for n in range(5)
    ]

    entities, _ = merge_graph_candidates(outcomes)

    assert entities[0].mentions == 5
    assert len(entities[0].evidence) == 3


def test_relation_label_normalization_and_default() -> None:
    assert normalize_relation("Mother Of") == "mother_of"
    assert normalize_relation("   owns ") == "owns"
    assert normalize_relation("") == "related_to"
    assert normalize_relation("，。、") == "related_to"
    assert len(normalize_relation("关" * 200)) == 60


def test_self_loop_is_dropped() -> None:
    quote = "林默犹豫着。"
    chunk = _chunk(quote)

    entities, relations = merge_graph_candidates(
        [
            (
                chunk,
                _out(
                    [GraphEntity(name="林默", type="Character", quote=quote)],
                    [GraphRelation(source="林默", target="林默", relation="hesitates", quote=quote)],
                ),
            )
        ]
    )

    assert len(entities) == 1
    assert relations == []


def _wide_graph(count: int = 60):
    hub = f"角色{count - 1}"
    entities = [
        MergedEntity(name=f"角色{i}", type="Character", mentions=i, chunk_ids={"scene-0001"})
        for i in range(count)
    ]
    relations = [
        MergedRelation(source=hub, target=f"角色{i}", relation="knows", mentions=1)
        for i in range(count - 1)
    ]
    return entities, relations


def test_snapshot_is_bounded_by_the_viewer_cap() -> None:
    entities, relations = _wide_graph()

    snapshot = build_graph_snapshot(entities, relations)

    assert len(snapshot["nodes"]) == GRAPH_MAX_NODES
    assert snapshot["total_nodes"] == len(entities)
    assert snapshot["truncated"] is True
    # 排名以连通度优先：被切掉的是叶子，枢纽一定留下。
    labels = {node["label"] for node in snapshot["nodes"]}
    assert "角色59" in labels
    assert len(snapshot["edges"]) == len(labels) - 1


def test_snapshot_edges_only_reference_kept_nodes() -> None:
    entities, relations = _wide_graph()

    snapshot = build_graph_snapshot(entities, relations)

    node_ids = {node["id"] for node in snapshot["nodes"]}
    assert ({edge["source"] for edge in snapshot["edges"]} | {edge["target"] for edge in snapshot["edges"]}) <= node_ids
    assert {edge["relation"] for edge in snapshot["edges"]} == {"knows"}


def test_snapshot_node_and_edge_schema_matches_the_sidecar() -> None:
    entities, relations = _wide_graph(count=25)

    snapshot = build_graph_snapshot(entities, relations)

    assert set(snapshot) == {
        "nodes",
        "edges",
        "total_nodes",
        "total_edges",
        "truncated",
    }
    node = snapshot["nodes"][0]
    assert set(node) == {"id", "label", "type", "degree", "properties"}
    # type 必须是英文：可视化组件原样渲染它，中文标签在英文界面就是漏译。
    assert node["type"] == "Character"
    assert node["properties"]["source"] == "structured_v1"
    assert set(node["properties"]) == {"mentions", "sections", "evidence", "source"}
    edge = snapshot["edges"][0]
    assert set(edge) == {"id", "source", "target", "relation", "properties"}


def test_snapshot_clamps_an_absurd_node_cap() -> None:
    entities, relations = _wide_graph()

    assert len(build_graph_snapshot(entities, relations, max_nodes=5)["nodes"]) == 20

    roomy, edges = _wide_graph(100)
    assert len(build_graph_snapshot(roomy, edges, max_nodes=500)["nodes"]) == 80


def test_graph_run_uses_a_pipeline_version_of_its_own() -> None:
    """chunk 结果每个 (run, chunk) 只有一个 result_json 槽位。

    图谱若与角色共用 pipeline_version，建图会把角色那批 chunk 缓存整行覆盖掉。
    """
    from novelvideo.structured_ingest import STRUCTURED_ANALYSIS_VERSION

    assert GRAPH_ANALYSIS_VERSION != STRUCTURED_ANALYSIS_VERSION
    assert GRAPH_ARTIFACT_TYPE == "graph"


def test_snapshot_artifact_roundtrip() -> None:
    entities, relations = _wide_graph(count=22)
    snapshot = build_graph_snapshot(entities, relations)

    restored = decode_snapshot(_encode_snapshot(snapshot))

    assert restored == snapshot
    assert decode_snapshot("") is None
    assert decode_snapshot("{") is None
    assert decode_snapshot('{"nodes": "nope"}') is None


class _FakeStore:
    """只实现 build_graph_structured 用到的那几个 store 方法。"""

    def __init__(self, tmp_path: Path, *, done_chunks: dict[str, dict] | None = None) -> None:
        self.project_dir = tmp_path / "output"
        self.state_dir = tmp_path / "state"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.started: dict | None = None
        self.artifacts: list[tuple[str, str, str]] = []
        self.finished: tuple[str, str] | None = None
        self._done = done_chunks or {}

    async def get_reusable_analysis_run(self, **_kwargs):
        return None

    async def start_analysis_run(self, **kwargs) -> None:
        self.started = kwargs

    async def list_analysis_chunks(self, _run_id: str, *, status: str = "done"):
        assert status == "done"
        return list(self._done.values())

    async def mark_analysis_chunk_done(self, _run_id, _chunk_id, _result) -> None:
        return None

    async def mark_analysis_chunk_failed(self, _run_id, _chunk_id, _error) -> None:
        return None

    async def save_analysis_artifact(self, run_id, artifact_type, payload) -> None:
        self.artifacts.append((run_id, artifact_type, payload))

    async def finish_analysis_run(self, _run_id, *, status="completed", error="") -> None:
        self.finished = (status, error)


_DRAMA_TEXT = """第一集

1-1 巷口 日 外
林默推开门，看见母亲站在窗边。
苏晚在楼下等他。
"""


def _patch_import(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    from novelvideo import structured_graph

    monkeypatch.setattr(structured_graph, "require_imported_novel", lambda _dir: text)
    monkeypatch.setattr(
        structured_graph,
        "chunk_source_text",
        lambda _text, _template: [
            _chunk(line.strip(), chunk_id=f"scene-{i:04d}", index=i, label=f"1-{i}")
            for i, line in enumerate(
                [ln for ln in text.splitlines() if ln.strip()]
            )
        ],
    )
    monkeypatch.setattr(
        "novelvideo.structured_builders.spine_template_for", lambda _store: "drama"
    )


async def test_build_graph_structured_pays_for_its_own_run_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_graph

    store = _FakeStore(tmp_path)
    _patch_import(monkeypatch, _DRAMA_TEXT)

    async def fake_extract(pending, **_kwargs):
        quote = pending[0].text
        entities = [
            MergedEntity(
                name="林默",
                type="Character",
                mentions=1,
                evidence=[quote],
                chunk_ids={pending[0].chunk_id},
            ),
            MergedEntity(
                name="苏晚",
                type="Character",
                mentions=1,
                evidence=[quote],
                chunk_ids={pending[0].chunk_id},
            ),
        ]
        relations = [
            MergedRelation(
                source="林默", target="苏晚", relation="knows", evidence=[quote], mentions=1
            )
        ]
        return (entities, relations, [])

    monkeypatch.setattr(structured_graph, "extract_graph_from_chunks", fake_extract)

    result = await structured_graph.build_graph_structured(store)

    assert result["published"] is True
    assert store.started["pipeline_version"] == GRAPH_ANALYSIS_VERSION
    assert store.finished == ("completed", "")
    assert store.artifacts[0][1] == GRAPH_ARTIFACT_TYPE
    # 侧车真的落盘了，GET /ingest/graph 读的就是这个文件。
    published = load_graph_preview(store.state_dir)
    assert published is not None
    assert [node["label"] for node in published["nodes"]] == ["林默", "苏晚"]
    assert len(published["edges"]) == 1


async def test_build_graph_structured_keeps_the_existing_preview_when_nothing_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_graph

    store = _FakeStore(tmp_path)
    _patch_import(monkeypatch, _DRAMA_TEXT)
    write_graph_preview(
        store.state_dir,
        {
            "nodes": [{"id": "entity-old", "label": "旧图", "type": "Character", "degree": 0, "properties": {}}],
            "edges": [],
            "total_nodes": 1,
            "total_edges": 0,
            "truncated": False,
        },
    )

    async def fake_extract(_pending, **_kwargs):
        return ([], [], [])

    monkeypatch.setattr(structured_graph, "extract_graph_from_chunks", fake_extract)

    result = await structured_graph.build_graph_structured(store)

    # 一次空跑不能把项目已有的图谱抹掉。
    assert result["published"] is False
    assert result["nodes"] == []
    assert [node["label"] for node in load_graph_preview(store.state_dir)["nodes"]] == ["旧图"]
