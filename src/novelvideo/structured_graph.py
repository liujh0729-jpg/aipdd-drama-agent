"""Model-extracted story graphs for structured_v1 projects.

structured_v1 deliberately never builds a Cognee graph: import splits the text
and stops, so the ingest page had nothing to draw and ``GET /ingest/graph``
answered with an empty preview no matter how much text was on disk.  This module
builds the same bounded preview the legacy track materializes from Ladybug, but
from the structured contract instead — per-chunk extraction, quoted evidence,
deterministic merging and a degree-ranked view of at most 48 nodes.

Evidence rules are inherited from the character builder rather than invented
here: an entity or relation has to quote the span it claims, and a quote that is
not verbatim inside that span is dropped.  What that bounds is membership, not
truth — a model can pair a real sentence with the wrong pair of names, and no
check in this file can tell.

Merging is *less* conservative than the character table on purpose.  A character
name is a primary key, a REST identifier and an asset directory, so the cast must
not be guessed at.  A preview graph is disposable: a wrong merge costs one
refresh, so same-name entities collapse into one node and the type that carries
the most weight wins.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, Field

from novelvideo.i18n_message import MessageLike, lmsg
from novelvideo.novel_source import require_imported_novel
from novelvideo.story_analysis import SourceChunk, chunk_source_text, source_sha256
from novelvideo.utils.bounded_concurrency import (
    default_llm_concurrency,
    map_bounded,
)

# Bounded exactly like CogneeStore.get_graph_snapshot, so both tracks feed the
# viewer the same shape and the same worst-case payload.
GRAPH_MAX_NODES = 48
GRAPH_MAX_EDGES = 160

# Distinct from STRUCTURED_ANALYSIS_VERSION: chunk results are stored one row per
# (run, chunk) with no slot for the kind of result, so a graph run must not share
# a run id with the character build or it would overwrite those cached outputs.
GRAPH_ANALYSIS_VERSION = "structured_graph_v1"
GRAPH_SCHEMA_VERSION = 1
GRAPH_ARTIFACT_TYPE = "graph"

ENTITY_TYPES = (
    "Character",
    "Location",
    "Object",
    "Group",
    "Event",
    "Concept",
)

# Which type survives a merge, and which nodes survive the 48-node cut. A named
# person or place is the topology a reader came for; "Concept" is what the model
# falls back to and is the first thing to go.
_TYPE_PRIORITY = {
    "Character": 6,
    "Location": 5,
    "Group": 4,
    "Object": 3,
    "Event": 2,
    "Concept": 1,
}

GRAPH_EXTRACTION_SYSTEM_PROMPT = """你是剧本/小说的关系抽取器。输入是作品中的一个片段。

只根据本片段抽取，不要推测片段以外的信息。

entities：本片段中出现的、有具体名字的实体。
- name 用本片段原文写法，不要翻译或改写。
- type 只能是 Character（人物）、Location（地点或场景）、Object（道具或物品）、
  Group（组织或团体）、Event（事件）、Concept（其他专有概念）。
- quote 必须是本片段原文中逐字出现的一句话，能证明该实体出现。

relations：实体之间的关系。
- source 和 target 都必须出现在本次 entities 的 name 里。
- relation 用英文小写下划线短语，例如 mother_of、located_in、occurs_at、owns、
  works_at、argues_with。
- quote 必须是本片段原文中逐字出现的一句话，能证明这条关系。

规则：
- 不要编造引用。找不到原文依据的实体或关系不要输出。
- 旁白、画外音、镜头提示不是实体。
- 没有专名的泛称（母亲、医生、众人、某公司）不要输出。
- 关系宁少勿滥，不确定就不要输出。"""


class GraphEntity(BaseModel):
    name: str = Field(description="实体在本片段中的名字，保持原文写法")
    type: str = Field(default="Concept", description="Character / Location / Object / Group / Event / Concept")
    quote: str = Field(default="", description="证明该实体出现的原文引用，必须逐字来自本片段")


class GraphRelation(BaseModel):
    source: str = Field(description="关系主体，必须是本次 entities 中的 name")
    target: str = Field(description="关系客体，必须是本次 entities 中的 name")
    relation: str = Field(default="related_to", description="英文小写下划线关系词")
    quote: str = Field(default="", description="证明该关系的原文引用，必须逐字来自本片段")


class ChunkGraphOutput(BaseModel):
    entities: list[GraphEntity] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)


@dataclass
class MergedEntity:
    """One entity after deterministic cross-chunk merging."""

    name: str
    type: str = "Concept"
    mentions: int = 0
    evidence: list[str] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)

    @property
    def node_id(self) -> str:
        digest = hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:16]
        return f"entity-{digest}"

    @staticmethod
    def _normalize(value: str) -> str:
        return normalize_entity_name(value)

    @property
    def key(self) -> str:
        return self._normalize(self.name)


@dataclass
class MergedRelation:
    source: str
    target: str
    relation: str
    evidence: list[str] = field(default_factory=list)
    mentions: int = 0


def normalize_entity_name(value: str) -> str:
    """Collapse spacing and punctuation noise without altering the name."""
    from novelvideo.structured_extraction import normalize_character_name

    return normalize_character_name(value)


def _whitespace_insensitive(text: str) -> str:
    return "".join((text or "").split())


def quote_supported(chunk: SourceChunk, quote: str) -> bool:
    """Whether ``quote`` appears verbatim in the chunk, ignoring line wrapping.

    Models re-wrap a sentence across lines while copying it. Requiring a literal
    substring of ``chunk.text`` would drop those quotes even though the source
    does say it, so both sides collapse to their non-whitespace characters.
    Nothing else is forgiven: a paraphrase still fails.
    """
    needle = _whitespace_insensitive(quote)
    if len(needle) < 2:
        return False
    return needle in _whitespace_insensitive(chunk.text)


def _evidence(quote: str) -> str:
    text = " ".join(str(quote or "").split())
    return text[:200]


def normalize_relation(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip("：:，,。.、")
    if not cleaned:
        return "related_to"
    return cleaned.lower().replace(" ", "_")[:60]


def merge_graph_candidates(
    outcomes: Iterable[tuple[SourceChunk, ChunkGraphOutput]],
) -> tuple[list[MergedEntity], list[MergedRelation]]:
    """Fold per-chunk candidates into one entity table and one edge list.

    Names merge on the normalized form; types resolve to the higher-priority
    one; relation endpoints that did not survive as entities are dropped, so the
    preview never shows an edge to a node that was never drawn.
    """
    from novelvideo.structured_extraction import is_generic_address

    entities: dict[str, MergedEntity] = {}
    seen_quotes: dict[str, set[str]] = {}
    for chunk, output in outcomes:
        for candidate in output.entities:
            name = normalize_entity_name(candidate.name)
            if not name:
                continue
            quote_ok = quote_supported(chunk, candidate.quote)
            if not quote_ok:
                # A name the model asserts with no usable quote is exactly what
                # the evidence rule exists to stop.
                continue
            key = name
            node = entities.get(key)
            if node is None:
                node = MergedEntity(name=candidate.name.strip(), type="Concept")
                entities[key] = node
                seen_quotes[key] = set()
            candidate_type = str(candidate.type or "").strip().capitalize()
            if candidate_type in _TYPE_PRIORITY and (
                _TYPE_PRIORITY.get(candidate_type, 0)
                > _TYPE_PRIORITY.get(node.type, 0)
            ):
                node.type = candidate_type
            node.mentions += 1
            node.chunk_ids.add(chunk.chunk_id)
            evidence = _evidence(candidate.quote)
            if evidence and evidence not in seen_quotes[key]:
                seen_quotes[key].add(evidence)
                if len(node.evidence) < 3:
                    node.evidence.append(evidence)

    # Bare kinship and job terms name whoever is on stage, not a person. The
    # character table refuses to seed them; a graph that drew them would hang a
    # "母亲" node off every family in the script and call it a cast.
    for key, node in list(entities.items()):
        if node.type == "Character" and is_generic_address(node.name):
            entities.pop(key)

    ordered = sorted(
        entities.values(),
        key=lambda item: (-item.mentions, -_TYPE_PRIORITY.get(item.type, 0), item.key),
    )

    survivors = {normalize_entity_name(item.name): item for item in ordered}
    relations: dict[tuple[str, str, str], MergedRelation] = {}
    for chunk, output in outcomes:
        for candidate in output.relations:
            source_key = normalize_entity_name(candidate.source)
            target_key = normalize_entity_name(candidate.target)
            if source_key not in survivors or target_key not in survivors:
                continue
            if source_key == target_key:
                continue
            if not quote_supported(chunk, candidate.quote):
                continue
            relation = normalize_relation(candidate.relation)
            edge_key = (source_key, target_key, relation)
            edge = relations.get(edge_key)
            if edge is None:
                edge = MergedRelation(
                    source=survivors[source_key].name,
                    target=survivors[target_key].name,
                    relation=relation,
                )
                relations[edge_key] = edge
            edge.mentions += 1
            evidence = _evidence(candidate.quote)
            if evidence and evidence not in edge.evidence and len(edge.evidence) < 3:
                edge.evidence.append(evidence)

    return ordered, list(relations.values())


def build_graph_snapshot(
    entities: list[MergedEntity],
    relations: list[MergedRelation],
    *,
    max_nodes: int = GRAPH_MAX_NODES,
    max_edges: int = GRAPH_MAX_EDGES,
) -> dict[str, Any]:
    """Rank and bound the merged graph into the viewer's preview schema.

    The cut is by connectivity first: degree dominates the score so a dropped
    node is a leaf, and edges are then filtered against whatever survived, which
    keeps the preview a single component-centred picture instead of a scatter of
    orphaned names.
    """
    max_nodes = max(20, min(int(max_nodes), 80))
    max_edges = min(int(max_edges), 160)

    degree: Counter[str] = Counter()
    for edge in relations:
        degree[edge.source] += 1
        degree[edge.target] += 1

    def score(entity: MergedEntity) -> int:
        return (
            degree.get(entity.name, 0) * 10
            + _TYPE_PRIORITY.get(entity.type, 1) * 3
            + int(bool(entity.name))
        )

    ranked = sorted(entities, key=score, reverse=True)[:max_nodes]
    kept_names = {item.name for item in ranked}

    nodes = [
        {
            "id": item.node_id,
            "label": item.name[:160],
            "type": item.type,
            "degree": degree.get(item.name, 0),
            "properties": {
                "mentions": item.mentions,
                "sections": len(item.chunk_ids),
                "evidence": " / ".join(item.evidence),
                "source": "structured_v1",
            },
        }
        for item in ranked
    ]

    edges = []
    for index, edge in enumerate(relations):
        if edge.source not in kept_names or edge.target not in kept_names:
            continue
        source_id = _node_id(edge.source)
        target_id = _node_id(edge.target)
        edges.append(
            {
                "id": f"{source_id}:{target_id}:{index}",
                "source": source_id,
                "target": target_id,
                "relation": edge.relation[:120],
                "properties": {
                    "mentions": edge.mentions,
                    "evidence": " / ".join(edge.evidence),
                },
            }
        )
        if len(edges) >= max_edges:
            break

    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(entities),
        "total_edges": len(relations),
        "truncated": len(entities) > len(nodes) or len(relations) > len(edges),
    }


def _node_id(name: str) -> str:
    digest = hashlib.sha1(normalize_entity_name(name).encode("utf-8")).hexdigest()[:16]
    return f"entity-{digest}"


def _create_graph_extraction_agent(agent: Any = None):
    if agent is not None:
        return agent

    from pydantic_ai import Agent

    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )
    from novelvideo.model_gateway_runtime import model_gateway_output_retries

    # The graph is a cast-and-locations view, so it belongs to the same slot as
    # the cast: no new model position for an operator to fill.
    return Agent(
        get_newapi_text_pydantic_model(
            "CHARACTER_BUILD_MODEL",
            capability="text.generate.agent",
        ),
        system_prompt=GRAPH_EXTRACTION_SYSTEM_PROMPT,
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=ChunkGraphOutput,
        retries={"output": model_gateway_output_retries(2)},
        name="Structured Story Graph Extractor",
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def extract_graph_from_chunks(
    chunks: list[SourceChunk],
    *,
    agent: Any = None,
    concurrency: int | None = None,
    cached_outcomes: Optional[list[tuple[SourceChunk, ChunkGraphOutput]]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_chunk_done: Optional[Callable[[SourceChunk, ChunkGraphOutput], Any]] = None,
    on_chunk_failed: Optional[Callable[[SourceChunk, BaseException], Any]] = None,
) -> tuple[list[MergedEntity], list[MergedRelation], list[tuple[SourceChunk, BaseException]]]:
    """Extract entities and relations chunk by chunk, then merge.

    One unparseable chunk is reported and skipped, never fatal: a single odd
    scene must not cost the whole work its graph.
    """

    def log(message: MessageLike) -> None:
        if on_log:
            on_log(message)

    replayed = list(cached_outcomes or [])
    if not chunks:
        entities, relations = merge_graph_candidates(replayed)
        return entities, relations, []

    runner = _create_graph_extraction_agent(agent)

    async def analyse(chunk: SourceChunk) -> tuple[SourceChunk, ChunkGraphOutput]:
        from novelvideo.structured_extraction import _run_structured

        output = await _run_structured(
            runner, f"【片段 {chunk.section_label}】\n{chunk.text}"
        )
        if on_chunk_done:
            await _maybe_await(on_chunk_done(chunk, output))
        return chunk, output

    failures: list[tuple[SourceChunk, BaseException]] = []

    def record_failure(chunk: SourceChunk, exc: BaseException) -> None:
        log(
            lmsg(
                "tasks.log.graph.chunkFailed",
                f"⚠️ 片段 {chunk.section_label} 图谱抽取失败，已跳过: {exc}",
                sectionLabel=chunk.section_label,
                error=str(exc),
            )
        )
        failures.append((chunk, exc))

    outcomes = await map_bounded(
        chunks,
        analyse,
        limit=default_llm_concurrency() if concurrency is None else concurrency,
        on_error=record_failure,
    )
    for chunk, exc in failures:
        if on_chunk_failed:
            await _maybe_await(on_chunk_failed(chunk, exc))

    succeeded = [outcome for outcome in outcomes if outcome is not None]
    log(
        lmsg(
            "tasks.log.graph.chunksDone",
            f"片段图谱抽取完成: {len(succeeded)}/{len(chunks)} 成功",
            succeeded=len(succeeded),
            total=len(chunks),
        )
    )
    entities, relations = merge_graph_candidates(replayed + succeeded)
    return entities, relations, failures


async def _graph_run(store: Any, novel_text: str, spine_template: str) -> dict | None:
    """The graph run recorded for the text now on disk, if any.

    Graph-only pipeline_version, so the character run's chunk results stay intact
    and a text edit invalidates both plans independently.
    """
    return await store.get_reusable_analysis_run(
        source_sha256=source_sha256(novel_text),
        schema_version=GRAPH_SCHEMA_VERSION,
        pipeline_version=GRAPH_ANALYSIS_VERSION,
        spine_template=spine_template,
    )


async def build_graph_structured(
    store: Any,
    *,
    on_progress: Optional[Callable[[float, MessageLike], None]] = None,
    on_log: Optional[Callable[[MessageLike], None]] = None,
    max_nodes: int = GRAPH_MAX_NODES,
) -> dict[str, Any]:
    """Extract the story graph from the imported text and publish the preview.

    Returns the snapshot summary.  An existing preview is left untouched when
    there is nothing to draw, so a failed or empty pass cannot blank a graph a
    project already had.
    """
    from novelvideo.graph_preview import (
        acquire_graph_preview_lock_async,
        empty_graph_preview,
        release_graph_preview_lock,
        write_graph_preview,
    )
    from novelvideo.structured_builders import spine_template_for

    def report(progress: Optional[float], task: MessageLike) -> None:
        if on_progress:
            on_progress(progress, task)

    def log(message: MessageLike) -> None:
        if on_log:
            on_log(message)

    novel_text = require_imported_novel(store.project_dir)
    template = spine_template_for(store)

    report(0.05, lmsg("tasks.progress.graph.chunking", "切分原文..."))
    chunks = chunk_source_text(novel_text, template)
    if not chunks:
        log(lmsg("tasks.log.graph.emptyChunks", "⚠️ 原文切分结果为空，未生成图谱"))
        report(1.0, lmsg("tasks.progress.graph.nothingToAnalyze", "无可分析内容"))
        return {**empty_graph_preview(), "published": False}

    log(
        lmsg(
            "tasks.log.graph.chunked",
            f"确定性切分: {len(chunks)} 个片段（{chunks[0].section_type}）",
            chunkCount=len(chunks),
            sectionType=chunks[0].section_type,
        )
    )

    run = await _graph_run(store, novel_text, template)
    run_id = str(run["run_id"]) if run else ""
    if not run_id:
        run_id = hashlib.sha1(
            f"{source_sha256(novel_text)}|{template}".encode("utf-8")
        ).hexdigest()[:32]
        await store.start_analysis_run(
            run_id=run_id,
            pipeline_version=GRAPH_ANALYSIS_VERSION,
            schema_version=GRAPH_SCHEMA_VERSION,
            spine_template=template,
            source_sha256=source_sha256(novel_text),
            source_length=len(novel_text),
            chunks=chunks,
        )

    cached: list[tuple[SourceChunk, ChunkGraphOutput]] = []
    pending = chunks
    done = {
        row["chunk_id"]: row
        for row in await store.list_analysis_chunks(run_id, status="done")
    }
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    for chunk_id, row in done.items():
        chunk = by_id.get(chunk_id)
        # The span has to be byte-identical, or the text moved and the cached
        # result describes a chunk that no longer exists.
        if chunk is None or row["source_hash"] != chunk.source_hash:
            continue
        try:
            cached.append((chunk, ChunkGraphOutput.model_validate_json(row["result_json"])))
        except Exception:  # noqa: BLE001 - a corrupt row is simply not a cache hit
            continue
    replayed = {chunk.chunk_id for chunk, _ in cached}
    pending = [chunk for chunk in chunks if chunk.chunk_id not in replayed]
    if cached:
        log(
            lmsg(
                "tasks.log.graph.reusingChunks",
                f"复用 {len(cached)} 个已完成片段，仅重算 {len(pending)} 个",
                cachedCount=len(cached),
                pendingCount=len(pending),
            )
        )

    async def persist_done(chunk: SourceChunk, output: ChunkGraphOutput) -> None:
        await store.mark_analysis_chunk_done(run_id, chunk.chunk_id, output.model_dump_json())

    async def persist_failed(chunk: SourceChunk, exc: BaseException) -> None:
        await store.mark_analysis_chunk_failed(run_id, chunk.chunk_id, str(exc))

    report(0.2, lmsg("tasks.progress.graph.extracting", "逐片段抽取人物与关系..."))
    entities, relations, failures = await extract_graph_from_chunks(
        pending,
        on_log=log,
        cached_outcomes=cached,
        on_chunk_done=persist_done,
        on_chunk_failed=persist_failed,
    )

    status = ("partial", f"{len(failures)} chunks failed") if failures else ("completed", "")
    if not entities:
        await store.finish_analysis_run(run_id, status=status[0], error=status[1])
        log(
            lmsg(
                "tasks.log.graph.noEvidence",
                "⚠️ 未抽取到有原文依据的实体，保留现有图谱预览",
            )
        )
        report(1.0, lmsg("tasks.progress.graph.noResults", "图谱抽取无结果"))
        return {**empty_graph_preview(), "published": False}

    snapshot = build_graph_snapshot(entities, relations, max_nodes=max_nodes)
    snapshot["run_id"] = run_id
    snapshot["pipeline"] = "structured_v1"

    if not failures:
        # Only a complete sweep may be stored as this run's result. A partial one
        # replayed later would keep the graph permanently missing exactly the
        # chunks that failed.
        await store.save_analysis_artifact(
            run_id,
            GRAPH_ARTIFACT_TYPE,
            _encode_snapshot(snapshot),
        )
    await store.finish_analysis_run(run_id, status=status[0], error=status[1])

    report(0.9, lmsg("tasks.progress.graph.publishing", "写入图谱预览..."))
    handle = await acquire_graph_preview_lock_async(store.state_dir)
    try:
        write_graph_preview(store.state_dir, snapshot)
    finally:
        release_graph_preview_lock(handle)

    log(
        lmsg(
            "tasks.log.graph.published",
            f"图谱已生成: {len(snapshot['nodes'])} 个节点 / {len(snapshot['edges'])} 条关系",
            nodeCount=len(snapshot["nodes"]),
            edgeCount=len(snapshot["edges"]),
        )
    )
    report(1.0, lmsg("tasks.progress.graph.done", "知识图谱生成完成"))
    return {**snapshot, "published": True}


def _encode_snapshot(snapshot: dict[str, Any]) -> str:
    import json

    return json.dumps(snapshot, ensure_ascii=False)


def decode_snapshot(payload: str) -> dict[str, Any] | None:
    """A stored graph artifact, or ``None`` when it is absent or unreadable."""
    import json

    if not payload:
        return None
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None
    return data

