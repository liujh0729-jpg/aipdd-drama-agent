"""[aipdd fork delta] 静态守卫：报错文案与文档里的导航路径必须指得进真实菜单。

「测试问题发现」第 3 条的实质不是某句话写错，而是**指路指到一个不存在的入口**：设置里
那一栏的真名是 ``settings.pages.models``（当前 = 「模型与渠道」），而仓库里飘着把栏目名
写成「模型网关」的 19 处（含 503 诊断那句）、写成「渠道管理」的、把独立一栏「媒体存储」
挂到「模型网关」底下的，以及只写半截「模型」的简写（87 处，注释、docstring 与用户可见
文案混在一起）。用户照着提示去找菜单，找不到 —— 一条本来能自救的报错因此变成死路。

（本段刻意不把旧写法连「设置」一起写成完整路径，否则守卫会扫到自己。）

规矩：

1. 导航路径的栏目名只能取 locale 里的真实页签名（从 ``settings.pages.*`` 推导，
   改栏目名时守卫自动跟着走，不需要再改一遍代码）。
2. 旧写法与半截简写一律不许出现在源码、脚本、测试、前端、语言包与活文档里。
3. 守卫不能对着空气生效：真名必须在文本面出现足够多次。
4. 词头决定该查哪一套页签名：写「设置 →…」按 zh 语言包核，写 ``Settings →…`` 按 en 核，
   写 ``Cài đặt →…`` 按 vi 核 —— 每种界面语言里真实存在的栏目名都不一样。同一条规矩对
   英文与越南语同样成立：那边飘过 ``Model Config``、``Model Gateway``、``User Management``、
   ``Cấu hình mô hình``、半截的 ``Mô hình`` 等一串假入口名，连语言包自己的文案也没例外。

例外 ``docs/changes``：那是逐字的历史台账，记录的是当时的确切字句；改掉就等于伪造历史。
"""

from __future__ import annotations

import ast
import functools
import json
import re
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
LOCALE_DIR = ROOT / "frontend" / "public" / "locales"
LOCALE_ZH = LOCALE_DIR / "zh" / "translation.json"

SURFACES = (
    "src/novelvideo",
    "scripts",
    "tests",
    "frontend/src",
    "frontend/public/locales",
    "docs/zh",
    "docs/en",
    "deploy",
    "examples",
    "readme",
)
# 根目录的部署与启动入口：运维人员照着这些注释装系统，指错路一样是死路。
STANDALONE_FILES = (
    "README.md",
    "README.en.md",
    "llms.txt",
    "AGENTS.md",
    "DESIGN.md",
    # 部署 skill 是人和 agent 都照着执行的运维手册，菜单名写错就是死路。
    ".codex/skills/dramaclaw-docker-deploy/SKILL.md",
    "docker-compose.yml",
    "docker-compose.aipdd.yml",
    "docker-compose.release.yml",
    "Dockerfile",
    "Dockerfile.cn",
    ".mcp.json",
    "pyproject.toml",
    "启动系统-Start.bat",
    "启动系统-Start.command",
    "安装环境-Setup.bat",
    "安装环境-Setup.command",
)
SCAN_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".ps1",
    ".bat",
    ".command",
}
SKIP_PARTS = ("__pycache__", "node_modules", "dist", "docs/changes")
# 网关子仓自己的用户手册（多语言）：装系统的人同样照着它点菜单，得一起管。
GATEWAY_DIR = "third_party/dramaclaw-gateway"

# 曾经飘在文本面里的假入口名（含一次改名时的「配置」粘连）。
DEAD_NAMES = ("模型网关", "渠道管理", "模型与渠道配置", "模型配置", "模型与通道")
SEP = r"(?:→|›|»|·|・|-|–|—|:|：)"

# 真实页签名 + 已经核准的子面板名（子面板在同一页里，路径可以继续往下写）。
EXTRA_TARGETS = ("火山语音", "本地 ComfyUI", "默认生成模型", "媒体存储", "素材库", "Seedance 素材")

# 词头 -> 该词头该查哪套页签名。二级页签名从各自语言包现场取，不写死。
class NavRule(TypedDict):
    lang: str
    dead: tuple[str, ...]
    half: str | None
    canonical: str


NAV_HEADS: dict[str, NavRule] = {
    "设置": {
        "lang": "zh",
        "dead": DEAD_NAMES,
        "half": r"^模型(?!与渠道)",
        "canonical": "模型与渠道",
    },
    "Settings": {
        "lang": "en",
        "dead": (
            "Model Config",
            "Model config",
            "Model Configuration",
            "Model Gateway",
            "Channel Management",
            "User Management",
            "Model & Channels",
            "Prompt library",
            "API keys",
            "Default Generation Models",
        ),
        # 半截写法：菜单叫 ``Models & Channels``，光写 ``Models`` 就是又一例指到不存在入口。
        "half": r"^Models(?!\s*&\s*Channels)",
        "canonical": "Models & Channels",
    },
    "Cài đặt": {
        "lang": "vi",
        "dead": (
            "Cấu hình mô hình",
            "Cấu hình model",
            "Mô hình",
            "Quản lý người dùng",
            "Thư viện phong cách",
            "Lưu trữ phương tiện",
            "API key",
        ),
        "half": None,  # vi 的半截写法（裸 "Mô hình"）已在 dead 里，不再叠一条
        "canonical": "Model & Kênh",
    },
}


def _locale(lang: str) -> dict:
    return json.loads((LOCALE_DIR / lang / "translation.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _page_labels(lang: str = "zh") -> frozenset[str]:
    """该语言里真实存在的一级页签名 + 唯一核准的二级面板名（页内标题不算入口，故意不收录）。

    缓存是必须的：主测试会对每个文件重取一次标签，不缓存就是把万行语言包重解析几千遍。
    """

    settings = _locale(lang)["settings"]
    labels = {
        str(v).strip()
        for v in settings["pages"].values()
        if isinstance(v, str) and str(v).strip()
    }
    panel = settings["modelConfig"]["modelList"].get("panelTitle")
    if isinstance(panel, str) and panel.strip():
        labels.add(panel.strip())
    return frozenset(labels)


@functools.lru_cache(maxsize=None)
def _window_pattern(head: str) -> re.Pattern[str]:
    """词头 + 分隔符 + 定长窗口：带空格的页签名（``API 密钥``、``Models & Channels``）靠它。"""

    return re.compile(rf"{re.escape(head)}\s*(?:→|›|»)\s*(.{{1,44}})")


@functools.lru_cache(maxsize=None)
def _nav_pattern(head: str) -> re.Pattern[str]:
    """词头 + 任意分隔符 + 栏目名（不写分隔符的「设置里模型网关」这类散文不算指路）。

    中文栏目名之间不空格，取 2-14 个非空白字就够了；拉丁栏目名带空格（
    ``Models & Channels``），只能取固定长度窗口再做前缀判定。

    本段刻意不把旧写法连词头一起写成完整路径，否则守卫会扫到自己。
    """

    if head == "设置":
        return re.compile(rf"设置\s*{SEP}\s*([^\s「」，。；、/)）]{{2,14}})")
    return _window_pattern(head)


# 操作系统的设置面板不是产品菜单，得从拉丁词头的命中里剔掉（如 System Settings → Privacy）。
HEAD_EXCLUSIONS = ("System ", "システム ", "macOS ")


def _texts_in(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix != ".py":
        return [raw]
    # Python 的相邻字符串字面量在 parse 阶段就合并成一个 Constant，所以跨行的
    # 「…请在「设置"  " → 模型网关」…」只能靠 AST 抓，纯文本正则会漏。
    try:
        tree = ast.parse(raw)
    except SyntaxError:  # pragma: no cover - 坏文件由 ruff 拦在前面
        return [raw]
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for rel in SURFACES:
        base = ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in path.as_posix() for part in SKIP_PARTS):
                continue
            files.append(path)
    for rel in STANDALONE_FILES:
        path = ROOT / rel
        if path.is_file():
            files.append(path)
    files.extend(sorted((ROOT / GATEWAY_DIR).glob("README*.md")))
    return sorted(set(files))


def test_every_settings_nav_path_names_a_real_page() -> None:
    """中 / 英 / 越 三种词头各自对各自的语言包核：假入口名与半截简写一律拒。"""

    offenders: list[str] = []
    hits_of_canonical: dict[str, int] = {head: 0 for head in NAV_HEADS}
    for path in _scanned_files():
        for text in _texts_in(path):
            for head, spec in NAV_HEADS.items():
                canonical = spec["canonical"]
                allowed = _page_labels(spec["lang"]) | set(EXTRA_TARGETS) | {canonical}
                for match in _nav_pattern(head).finditer(text):
                    prefix = text[max(0, match.start() - len(head) - 12) : match.start()]
                    if head != "设置" and any(prefix.endswith(skip) for skip in HEAD_EXCLUSIONS):
                        continue
                    target = match.group(1).lstrip("*「”\"'`( ").strip()
                    if not target:
                        continue
                    if target.startswith(canonical):
                        hits_of_canonical[head] += 1
                        continue
                    if any(target.startswith(dead) for dead in spec["dead"]):
                        offenders.append(f"{path.relative_to(ROOT)}: {head} → {target!r}")
                        continue
                    half = spec["half"]
                    if half and re.match(half, target):
                        # 半截简写：菜单叫完整页签名，不叫被截短的那一截。
                        offenders.append(f"{path.relative_to(ROOT)}: {head} → {target!r}（半截简写）")
                        continue
                    if not target.startswith(tuple(allowed)):
                        # 认不出的目标：要么是新菜单名（补进语言包后守卫自动放行），要么是散文误伤。
                        continue

    assert not offenders, "导航路径指到了不存在的入口：\n  " + "\n  ".join(sorted(set(offenders)))
    floor = {"设置": 20, "Settings": 10, "Cài đặt": 2}
    for head, count in hits_of_canonical.items():
        assert count >= floor[head], (
            f"「{head} → {NAV_HEADS[head]['canonical']}」只出现 {count} 次（底线 {floor[head]}），"
            "守卫可能已经对着空气生效"
        )


def test_ascii_arrow_nav_path_does_not_name_a_dead_page() -> None:
    """运维文档里有人拿 ASCII 箭头（两个连字符加大于号）写导航路径，也得拦。

    不把这种写法并入主分隔符：代码注释里到处都是 ``x -> y`` 这种函数式写法，
    扩了分隔符只会把守卫泡成误报。这一条只拿假入口名做精准比对。

    （本段刻意不把词头跟 ASCII 箭头连起来写，否则守卫会扫到自己。）
    """

    dead_all = sorted(
        {dead for spec in NAV_HEADS.values() for dead in spec["dead"]},
        key=len,
        reverse=True,
    )
    ascii_nav = re.compile(r"(?:设置|Settings|Cài đặt)\s*->\s*(" + "|".join(re.escape(d) for d in dead_all) + r")")

    offenders: list[str] = []
    for path in _scanned_files():
        for text in _texts_in(path):
            for match in ascii_nav.finditer(text):
                offenders.append(f"{path.relative_to(ROOT)}: …{match.group(0)!r}")

    assert not offenders, "ASCII 箭头的导航路径指到了不存在的入口：\n  " + "\n  ".join(sorted(set(offenders)))


def test_locale_copy_only_points_at_labels_that_locale_actually_renders() -> None:
    """语言包自己的文案也不能骗自己：用户在该语言下看到的页签名就是 ``settings.pages.*``。

    这组文案里最阴的一种：界面页签翻成了 ``Model & Kênh``，提示语还写着 ``Cấu hình mô
    hình`` —— 用户在菜单里找不到这个词，报错自己就成了死路。zh / en / vi 三种词头
    都按“该语言自己的页签名 + 唯一核准的二级面板名”核；en / zh 的页签名允许
    作为跨国通用写法出现在其他语言包里（运维文档里常见）。
    """

    offenders: list[str] = []
    # 运维文档里常见拿英文 / 中文页签名当通用写法，对其他语言包放行。
    cross_ok = {"Models & Channels", "模型与渠道"}
    for lang_dir in sorted(p for p in LOCALE_DIR.iterdir() if p.is_dir()):
        flat = _page_labels(lang_dir.name) | _page_labels("en") | _page_labels("zh") | cross_ok | set(EXTRA_TARGETS)
        text = (lang_dir / "translation.json").read_text(encoding="utf-8")
        for head in NAV_HEADS:
            for match in _window_pattern(head).finditer(text):
                target = match.group(1).lstrip("*「”\"'`( ").strip()
                if not target:
                    continue
                if not any(target.startswith(label) for label in flat if label):
                    offenders.append(f"locales/{lang_dir.name}: {head} → {target[:32]!r}")

    assert not offenders, "语言包里的指路词在该语言的页签里不存在：\n  " + "\n  ".join(sorted(set(offenders)))


def test_locale_page_label_still_drives_the_canonical_nav_name() -> None:
    """栏目名从 locale 推导：改名要同时改这里认的那个值，防止两边各说各话。"""

    for head, spec in NAV_HEADS.items():
        labels = _page_labels(spec["lang"])
        assert labels, f"{spec['lang']} 语言包里读不到 settings.pages —— 守卫会对着空气生效"
        assert spec["canonical"] in labels, (
            f"{head} 词头下的真名已改：当前页签名 {sorted(labels)!r}，文案与守卫得跟着改"
        )
