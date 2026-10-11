"""[aipdd fork delta req41] 静态守卫：部署形态数据（站点地址 / 本机兜底地址 / 兜底模型名）
不许写死在代码与编排文件里。

req41 的起因是用户指着 ``docker-compose.aipdd.yml`` 里的一行问：「https://susciyuan.com/v1
是我在数据库填写的地址，NEWAPI_PUBLIC_BASE_URL 的 localhost 也是我在数据库填的，你怎么知道
是 localhost？TEXT_MODEL_FALLBACK 为什么一定是 ap-glm-5.2？」——这些问题没有「代码比部署更
早知道答案」的合法情形：

* 网关 / 前端 / ComfyUI / VectCut / 导演台跑在哪、什么主机名与端口，是**部署形态数据**；
* 渠道里有什么模型名，是**渠道目录数据**（req40 已立规）；
* 只有「渠道定了、端点就定了」的公共厂商地址（AIPDD / 火山方舟 / MediaKit / Anthropic …）
  可以留在代码里预填。

本守卫把这些口径钉成代码层面的规矩，防止下一次有人图省事在调用点补一个
``"http://localhost:3000/v1"``，让「换地址就要改代码」原地复发。四条规则：

1. **部署文件**（compose ×3 / Dockerfile ×2 / ``.env.example``）里生效的 env 赋值不许出现
   本机兜底地址、站点域名、具体模型名；容器健康检查与端口绑定例外（那是结构性写法）。
2. **源码与前端**里被当作地址的字符串字面量（模块常量 / 字面默认值 / 直接 return）不许出现
   本机兜底地址，例外逐条登记原因（api 容器访问自己的 loopback 是结构性的）。
3. **部署方站点域名**在任何代码、编排、模板里都不许出现。
4. **兜底模型名**不许出现在部署文件里（``TEXT_MODEL_FALLBACK`` 等只能留空）。
5. 白名单不留死条目（棘轮）：登记过的位置若代码里已消失，同样失败。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "novelvideo"
FRONTEND_SRC = ROOT / "frontend" / "src"

DEPLOY_FILES = (
    "docker-compose.yml",
    "docker-compose.aipdd.yml",
    "docker-compose.release.yml",
    "Dockerfile",
    "Dockerfile.cn",
    ".env.example",
)

# 部署方自己的站点 / 网关域名：这些是「用户在设置里填的数据」，出现在仓库里就是写死。
FORBIDDEN_SITE_HOSTS = ("susciyuan.com",)

# 本机兜底地址：代码不能假定「一定部署在本机」。
_LOOPBACK_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|::1)(?::\d+)?")

# 部署文件里生效的赋值行：``KEY: value``（compose）/ ``KEY=value``（.env.example）。
_COMPOSE_ASSIGN_RE = re.compile(
    r"^\s{2,}(?P<key>[A-Z][A-Z0-9_]*):\s*[\"']?(?P<value>.*?)[\"']?\s*$"
)
_ENV_ASSIGN_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")

# 结构性例外：这些行里的 loopback 不是「猜站点地址」，而是容器自检 / 宿主端口绑定。
_DEPLOY_STRUCTURAL_RE = re.compile(
    r"""^(?:
        \s*test:\                       # compose healthcheck（容器内自检自己）
        |\s*-\s*[\"']?127\.0\.0\.1:     # 端口绑定只挂回环（部署方可覆盖）
        |\s*-?\s*\"?127\.0\.0\.1:       # 同上，缩进/引号形态差异
        |HEALTHCHECK\b                  # Dockerfile 自检
        |CMD\b                          # Dockerfile 指令
    )""",
    re.VERBOSE,
)

# compose 里的 ``ports:`` / ``expose`` 行同样属结构性写法。
_DEPLOY_PORT_LINE_RE = re.compile(r"^\s*(ports|expose)\s*:")

# 模型名字面量：req40 的族前缀 + ``ap-`` 这类渠道内命名，出现在部署文件即写死兜底名。
_MODEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?:ap|doubao|deepseek|qwen|kimi|moonshot|minimax|glm|grok|"
    r"gemini|claude|gpt|seedream|seedance|seededit|funasr|indextts|cosyvoice)"
    r"-[A-Za-z0-9][A-Za-z0-9._-]*(?![A-Za-z0-9])"
)

# 规则 2 的扫描面：模块级常量赋值 / 函数里的字面 return / ``or`` 兜底尾巴。
_URL_CONST_RE = re.compile(
    r"^(?:export\s+)?(?:const\s+|let\s+)?[A-Z][A-Z0-9_]{2,}\s*(?::[^=]+)?=\s*[\"'](?P<value>[^\"']*)[\"']"
)
_URL_RETURN_RE = re.compile(r"^\s*return\s+[\"'](?P<value>[^\"']*)[\"']")
_URL_DEFAULT_RE = re.compile(
    r"(?:environ\.get|getenv)\([^)]*,\s*[\"'](?P<value>[^\"']*)[\"']\s*\)"
)
_DICT_VALUE_RE = re.compile(r"\"(?P<key>[A-Za-z_0-9]*base_url)\"\s*:\s*\"(?P<value>[^\"]*)\"")

# 逐条登记的本机地址残留（``文件:标识`` → 原因）。这些都是「进程访问自己」或
# 「厂商自带默认端口」的结构性事实，不是对用户部署的猜测；代码里消失后请删掉这条
# （有棘轮用例）。
LOOPBACK_ALLOWLIST: dict[str, str] = {
    # api 容器内部回环：MCP server / hermes worker 与 api 同容器，loopback 即服务自身，
    # 端口 8780 由镜像固定（Dockerfile CMD），不随用户部署变。
    "agent/local_tools.py:API_INNER_URL": "MCP 与 api 同容器，容器内 loopback 即 api 自身",
    "chat/hermes_pool.py:DEFAULT_API_URL": "hermes worker 回调本进程 api 的容器内 loopback",
    "agent/mcp.py:return": "内置 MCP server 定义里「容器内访问自身」的 loopback 出口",
    # Ollama 的 11434 是它自己文档化的默认监听端口（厂商自带默认，不是我们的部署数据），
    # 且只是网关后台渠道表单里的可改预填值。
    "newapi_provisioner.py:ollama": "Ollama 自带默认端口 11434（网关渠道表单可改预填值）",
    # 前端：浏览器里真值来自 window.location.origin；下面两条一个是「docker exec 进容器
    # 后访问容器自身」的示例配置，一个是无 window 分支的占位。
    "features/mcp/McpPage.tsx:DOCKER_API_URL": "docker exec 进容器后访问容器自身 loopback",
    "features/mcp/McpPage.tsx:apiBase-fallback": "无 window（非浏览器）分支的占位地址",
}


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*")


def _iter_source_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.suffix in {".py", ".ts", ".tsx"}
        and "__pycache__" not in path.parts
        and "node_modules" not in path.parts
        and path.name not in {"vite-env.d.ts"}
    )


def _loopback_hits(root: Path, *, skip_tests: bool) -> list[tuple[str, int, str]]:
    """返回 ``（相对路径, 行号, 行内容）``：被当作地址使用的 loopback 字面量。"""

    hits: list[tuple[str, int, str]] = []
    for path in _iter_source_files(root):
        if skip_tests and (
            "__tests__" in path.parts or path.name.startswith("test_")
        ):
            continue
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(_read(path), start=1):
            if _is_comment_line(line):
                continue
            for pattern in (
                _URL_CONST_RE,
                _URL_RETURN_RE,
                _URL_DEFAULT_RE,
                _DICT_VALUE_RE,
            ):
                for match in pattern.finditer(line):
                    value = match.group("value")
                    if _LOOPBACK_RE.search(value):
                        hits.append((rel, lineno, line.strip()))
    return hits


# ---------------------------------------------------------------------------
# 规则 1：部署文件里生效的赋值不许写死本机地址 / 站点域名 / 模型名。
# ---------------------------------------------------------------------------


def _deploy_active_assignments(rel: str) -> list[tuple[int, str, str]]:
    """``（行号, 键, 值）`` ——只取真正生效的赋值行，注释与结构性行排除。"""

    rows: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(_read(ROOT / rel), start=1):
        if _is_comment_line(line) or _DEPLOY_STRUCTURAL_RE.match(line):
            continue
        if _DEPLOY_PORT_LINE_RE.match(line):
            continue
        match = _COMPOSE_ASSIGN_RE.match(line) or _ENV_ASSIGN_RE.match(line)
        if not match:
            continue
        key = match.group("key")
        rows.append((lineno, key, match.group("value")))
    return rows


def test_deploy_files_do_not_prefill_loopback_addresses() -> None:
    offenders: list[str] = []
    total = 0
    for rel in DEPLOY_FILES:
        path = ROOT / rel
        assert path.exists(), f"部署文件不存在（改名请同步本守卫）：{rel}"
        for lineno, key, value in _deploy_active_assignments(rel):
            total += 1
            if "${" in value:  # compose 插值：真实值来自部署方的 .env，不算写死
                continue
            if _LOOPBACK_RE.search(value):
                offenders.append(f"{rel}:{lineno} {key}={value!r}")
    assert total >= 20, f"部署文件赋值行扫描异常（只解析到 {total} 行），守卫可能失效"
    assert not offenders, (
        "部署文件把「网关 / 服务跑在哪」写死成了本机地址。换主机名或不做本机部署时这些值"
        "全部错：请改成留空 + ``${VAR:-}`` 透传，让部署方在 .env 或「设置 → 模型与渠道」里填。\n"
        + "\n".join(offenders)
    )


def test_deploy_files_do_not_ship_fallback_model_names() -> None:
    offenders: list[str] = []
    for rel in DEPLOY_FILES:
        for lineno, key, value in _deploy_active_assignments(rel):
            if "${" in value:
                continue
            token = _MODEL_TOKEN_RE.search(value)
            if token:
                offenders.append(f"{rel}:{lineno} {key}={value!r} → {token.group(0)!r}")
    assert not offenders, (
        "部署文件写死了具体模型名。渠道目录才是模型名的真源（req40）：兜底项应当留空，"
        "由「设置 → 模型与渠道」绑定的槽位 + 渠道目录决定。\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 规则 3：部署方站点域名不许出现在仓库里。
# ---------------------------------------------------------------------------


def test_operator_site_hosts_are_not_hardcoded() -> None:
    offenders: list[str] = []
    scan_roots = [SRC_ROOT, FRONTEND_SRC, ROOT / "scripts"]
    for path in list(scan_roots):
        if not path.exists():
            continue
        for file_path in _iter_source_files(path):
            rel = str(file_path)
            for lineno, line in enumerate(_read(file_path), start=1):
                if _is_comment_line(line):
                    continue
                for host in FORBIDDEN_SITE_HOSTS:
                    if host in line:
                        offenders.append(f"{rel}:{lineno} {host} → {line.strip()[:80]}")
    for rel in DEPLOY_FILES:
        for lineno, line in enumerate(_read(ROOT / rel), start=1):
            if _is_comment_line(line):
                continue
            for host in FORBIDDEN_SITE_HOSTS:
                if host in line:
                    offenders.append(f"{rel}:{lineno} {host} → {line.strip()[:80]}")
    assert not offenders, (
        "这些行把部署方自己的站点域名写进了代码/编排。它是用户在数据库里填的数据，"
        "换域名就得改代码：请改成读设置 / 读请求 Host / 读 env。\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 规则 2 + 5：源码里的本机地址必须逐条登记，且不留死条目。
# ---------------------------------------------------------------------------


def test_backend_loopback_urls_are_registered() -> None:
    hits = _loopback_hits(SRC_ROOT, skip_tests=False)
    unregistered = [
        f"{rel}:{lineno} {text[:100]}"
        for rel, lineno, text in hits
        if not any(key.startswith(f"{rel}:") for key in LOOPBACK_ALLOWLIST)
    ]
    assert not unregistered, (
        "这些地址字面量把服务位置写死成了本机地址（用户换主机名 / 不做本机部署就会失效）。"
        "真源应是设置、渠道目录或请求 Host；确属「进程访问自己」这类结构性事实，"
        "才登记进 LOOPBACK_ALLOWLIST 并写清原因：\n" + "\n".join(unregistered)
    )


def test_frontend_loopback_urls_are_registered() -> None:
    hits = _loopback_hits(FRONTEND_SRC, skip_tests=True)
    unregistered = [
        f"{rel}:{lineno} {text[:100]}"
        for rel, lineno, text in hits
        if not any(key.startswith(f"{rel}:") for key in LOOPBACK_ALLOWLIST)
    ]
    assert not unregistered, (
        "前端把网关 / 服务地址的默认值写成了本机地址：浏览器端的真值应来自 /config 或"
        "window.location.origin，未配置就留空（表单显示空，让用户填自己的地址）。\n"
        + "\n".join(unregistered)
    )


def test_loopback_allowlist_is_not_stale() -> None:
    present = {rel for rel, _lineno, _text in _loopback_hits(SRC_ROOT, skip_tests=False)}
    present |= {rel for rel, _lineno, _text in _loopback_hits(FRONTEND_SRC, skip_tests=True)}
    stale = sorted(key for key in LOOPBACK_ALLOWLIST if key.split(":", 1)[0] not in present)
    assert not stale, (
        f"这些登记项对应的代码里已经没有本机地址了（顺手删掉，别留给下一个人误会）：{stale}"
    )
