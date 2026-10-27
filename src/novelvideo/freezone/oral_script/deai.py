# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""去 AI 味的本地机械预检（纯函数，不出网）。

蒸馏自「太上老君·AI炼丹炉」的 ``ai_tell_linter.py``（作者柳爷，识别思路参考
humanizer / Humanizer-zh，MIT；原仓库许可 CC BY-NC 4.0）。工具化后的差别：

- 原脚本给人看 Markdown 报告、以退出码表示结论；这里返回结构化命中，让 runner 能
  直接把命中句子回喂给模型做**一轮**定向重写。
- 假对比仍然是硬拦截：口播里最容易被观众一眼认出的 AI 味就是「先立一个稻草人再推倒
  它」，原方法论要求默认一句都不用。
- 软提醒不阻断任务，只随产物交付（空转词、念不顺的句式、破折号过多）。

局限与原脚本一致：没命中不等于没有 AI 味，最后还是要人念一遍。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 假对比的十种壳（含换了动词的同一种壳），命中即硬拦截。
HARD_RULES: dict[str, str] = {
    "不是X而是Y": r"不是[^。！？\n]{1,45}(?:，|,)?(?:而是|是)[^。！？\n]{1,45}",
    "并非X而是Y": r"并非[^。！？\n]{1,45}(?:，|,)?而是[^。！？\n]{1,45}",
    "不在于X而在于Y": r"不在于[^。！？\n]{1,45}(?:，|,)?而在于[^。！？\n]{1,45}",
    "你以为X其实Y": r"你以为[^。！？\n]{1,45}(?:，|,)?其实[^。！？\n]{1,45}",
    "不只是X更是Y": r"不(?:只|仅)是?[^。！？\n]{1,45}(?:，|,)?(?:更是|还(?:是|要)?)[^。！？\n]{1,45}",
    "只X不够还要Y": r"(?:只|光)[^。！？\n]{1,45}(?:不够|不行|没用)[^。！？\n]{0,12}(?:还要|还得|才)[^。！？\n]{1,45}",
    "只是X才是Y": r"只是[^。！？\n]{1,45}(?:，|,)?[^。！？\n]{0,12}才是[^。！？\n]{1,45}",
    "X不是问题，Y才是": r"不是[^。！？，,\n]{1,20}[，,][^。！？，,\n]{1,20}才是",
    "不叫X，叫Y": r"不(?:叫|算)[^。！？，,\n]{1,20}[，,][^。！？，,\n]{0,4}(?:叫|算)[^。！？\n]{1,30}",
    "不AX，A的是Y": r"不([\u4e00-\u9fa5])[^。！？，,\n]{0,20}[，,][^。！？，,\n]{0,6}\1(?:的是|才是)[^。！？\n]{1,30}",
}

#: 空转词与套话路标（软提醒）：删掉路标，看剩下的话有没有信息。
SOFT_TERMS: tuple[str, ...] = (
    "本质上", "真正的核心", "这背后反映的是", "归根结底", "值得注意的是",
    "随着AI的发展", "在这个AI时代", "赋能", "抓手", "认知跃迁",
    "助力", "全方位", "无缝", "深度融合", "生态闭环", "底层逻辑", "重塑", "颠覆性",
    "总而言之", "综上所述", "让我们一起", "未来可期", "希望对你有帮助", "如果你有任何问题",
    "值得一提的是", "不可否认", "毋庸置疑",
    "说了这么多", "收个总", "正确姿势", "行业公开的秘密", "干货来了",
    "兄弟们", "家人们", "你知道吗", "说实话",
)

#: 句式级软提醒（比词表更抓得准的几种 AI 节奏）。
SOFT_REGEX: dict[str, str] = {
    "「随着……发展」式开头": r"(?m)^\s*随着[^。，！？\n]{1,20}(?:的)?(?:发展|到来|普及|兴起)",
    "「进行＋动词」书面句": r"进行(?:了)?(?:一次|一番)?[一-龥]{2}",
    "层叠的「的」（一句里 4 个以上）": r"[^。！？\n]*?(?:的[^。！？，\n]{0,8}){4,}",
    "四字词排比": r"(?:[一-龥]{4}[、，,]){3,}[一-龥]{4}",
    "「最后送你/给你几个」式清单": r"(?:送你|给你|教你)[两三四五六七八九十]个",
    "自嗨式设问过渡": r"(?:为什么我|你们知道)[^。！？\n]{0,20}吗",
}

#: 全文破折号超过这个数就合并成一条「破折号过多」提醒（原经验值 5）。
DASH_LIMIT = 5


@dataclass(frozen=True)
class AiTellHit:
    """一处命中。``kind`` 为 ``hard``（假对比，默认要改）或 ``soft``（结合语境判断）。"""

    kind: str
    rule: str
    line: int
    text: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "rule": self.rule,
            "line": self.line,
            "text": self.text,
        }


@dataclass(frozen=True)
class AiTellReport:
    """一次预检的结论：``passed`` 只由硬拦截决定，软提醒不阻断交付。"""

    passed: bool
    hard: list[AiTellHit] = field(default_factory=list)
    soft: list[AiTellHit] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "hard_count": len(self.hard),
            "soft_count": len(self.soft),
            "hard": [hit.to_payload() for hit in self.hard],
            "soft": [hit.to_payload() for hit in self.soft],
        }


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def lint_script(text: str, *, allow: tuple[str, ...] | list[str] = ()) -> AiTellReport:
    """扫一遍稿子，返回结构化命中。

    ``allow`` 是允许清单片段：确实需要保留的对比句（纠正可核实的日期、数字、法律性
    质，或区分适用范围）写进来就不算硬拦截——沿用原脚本的口径，命中文字包含其中任一
    片段（或片段包含命中文字）时跳过。
    """
    body = str(text or "")
    allowed = [str(item).strip() for item in allow if str(item).strip()]

    hard: list[AiTellHit] = []
    for rule, pattern in HARD_RULES.items():
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip()
            if any(item in snippet or snippet in item for item in allowed):
                continue
            hard.append(
                AiTellHit(kind="hard", rule=rule, line=_line_number(body, match.start()), text=snippet)
            )

    soft: list[AiTellHit] = []
    for term in SOFT_TERMS:
        for match in re.finditer(re.escape(term), body):
            soft.append(
                AiTellHit(kind="soft", rule=term, line=_line_number(body, match.start()), text=term)
            )
    for rule, pattern in SOFT_REGEX.items():
        for match in re.finditer(pattern, body):
            soft.append(
                AiTellHit(
                    kind="soft",
                    rule=rule,
                    line=_line_number(body, match.start()),
                    text=match.group(0).strip(),
                )
            )
    dashes = [match.start() for match in re.finditer("——", body)]
    if len(dashes) > DASH_LIMIT:
        soft.append(
            AiTellHit(
                kind="soft",
                rule=f"破折号过多（全文 {len(dashes)} 处，超过 {DASH_LIMIT} 处）",
                line=_line_number(body, dashes[0]),
                text="——",
            )
        )

    hard.sort(key=lambda hit: (hit.line, hit.rule))
    soft.sort(key=lambda hit: (hit.line, hit.rule))
    return AiTellReport(passed=not hard, hard=hard, soft=soft)
