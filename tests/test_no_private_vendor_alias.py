"""[aipdd fork delta] 静态守卫：发行方私有模型别名不许出现在系统里。

起因是「测试问题发现」里的这条生产报错：

    No available channel for model <发行方私有别名> under group default (distributor)

这个名字既不在官方媒体目录、也不在渠道清单里，却因为**历史上被内置过**残留在三处：
一条能力启发式（把私有名族写进了画质档位判定）、一批注释与文档、一片测试 fixture。
残留本身不直接发起调用，但它会让下一次「顺手内置一个名字」有可抄的样板 —— 而那个样板
正是这条 503 的来源。模型的**唯一**真源是渠道目录（req40 立的规矩），代码里不需要、
也不允许有任何厂商私有别名。

三条规则：

1. **文本面零出现**：源码、脚本、测试、前端、活文档、编排与模板里都不许有这个别名
   （历史台账 ``docs/changes`` 与需求报告原文例外 —— 它们记录的是当时的确切字句）。
2. **能力启发式不认别名**：画质档位只按公开型号形态（gpt-image 族）预置，私有名族
   一律不预置 —— 给不吃 ``quality`` 的模型预置画质，等于让网关顶回错误。
3. **官方目录不带别名**：随包发布的媒体目录里没有任何别名条目。
4. **唯一豁免是法定署名，且必须逐字核对**：`NOTICE` 的合作方/赞助商商标条款由
   ``scripts/compliance/generate_p0b_artifacts.py`` 生成，提到的是**发行主体**的商标（不是
   可选型号），Elastic-2.0 明令不得从分发副本里移除这类保留权利声明，因此不删。豁免只
   到这一句为止：下面那条白盒测试会把它钉死在商标条款里，一旦有人把别名接回路由、
   目录或页签文案，这条会立刻红。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 拆写：这份守卫文件自身也在扫描面里，写成完整字面量会把自己扫出来。
_ALIAS = "lin" + "gshan"
_ALIAS_RE = re.compile(_ALIAS, re.IGNORECASE)

SCAN_ROOTS = (
    "src/novelvideo",
    "scripts",
    "tests",
    "frontend/src",
    "frontend/public",
    "docs/zh",
    "docs/en",
    "deploy",
    "examples",
    "assets",
)
SCAN_FILES = (
    "README.md",
    "README.en.md",
    "DESIGN.md",
    "AGENTS.md",
    "llms.txt",
    ".mcp.json",
    "pyproject.toml",
    "docker-compose.yml",
    "docker-compose.aipdd.yml",
    "docker-compose.release.yml",
    # 合规台账生成器与它产出的 NOTICE：发行主体商标声明里带这个名字，得在白盒测试里盯住。
    "NOTICE",
    "scripts/compliance/generate_p0b_artifacts.py",
)
SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".sh", ".bat", ".ps1"}

# 历史台账记录的是「当时文案长什么样」，逐字引用不能改；需求报告引用的是网关原话。
SKIP_DIR_PARTS = ("__pycache__", "node_modules", "dist", "build", ".venv", "docs/changes")

# 唯一允许的残留：Elastic-2.0 要求保留的发行主体商标声明（NOTICE 与它的生成器）。
# 它提到的是合作方实体名，不是可选型号；下一节那条白盒测试把它钉在那一句里。
TRADEMARK_NOTICE_FILES = ("NOTICE", "scripts/compliance/generate_p0b_artifacts.py")
# 这段话在 NOTICE 里是整段，在生成器里是五串相邻字面量（Python 在 parse 阶段会合回一个
# 常量），所以按行拆开比对。
TRADEMARK_CLAUSE_LINES = (
    "Partner and sponsor logos shown in the project README (including Claymore AI",
    "Lab, \u65b0\u98de\u7fd4\u79d1\u6280 / Neo Flying Technology, \u7075\u5c71 / L.Shan AI, and \u94a6\u65b0\u63a7\u80a1 / K-NOVA",
    "Holding Group) are trademarks of their respective owners, displayed with",
    "permission for attribution purposes only. No trademark rights in those marks",
    "are granted by the Elastic License 2.0.",
)


def _scanned_text_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCAN_ROOTS:
        base = ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            text_path = path.as_posix()
            if any(part in text_path for part in SKIP_DIR_PARTS):
                continue
            files.append(path)
    for rel in SCAN_FILES:
        path = ROOT / rel
        if path.is_file():
            files.append(path)
    return sorted(files)


def test_private_vendor_alias_is_absent_from_every_text_surface() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in _scanned_text_files()
        if path.relative_to(ROOT).as_posix() not in TRADEMARK_NOTICE_FILES
        and _ALIAS_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, (
        f"发行方私有别名又回来了：{offenders}\n"
        "模型名只从渠道目录来（见 tests/test_no_hardcoded_model_names.py），"
        "代码与文档里不保留任何厂商私有别名。"
    )


def _clause_units(path: Path) -> list[str]:
    """比对的文本单元：NOTICE 按行拆，生成器拿 AST 常量（相邻字面量已合回一段）。"""

    raw = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return raw.splitlines()
    try:
        tree = ast.parse(raw)
    except SyntaxError:  # pragma: no cover - 坏文件由 ruff 拦在前面
        return raw.splitlines()
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_the_only_surviving_mention_is_the_trademark_notice() -> None:
    """豁免只能豁免那一句：发行主体的商标声明，逐字核对，多一处都算漂回。

    这比「整文件放过」紧得多：一旦有人把别名接回模型路由、渠道目录或页签文案，
    那一处不会落在这一句里，当场就能被这条断言抓住。
    """

    vendor_re = re.compile(_ALIAS + r"|\u7075\u5c71|L\.?Shan", re.IGNORECASE)
    for rel in TRADEMARK_NOTICE_FILES:
        units = _clause_units(ROOT / rel)
        # 商标声明的每一行都必须逐字存在（静默改写就是删法定署名，同样拒）。
        for line in TRADEMARK_CLAUSE_LINES:
            assert units.count(line) == 1, f"{rel}: 商标声明行被改动或重复：{line!r}"
        clause_start = next(i for i, u in enumerate(units) if u == TRADEMARK_CLAUSE_LINES[0])
        clause_units = set(units[clause_start : clause_start + len(TRADEMARK_CLAUSE_LINES)])
        for unit in units:
            for m in vendor_re.finditer(unit):
                assert unit in clause_units, (
                    f"{rel}: 发行方名称出现在了商标条款之外（…{unit[max(0, m.start() - 60):m.end() + 60]!r}）"
                )


def test_quality_defaults_do_not_special_case_the_alias_family() -> None:
    from novelvideo.catalog import media_model_discovery

    # 私有名族拿不到预置画质：上游不吃 quality 时，下发它只会被网关顶回。
    plain = media_model_discovery.default_image_capabilities(f"{_ALIAS}-g9")
    assert "qualityOptions" not in plain
    # 公开型号形态仍然照常预置 —— 这条守卫不是把画质档位整个关掉。
    gpt = media_model_discovery.default_image_capabilities("gpt-image-2")
    assert gpt["qualityOptions"] == ["low", "medium", "high"]


def test_bundled_media_catalog_carries_no_alias_entries() -> None:
    from novelvideo.model_gateway_settings import get_official_media_model_catalog

    for media_type in ("image", "video"):
        entries = get_official_media_model_catalog(media_type)
        assert entries, f"{media_type} 目录不该是空的（守卫会对着空气生效）"
        leaked = [e["id"] for e in entries if _ALIAS_RE.search(str(e["id"]))]
        assert not leaked, f"{media_type} 目录里混进了别名条目：{leaked}"
