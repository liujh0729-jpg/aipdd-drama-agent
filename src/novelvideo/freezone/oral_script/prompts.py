# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""口播稿写作指令的组装（纯本地，不出网）。

把「炉心七条底层判断 + 开炉的开头结构 + 改稿六刀 + 去 AI 味」压成一份模型每次都能
照做的单一指令。原方法论散在七个技能文件里、靠对话逐步调用；工具化之后没有多轮
对话，所以这些判断必须**一次性写进 prompt**，并要求模型在交付里自证（写明第一句靠
哪种吸引力、下一个钩子由什么兑现）。

章节名是 runner 与解析层的契约（``script.SECTION_TITLES``），改动要同步。
"""

from __future__ import annotations

from typing import Any

# 中文口播的实测语速口径：约每秒 4.8 字（比朗读慢一档，留出换气与画面切换）。
# 六刀里的秒数阈值是抖音经验值，这里只做「字数 → 时长」的换算，不作承诺。
CHARS_PER_SECOND = 4.8

#: 时长档 → 正文字数上下限的换算表（面板下拉与后端归一化共用这一份集合）。
DURATION_BANDS: tuple[int, ...] = (15, 30, 45, 60, 90, 120)
DEFAULT_DURATION_S = 60
MIN_DURATION_S = 10
MAX_DURATION_S = 180

DEFAULT_TITLE_COUNT = 3
MIN_TITLE_COUNT = 1
MAX_TITLE_COUNT = 5

#: 结构套路（蒸馏自「开炉」：依据素材选结构，并用一句话说明为什么选它）。
FORMULAS: dict[str, dict[str, str]] = {
    "auto": {
        "name_cn": "由内容定",
        "spine": "不预设套路：先看这条素材天然是一件事还是一个判断，按最省力的顺序推进。",
    },
    "judgment": {
        "name_cn": "热点判断",
        "spine": "这件事是什么 → 为什么值得你现在听 → 我的判断 → 以后会怎样。",
    },
    "qa": {
        "name_cn": "专业答疑",
        "spine": "观众真实卡住的那个问题 → 解释或现场演示 → 一句能带走的结论。",
    },
    "on_site": {
        "name_cn": "现场突发",
        "spine": "此刻的现场（着急、反常）→ 马上把实物或画面举到镜头前兑现 → 往下推进 → 收。",
    },
    "contrarian": {
        "name_cn": "反着说",
        "spine": "全都在夸的东西，说出它被忽略的那笔代价 → 这笔代价具体长什么样 → 怎么办。",
    },
    "event": {
        "name_cn": "事件衬托",
        "spine": "讲一件具体发生过的事 → 事里露出的判断 → 让观众自己得出结论，不替他宣布。",
    },
}
DEFAULT_FORMULA = "auto"

#: 交付里禁止出现的句式与词（去 AI 味规则库的第一优先项与口播前十种）。
BANNED_SHAPES: tuple[str, ...] = (
    "假对比：不是X而是Y / 并非X而是Y / 不在于X而在于Y / 你以为X其实Y / 不只是X更是Y / "
    "只X不够还要Y / X只是表面Y才是关键 / 这不叫努力这叫瞎练（换了动词的同一种壳也算）",
    "伪深度路标：本质上、归根结底、真正的核心是、这背后反映的是、值得注意的是",
    "空开场：随着……的发展、在这个快节奏的时代、今天想跟大家聊聊",
    "旁白腔与伪互动：聊到这儿肯定有人问、接下来我分三点讲、让我们一起来看看",
    "硬凑三点与整齐排比：首先其次最后、三句一样长的口号、明明两点够用要凑成三点",
    "结尾拔高：总之这是一个充满希望的未来、愿我们都能……",
    "大词与商务腔：赋能、抓手、全方位、底层逻辑、闭环、重塑、颠覆性",
    "模糊权威：研究表明、专家指出、很多人都说（说不出是谁、哪份材料就删）",
    "假口语：硬塞兄弟们、说实话、你知道吗、家人们（口语感来自说话的顺序和对象，不来自插入语气词）",
    "念不顺的书面句：一句里四个以上的「的」、四字词一串、「进行了一次优化」",
)

_COMMON_RULES: tuple[str, ...] = (
    "陌生人只给你一两秒。开头的第一任务是让刷到的人愿意多停几秒，讲清楚和建立信任都排在后面。",
    "第一句的主语是观众：他正在遇到什么、能得到什么。讲创作者自己的开头（我做了一年、我最近发现、"
    "今天想跟大家聊聊）对陌生人是废话——你的经历往后放，当证据用。",
    "先替观众把心里没说出口的话想出来：他没意识到的痛点、对你的防御（又要卖课了吧、肯定有坑、"
    "收钱了吧）、他一厢情愿的想象。防御在被形成之前说出来或绕开，比事后解释便宜得多。只用真实普遍"
    "存在的心理，不虚构一种普遍焦虑来吓人。",
    "想让人相信的东西不要直接说，找一件具体的事把它衬托出来，让观众自己得出结论。案例要短、要具体。",
    "开头答应观众的，下一句、下一个画面马上兑现（真实动作、实物、演示、截图、数据）；接住它的最好"
    "又是下一个钩子。拿不出兑现的东西就换开头。",
    "承诺要比手里的证据小一点。观众对「用了就爆款」「躺着赚钱」早有抵抗力，说得太夸张反而没人信。",
    "判断力胜过教程：少写步骤，多写「这件事值不值得、为什么、会走向哪里」。展示你查了哪些别人想不"
    "到的维度，思考过程本身就是能力证明。",
    "术语换成目标人群手机里真的会出现、会去搜的具体名字；抽象品类词（大模型、某软件）观众没感觉。",
    "演示和举例选目标观众自己身上最常见的那个处境，让人一看就觉得说的就是我；不要选只有少数人才有的处境。",
    "时长服从信息：十几秒能说清的不要拉长，有必要交代的過程也不硬缩。",
    "结尾断头台：最有力的那句说完，只跟一句 15—35 字（3 秒内念完）的收束就停，不写求关注、求评论的流程。",
    "前后态度要一致：前面说了「不是来推销的」，结尾就不能直接拉客；前面说「别急着下结论」，结尾就不"
    "能下绝对的结论。不一致时改结尾去配合开头建立的信任。",
)

#: 事实底线（原方法论的「不编」与「不知道这个人是谁就不写第一人称经历」）。
_FACT_RULES: tuple[str, ...] = (
    "只用使用者给过的事实。经历、数字、测试结果、合作背书、观众反应都不能新增；没测过的不能写成"
    "「我测过了」。",
    "使用者没给出身份和经历时，稿子里不能出现「我做了二十年」「上个月一个客户来找我」这类句子，也"
    "不能编一个具体案例来撑开头。这时改用观众的处境开头，或者在需要真事的地方留一处明显的空，写明"
    "「这里换成你自己碰到的一件事」。",
    "行业里的价格、比例、损耗这类数字，写成「一般」「大概」，并在交付提醒里让用户核实。",
    "只在一两处真不知道、又会影响效果的地方留空；不要整篇都是方括号让用户填。缺的恰好是这条稿的"
    "核心时，先给一版不靠这个缺失信息、今天就能念的稿，再说清补上之后能强在哪。",
    "参考别人的爆款时借观点不借脉络：第一句说什么、第二句说什么、用什么例子都和人家差不多就是照搬。"
    "别人的原话、身份、成果不能变成使用者的。",
    "健康、法律、理财类内容不能丢掉危险信号和必要限定；说得太绝的专业结论要收到准确的程度。",
    "不承诺播放、涨粉和收入。",
)


def clamp_duration(value: Any) -> int:
    """时长档归一化：非正数回默认档，其余夹到 10—180 秒。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DURATION_S
    if seconds <= 0:
        return DEFAULT_DURATION_S
    return max(MIN_DURATION_S, min(MAX_DURATION_S, seconds))


def clamp_title_count(value: Any) -> int:
    """标题候选数量归一化（0/非法回默认 3）。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TITLE_COUNT
    if count <= 0:
        return DEFAULT_TITLE_COUNT
    return max(MIN_TITLE_COUNT, min(MAX_TITLE_COUNT, count))


def normalize_formula(value: Any) -> str:
    """结构套路归一化：未知值回「由内容定」，不报错。"""
    key = str(value or "").strip().lower()
    return key if key in FORMULAS else DEFAULT_FORMULA


def resolve_word_budget(duration_s: Any) -> tuple[int, int]:
    """时长档换算成正文字数上下限（±15%），给 prompt 一个可核对的目标。"""
    seconds = clamp_duration(duration_s)
    center = round(seconds * CHARS_PER_SECOND)
    return max(20, round(center * 0.85)), round(center * 1.15)


def estimate_speech_seconds(text: str) -> int:
    """按中文口播语速估算净台词时长（秒）：字数 ÷ 4.8，向上取整。

    只数汉字与数字字母，镜头提示（括号内的动作说明）不计入口播时长。
    """
    from .script import strip_stage_marks

    spoken = strip_stage_marks(text)
    chars = sum(1 for ch in spoken if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())
    if chars <= 0:
        return 0
    return max(1, round(chars / CHARS_PER_SECOND))


def _joined(lines: list[str], items: list[str] | tuple[str, ...]) -> list[str]:
    return lines + [f"- {item}" for item in items]


def build_oral_script_prompt(
    *,
    topic: str = "",
    persona: str = "",
    material: str = "",
    audience: str = "",
    scene: str = "",
    tone: str = "",
    formula: object = None,
    duration_s: object = DEFAULT_DURATION_S,
    title_count: object = DEFAULT_TITLE_COUNT,
) -> str:
    """组装口播稿写作指令：使用者处境 + 底层判断 + 禁句 + 输出契约。"""
    topic_clean = str(topic or "").strip()
    persona_clean = str(persona or "").strip()
    material_clean = _clamp_material(material)
    audience_clean = str(audience or "").strip()
    scene_clean = str(scene or "").strip()
    tone_clean = str(tone or "").strip()
    formula_id = normalize_formula(formula)
    resolved = FORMULAS[formula_id]
    seconds = clamp_duration(duration_s)
    words_min, words_max = resolve_word_budget(seconds)
    titles = clamp_title_count(title_count)

    lines: list[str] = [
        "你是帮人写短视频口播稿的老手，写出来的稿子要能被本人对着镜头直接念完。"
        "先下判断再动笔：拿到材料就给出你的选择和理由，不把问题抛回给使用者。",
        "",
        "【这次的任务】",
        f"- 要拍的事：{topic_clean or '（使用者没写主题，从素材里挑一件今天最能拍的）'}",
        f"- 这个人是谁：{persona_clean or '（未提供，见下面的事实底线）'}",
        f"- 想吸引谁：{audience_clean or '（未提供：你按这条内容判断最该来的人，并在交付里写明）'}",
        f"- 拍摄条件：{scene_clean or '（未提供：按一个人、手机随手拍、不额外布置的最低条件设计）'}",
        f"- 说话的样子：{tone_clean or '（未提供：像跟朋友面对面说话，不播音腔）'}",
        f"- 结构：{resolved['name_cn']}。{resolved['spine']}",
        f"- 成片时长约 {seconds} 秒，正文 {words_min}-{words_max} 字（按每秒 {CHARS_PER_SECOND} 字念）。",
        "",
        "【怎么想：每条建议之前先过这几条】",
    ]
    lines = _joined(lines, list(_COMMON_RULES))

    lines += ["", "【事实底线，违反就等于交废稿】"]
    lines = _joined(lines, list(_FACT_RULES))
    if not persona_clean:
        lines += [
            "- 本次没有提供「这个人是谁」，所以整篇不许出现任何第一人称经历、身份和年限；"
            "开头用观众的处境，需要真事的地方留一处空并写明换成使用者自己碰到的一件事。",
        ]

    lines += ["", "【一句都不许出现】"]
    lines = _joined(lines, list(BANNED_SHAPES))
    lines += [
        "",
        "【自检，写完逐条对照】",
        "- 第一句：把创作者自己当主语吗？有没有让陌生人停下来的东西？下一句拿什么兑现？",
        "- 假对比：全篇一句「不是X而是Y」这类壳都没有（会被本地检查硬拦）。",
        "- 每一句都用上了这个人的处境、条件或素材吗？原样给另一个人还成立的，就是泛货，重写。",
        "- 念一遍：这话他跟朋友面对面会这么说吗？不会就是 AI 味。",
        "- 结尾是不是金句落地就停，没走求关注流程？",
        "",
        "【素材】（只能在这份资料范围内取事实，可以借观点和推进逻辑，不许照搬脉络）",
        material_clean or "（未提供参考素材）",
        "",
        "【交付格式：严格按下面六个二级标题输出，标题文字一字不改，不要额外前言】",
        "## 内核",
        "两句话：这条看完观众应该明白什么、它靠哪种吸引力留人（娱乐 / 帮助 / 实力展示 / 下危机）。",
        "## 开头",
        "给 2 个第一句候选，各自写完紧跟着用什么兑现，以及为什么这句适合这次的观众；标注推荐哪个。",
        "## 口播逐字稿",
        "可以直接念的全文。镜头和动作提示单独成行、用全角括号包起来（例：（把刚捡的蛋递到镜头前））；"
        "括号里的内容不进嘴，其余每一句都是要念出来的话，句句有承接。",
        "## 为什么这么排",
        "最多 5 条，说清关键处的取舍和理由；这一节同样不许用假对比句式。",
        "## 标题候选",
        f"{titles} 个，一行一个；最推荐的那个后面加「（推荐）」，可用括号补半句理由。",
        "## 拍之前",
        "1-3 条：必须实拍才能成立的画面、需要核实或补齐的事实、条件变了要换哪句。",
    ]
    return "\n".join(lines)


def build_rewrite_prompt(
    previous: str,
    hits: list[dict[str, Any]],
    *,
    reason: str = "ai_tell",
) -> str:
    """一轮定向重写的指令：只改硬伤，事实数字观点一律冻住。

    对应原方法论的「最多一轮有依据的修改」——不做无限重写，也不借机换选题。
    """
    clean_prev = str(previous or "").strip()
    lines = [
        "上一版口播稿过不了本地检查。现在只做一轮定向修改，改完交全文，不要解释、不要前言。",
        "",
        "【必须改掉的问题】",
    ]
    if reason == "structure":
        lines.append("- 章节不完整或缺「口播逐字稿」整节，按原输出契约重新交齐六个二级标题。")
    for hit in hits[:20]:
        quoted = str(hit.get("text") or "").strip()
        kind = str(hit.get("kind") or "")
        rule = str(hit.get("rule") or "")
        if not quoted:
            continue
        lines.append(f"- [{kind}] {rule}：{quoted}")
    lines += [
        "",
        "【改法】",
        "- 假对比这样改：删掉被否定、被拿来垫高的那一半，找出后半句真正要说的意思，补上谁、在哪、"
        "做了什么、凭什么、结果是什么，然后直接说这个意思，不宣布「答案」「真相」「核心」。",
        "- 空转词直接删，看剩下有没有信息；没有就补动作、数字、例子或后果，不换同义词。",
        "- 事实、数字、观点、引用来源改前改后不能变；镜头提示保持单独成行、用全角括号。",
        "- 确实必须保留的对比（纠正可核实的日期、数字、法律性质，或区分适用范围）才留，其余一律改。",
        "- 不改选题、不换结构、不扩写，除问题句外尽量保留原话。",
        "",
        "【上一版全文】",
        clean_prev,
    ]
    return "\n".join(lines)


_MAX_MATERIAL_CHARS = 12000


def _clamp_material(material: str) -> str:
    text = str(material or "").strip()
    if len(text) <= _MAX_MATERIAL_CHARS:
        return text
    return text[:_MAX_MATERIAL_CHARS]
