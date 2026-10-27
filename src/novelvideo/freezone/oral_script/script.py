# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""模型返回的分节 Markdown → 结构化成稿（纯本地）。

章节契约写在 ``prompts.build_oral_script_prompt`` 的交付格式里；这里按同一份
``SECTION_TITLES`` 切分。切分做得比一般 Markdown 解析宽松：模型偶尔会漏掉 ``##``、
给标题加冒号或多写一层代码围栏，只要章节名在行首出现就认。真正缺整节才判结构不合
格（runner 据此触发一轮定向重写），而不是把用户的稿子丢掉。

「净台词」是给提词器和配音用的：镜头与动作提示单独成行、用括号包起来（这是 prompt
约定的写法），过滤这些行并剥掉行内括号片段，剩下的就是嘴上要念的话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 交付章节（顺序即 prompts 里要求的顺序，也是面板展示顺序）。
SECTION_TITLES: tuple[str, ...] = (
    "内核",
    "开头",
    "口播逐字稿",
    "为什么这么排",
    "标题候选",
    "拍之前",
)

#: 缺了这一节就没有可用成稿，判结构不合格。
REQUIRED_SECTIONS: frozenset[str] = frozenset({"口播逐字稿"})

#: 章节的稳定 ASCII 标识（与 ``SECTION_TITLES`` 同序）。产物里带上它，前端按 id 取
#: 自己的 i18n 标签——把中文章节日名写进界面代码就过不了仓库的硬编码中文关卡。
SECTION_IDS: tuple[str, ...] = (
    "core",
    "opening",
    "script",
    "why",
    "titles",
    "shoot",
)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*$")
#: 行首的 markdown 列表符 / 加粗符，取正文前先剥掉。
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)?")
_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*$")
#: 括号包裹的镜头与动作提示：全角、半角、方头括号都算。方括号 `[]` 不当镜头提示—
#: —它通常是需要用户自己填的占位，必须原样留在成稿里提醒到人了。
_STAGE_LINE_RE = re.compile(r"^[（(【](?P<note>.+)[）)】]\s*$", re.S)
_STAGE_INLINE_RE = re.compile(r"[（(【][^\n）)】]{1,80}[）)】]")
_RECOMMEND_WORD_RE = re.compile(r"^(?:推荐|首选|建议|这条)(?:[\u2014\-\s]|$)")
_BRACKET_ANY_RE = re.compile(r"[（(]([^）)\n]{0,60})[）)]")
#: 行内加粗：净台词要进提词器 / 配音，星号会被当成正文念出来。
_INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

_SCRIPT_SECTION = "口播逐字稿"
_WHY_SECTION = "为什么这么排"


@dataclass
class OralScriptParsed:
    """一次成稿的结构化结果。"""

    sections: dict[str, str] = field(default_factory=dict)
    script: str = ""
    spoken_text: str = ""
    lines: list[str] = field(default_factory=list)
    stage_notes: list[str] = field(default_factory=list)
    titles: list[dict[str, object]] = field(default_factory=list)
    recommended_title: str = ""
    first_line: str = ""
    missing_sections: list[str] = field(default_factory=list)

    @property
    def structure_ok(self) -> bool:
        """必需章节齐全（缺成稿整节时交给 runner 判不合格）。"""
        return not self.missing_sections

    def to_payload(self) -> dict[str, object]:
        return {
            "sections": self.sections,
            "script": self.script,
            "spoken_text": self.spoken_text,
            "lines": self.lines,
            "stage_notes": self.stage_notes,
            "titles": self.titles,
            "recommended_title": self.recommended_title,
            "first_line": self.first_line,
            "missing_sections": self.missing_sections,
        }


def section_records(sections: dict[str, str]) -> list[dict[str, str]]:
    """按契约顺序导出章节记录（稳定 id + 原始节名 + 正文）。

    顺序固定为 ``SECTION_TITLES`` 的顺序，空节也保留：交付少了一节能让用户看出来，
    静默省略反而像工具自己吞了内容。
    """
    return [
        {
            "id": section_id,
            "title": title,
            "body": str(sections.get(title) or "").strip(),
        }
        for section_id, title in zip(SECTION_IDS, SECTION_TITLES)
    ]


def strip_code_fence(text: str) -> str:
    """剥掉模型误加的整篇 ``` 围栏与首尾空白。"""
    lines = str(text or "").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _FENCE_RE.match(lines[0]):
        lines.pop(0)
        if lines and _FENCE_RE.match(lines[-1]):
            lines.pop()
    return "\n".join(lines).strip()


def strip_stage_marks(text: str) -> str:
    """去掉镜头/动作提示，只留嘴上要念的话。"""
    kept: list[str] = []
    for raw in str(text or "").splitlines():
        line = _BULLET_RE.sub("", raw).strip()
        if not line:
            kept.append("")
            continue
        if _STAGE_LINE_RE.match(line):
            continue
        line = _STAGE_INLINE_RE.sub("", line).strip()
        kept.append(_INLINE_BOLD_RE.sub(r"\1", line).strip())
    out = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def delivered_text(parsed: OralScriptParsed) -> str:
    """去 AI 味检查要覆盖的交付全文：正文 + 写给用户的理由。

    方法论点明假对比最爱藏在金句收尾和「为什么这么排」这类理由段里，只看正文会
    漏；门禁（要不要补一轮重写）与交付给用户的体检报告都取这一份文本，否则会出现
    「报告说有硬伤、却一次都没重写」这种自相矛盾的产物。
    """
    why = str(parsed.sections.get(_WHY_SECTION) or "")
    return f"{parsed.script}\n{why}".strip()


def _section_key(header: str) -> str | None:
    """把一行标题认成章节名（容忍 ``## 口播逐字稿：xxx``、加粗、多余空白）。"""
    cleaned = _BOLD_RE.sub(r"\1", header.strip()).lstrip("#").strip()
    cleaned = cleaned.replace("：", ":")
    name = cleaned.split(":", 1)[0].strip()
    for title in SECTION_TITLES:
        if name == title or name.startswith(title):
            return title
    return None


def _is_header(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    # 没写 ## 但整行就是一个章节名（加粗或裸写），也当分节边界。
    if _section_key(_BULLET_RE.sub("", stripped)) and len(stripped) <= 24:
        return _BOLD_RE.match(stripped) is not None
    return False


def _parse_titles(body: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for raw in body.splitlines():
        line = _BOLD_RE.sub(r"\1", _BULLET_RE.sub("", raw).strip()).strip()
        if not line or line.startswith("|") or set(line) <= {"-", ":", " "}:
            continue
        recommended = False
        notes: list[str] = []
        for match in _BRACKET_ANY_RE.finditer(line):
            content = match.group(1).strip()
            if _RECOMMEND_WORD_RE.match(content):
                recommended = True
                continue
            if content:
                notes.append(content)
        title = _BRACKET_ANY_RE.sub("", line).strip()
        # 括号外的半句理由一般用破折号接在标题后面，归到 note 里。
        for sep in ("——", "—", "－", " - "):
            head, found, tail = title.partition(sep)
            if found and tail.strip():
                notes.append(tail.strip())
                title = head
                break
        title = title.strip().rstrip("—－-· ").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "recommended": recommended,
                "note": "・".join(n for n in notes if n),
            }
        )
    return out


def parse_oral_script(text: str) -> OralScriptParsed:
    """按章节切分成稿，并派生净台词、逐行分镜、标题候选。"""
    body = strip_code_fence(text)
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        key = _section_key(line) if _is_header(line) else None
        if key:
            current = key
            sections.setdefault(key, [])
            continue
        if current is None:
            # 模型没给章节时，整篇先兜进「口播逐字稿」，用户的稿子不能被丢掉。
            current = _SCRIPT_SECTION
            sections.setdefault(current, [])
        sections[current].append(line)

    parsed_sections = {
        name: "\n".join(sections.get(name, [])).strip() for name in SECTION_TITLES
    }

    script = parsed_sections.get(_SCRIPT_SECTION, "")
    missing = [
        name for name in REQUIRED_SECTIONS if not parsed_sections.get(name)
    ]

    lines = [
        _BULLET_RE.sub("", raw).strip()
        for raw in script.splitlines()
        if raw.strip()
    ]
    stage_notes = [
        _STAGE_LINE_RE.match(line).group("note").strip()
        for line in lines
        if _STAGE_LINE_RE.match(line)
    ]
    spoken = strip_stage_marks(script)
    spoken_lines = [part for part in spoken.splitlines() if part.strip()]
    parsed = OralScriptParsed(
        sections=parsed_sections,
        script=script,
        spoken_text=spoken,
        lines=lines,
        stage_notes=stage_notes,
        titles=_parse_titles(parsed_sections.get("标题候选", "")),
        missing_sections=missing,
    )
    parsed.recommended_title = next(
        (
            str(item["title"])
            for item in parsed.titles
            if item.get("recommended")
        ),
        str(parsed.titles[0]["title"]) if parsed.titles else "",
    )
    parsed.first_line = next(
        (line for line in spoken_lines if len(line) >= 4),
        "",
    )
    return parsed
