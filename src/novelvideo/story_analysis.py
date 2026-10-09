"""Deterministic source-text chunking for the structured_v1 pipeline.

Structured extraction never hands a whole novel or screenplay to one model
call.  It splits the imported text along boundaries the format already provides
— scene headings for screenplays, chapter markers for prose — and analyses each
piece independently so that work is bounded, parallelisable and resumable.

Every chunk carries the character offsets it came from.  That is what lets a
later extraction prove an entity is real: the evidence it reports has to appear
inside the span it claims, in the imported text, or the candidate is dropped.

Offsets are recovered without changing the shared parsers.  ``parse_scene_blocks``
runs on stripped, blank-filtered lines and cannot report positions in the
original text, and it is used by asset compilation, screenplay normalization and
import quality checks — so instead of altering it, scene headers are located by
scanning forward from the previous block's end.  Blocks come back in source
order, so a forward-only scan cannot match an earlier occurrence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Literal

SectionType = Literal["scene", "chapter", "window"]

# Prose without any chapter marker still has to be analysed. Windows are large
# enough to carry context across a conversation but small enough to stay well
# inside a model's useful attention span.
_WINDOW_CHARS = 6000
_WINDOW_OVERLAP = 200

# Short neighbouring sections are packed up to this size. A call costs the
# same few seconds whether it carries 300 characters or 3000, so packing is
# what keeps a screenplay of many small scenes from being all latency.
_TARGET_CHUNK_CHARS = 3000


@dataclass(frozen=True)
class SourceChunk:
    """One analysable span of the imported text."""

    chunk_id: str
    chunk_index: int
    section_type: SectionType
    section_label: str
    source_start: int
    source_end: int
    text: str
    characters: list[str] = field(default_factory=list)

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def source_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def chunk_source_text(text: str, spine_template: str | None) -> list[SourceChunk]:
    """Split imported text along the boundaries its format already provides.

    Screenplays are cut at scene headings, prose at chapter markers.  Either may
    fall through to fixed windows when the expected markers are absent, so that
    a malformed import still produces analysable chunks instead of nothing.
    """
    if not (text or "").strip():
        return []

    if str(spine_template or "").strip() == "drama":
        chunks = _chunk_by_scene(text)
    else:
        chunks = _chunk_by_chapter(text)

    if not chunks:
        return _chunk_by_window(text)

    # A scene or chapter boundary is the right place to cut, but it says nothing
    # about size: a single chapter can run tens of thousands of characters, while
    # a screenplay scene is often a few hundred. Split what is too big, then pack
    # what is too small, and renumber so chunk_index still matches position.
    bounded: list[SourceChunk] = []
    for chunk in chunks:
        bounded.extend(_split_oversized(chunk))
    packed = _pack_small(bounded)
    return [
        replace(chunk, chunk_index=index) for index, chunk in enumerate(packed)
    ]


def _pack_small(chunks: list[SourceChunk]) -> list[SourceChunk]:
    """Group consecutive short sections into one analysable chunk.

    A model call costs several seconds of round trip no matter how little text
    it carries, so a screenplay of many short scenes spends nearly all its time
    waiting rather than reading. Sections are contiguous by construction, so
    joining neighbours keeps offsets exact and evidence still resolves against
    the source.

    Only neighbours of the same kind are joined, and packing stops at the target
    size, so a chunk never grows past what one call can usefully consider.
    """
    packed: list[SourceChunk] = []
    group: list[SourceChunk] = []

    def flush() -> None:
        if not group:
            return
        if len(group) == 1:
            packed.append(group[0])
        else:
            first, last = group[0], group[-1]
            label = first.section_label
            if len(group) > 1:
                label = f"{first.section_label} 等 {len(group)} 段"
            characters: list[str] = []
            for member in group:
                for name in member.characters:
                    if name not in characters:
                        characters.append(name)
            packed.append(
                SourceChunk(
                    chunk_id=f"{first.chunk_id}+{len(group)}",
                    chunk_index=first.chunk_index,
                    section_type=first.section_type,
                    section_label=label,
                    source_start=first.source_start,
                    source_end=last.source_end,
                    # Rebuilt from the members rather than re-sliced, so this
                    # stays correct even if neighbours are ever non-contiguous.
                    text="".join(member.text for member in group),
                    characters=characters,
                )
            )
        group.clear()

    for chunk in chunks:
        if group:
            same_kind = chunk.section_type == group[0].section_type
            contiguous = chunk.source_start == group[-1].source_end
            width = (chunk.source_end - group[0].source_start)
            if not (same_kind and contiguous) or width > _TARGET_CHUNK_CHARS:
                flush()
        group.append(chunk)
    flush()
    return packed


def _split_oversized(chunk: SourceChunk) -> list[SourceChunk]:
    """Break one section into overlapping parts if it exceeds the window size.

    Parts keep the parent's label and derive their ids from it, so evidence
    recorded against a part still points at a recognisable section.
    """
    if len(chunk.text) <= _WINDOW_CHARS:
        return [chunk]

    parts: list[SourceChunk] = []
    offset = 0
    part_index = 0
    length = len(chunk.text)
    while offset < length:
        end = min(offset + _WINDOW_CHARS, length)
        parts.append(
            SourceChunk(
                chunk_id=f"{chunk.chunk_id}-p{part_index:03d}",
                chunk_index=chunk.chunk_index,
                section_type=chunk.section_type,
                section_label=f"{chunk.section_label}({part_index + 1})",
                source_start=chunk.source_start + offset,
                source_end=chunk.source_start + end,
                text=chunk.text[offset:end],
                characters=list(chunk.characters),
            )
        )
        if end >= length:
            break
        offset = end - _WINDOW_OVERLAP
        part_index += 1
    return parts


def _chunk_by_scene(text: str) -> list[SourceChunk]:
    from novelvideo.utils.screenplay_scene_parser import parse_scene_blocks

    blocks = parse_scene_blocks(text)
    if not blocks:
        return []

    starts: list[int] = []
    cursor = 0
    for block in blocks:
        anchor = (block.header_line or "").strip()
        if not anchor:
            anchor = next((line for line in block.lines if line.strip()), "").strip()

        found = text.find(anchor, cursor) if anchor else -1
        if found < 0:
            # An anchor that cannot be located (the parser rewrote it, or the
            # same heading was consumed earlier) must not silently swallow the
            # preceding block: start where the previous one ended instead.
            found = cursor
        starts.append(found)
        cursor = found + max(len(anchor), 1)

    chunks: list[SourceChunk] = []
    # Whatever precedes the first scene heading — a synopsis, a cast list, the
    # character bios — is often the densest description in the whole script, so
    # it must not fall outside every chunk.
    if starts and starts[0] > 0:
        chunks.append(
            SourceChunk(
                chunk_id="scene-preamble",
                chunk_index=0,
                section_type="scene",
                section_label="片头",
                source_start=0,
                source_end=starts[0],
                text=text[: starts[0]],
            )
        )

    for index, block in enumerate(blocks):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if end <= start:
            continue
        label = block.header_line.strip() or block.location.strip() or f"场 {index + 1}"
        chunks.append(
            SourceChunk(
                chunk_id=f"scene-{index:04d}",
                chunk_index=index,
                section_type="scene",
                section_label=label,
                source_start=start,
                source_end=end,
                text=text[start:end],
                characters=list(block.characters),
            )
        )
    return chunks


def _chunk_by_chapter(text: str) -> list[SourceChunk]:
    from novelvideo.cognee.chapter_detector import ChapterDetector

    # A marker-less novel comes back as one synthesized chapter covering the
    # whole text. Accepting it would defeat the point of chunking, and its
    # content is rewritten by the detector's fallback preparation, so the
    # offsets would not match the source either. Let the window path take over.
    chapters = [
        chapter
        for chapter in ChapterDetector().detect(text)
        if not getattr(chapter, "is_fallback", False)
    ]
    if not chapters:
        return []

    # ChapterDetector splits on "\n" without dropping blank lines, so line
    # indices map back to character offsets exactly.
    lines = text.split("\n")
    line_offsets: list[int] = []
    running = 0
    for line in lines:
        line_offsets.append(running)
        running += len(line) + 1
    line_offsets.append(len(text))

    def offset_of(line_index: int) -> int:
        clamped = max(0, min(int(line_index), len(line_offsets) - 1))
        return line_offsets[clamped]

    chunks: list[SourceChunk] = []
    # Same reason as the screenplay path: a synopsis or cast list sitting before
    # the first chapter marker is the densest description the source has, and it
    # would otherwise fall outside every chunk.
    first_start = offset_of(chapters[0].start_line)
    if first_start > 0:
        chunks.append(
            SourceChunk(
                chunk_id="chapter-preamble",
                chunk_index=0,
                section_type="chapter",
                section_label="卷首",
                source_start=0,
                source_end=first_start,
                text=text[:first_start],
            )
        )

    for index, chapter in enumerate(chapters):
        start = offset_of(chapter.start_line)
        end = min(offset_of(chapter.end_line), len(text))
        if end <= start:
            continue
        chunks.append(
            SourceChunk(
                chunk_id=f"chapter-{index:04d}",
                chunk_index=index,
                section_type="chapter",
                section_label=f"第{chapter.number}章",
                source_start=start,
                source_end=end,
                text=text[start:end],
            )
        )
    return chunks


def _chunk_by_window(text: str) -> list[SourceChunk]:
    """Fall back to overlapping fixed windows when no markers were found.

    The overlap keeps an entity introduced right at a boundary from being cut in
    half and missed by both neighbours.
    """
    chunks: list[SourceChunk] = []
    start = 0
    index = 0
    length = len(text)
    while start < length:
        end = min(start + _WINDOW_CHARS, length)
        chunks.append(
            SourceChunk(
                chunk_id=f"window-{index:04d}",
                chunk_index=index,
                section_type="window",
                section_label=f"片段 {index + 1}",
                source_start=start,
                source_end=end,
                text=text[start:end],
            )
        )
        if end >= length:
            break
        start = end - _WINDOW_OVERLAP
        index += 1
    return chunks
