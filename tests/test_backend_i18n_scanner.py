"""`scripts/check_backend_i18n.py` 第五类口径（私有包装参数直连流）的行为锁定。

背景：四类老口径看不见「中文写进自家包装函数」的逃检。2026-10-06 扩门后，扫描器会先在本
文件里找出把形参原样交给 `current_task=` / `logs=` / `message=` / `report()` / `log()` /
dict `"message"` 的私有包装，再检查调用点有没有把中文字面量绑到那个形参上。

这些断言同时钉住两个方向的错误：
  * 漏检（扩门白做了）——包装调用点的中文必须命中；
  * 误报（把标签参数也算成文案，棘轮会被噪音淹没）——`label=` / f-string 片段必须不命中。
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.m07

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_backend_i18n.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("check_backend_i18n", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _hits(source: str) -> list[tuple[int, str]]:
    module = _load_scanner()
    tree = ast.parse(source)
    scanner = module.Scanner(module.wrapper_registry(tree))
    scanner.visit(tree)
    return sorted(scanner.hits)


def _direct_hits(source: str) -> list[tuple[int, str]]:
    """只要第五类口径（私有包装直连流）的命中。"""
    return [hit for hit in _hits(source) if "直连" in hit[1]]


# --------------------------------------------------------------------------- #
# 漏检方向：私有包装必须被看见


def test_positional_wrapper_argument_is_caught():
    source = (
        "def _update(ctx, task_type, scope, progress, current_task):\n"
        "    get_task_manager().update_progress_for_project(\n"
        "        ctx, task_type, 0, scope=scope, progress=progress,\n"
        '        current_task=current_task, logs=[current_task],\n'
        "    )\n"
        '\n\n_update(ctx, "freezone_image", job_id, 0.5, "正在绘制画面...")\n'
    )
    hits = _hits(source)
    assert len(hits) == 1
    assert "current_task=" in hits[0][1]
    # 命中的行号必须落在中文字面量那一行，否则就地写 `# i18n-exempt` 放不掉它。
    assert "正在绘制画面" in source.splitlines()[hits[0][0] - 1]


def test_keyword_message_wrapper_argument_is_caught():
    hits = _hits(
        "def _step(stage, status, *, message=\"\", **extra):\n"
        "    return {\"ok\": True, \"stage\": stage, \"message\": message}\n"
        '\n\n_step("ep1", "failed", message="第 1 集尚未有故事")\n'
    )
    assert len(hits) == 1
    assert "message=" in hits[0][1]


def test_nested_wrapper_and_same_name_overloads_are_caught():
    # runner 里的 `def update(...)` 大多嵌在 `_run_xxx()` 内部；stage_asset 同名定义了三次。
    hits = _hits(
        "async def run(ctx):\n"
        "    def update(progress, current_task):\n"
        "        manager.update_progress_for_project(ctx, \"t\", 0,\n"
        "            progress=progress, current_task=current_task, logs=[current_task])\n"
        '\n    update(0.2, "拆解分集大纲...")\n'
        '\n    update(0.8, "已完成")\n'
    )
    assert len(hits) == 2


def test_log_callback_wrapper_positional_argument_is_caught():
    # `log` 由调用方传进来的场景（comfyui_executor / scene_voxel）。
    hits = _hits(
        "def _log(msg, log):\n"
        "    log(msg)\n"
        '\n\n_log("开始渲染", log=logger)\n'
    )
    assert len(hits) == 1


def test_migrated_lmsg_call_is_not_hit():
    assert _direct_hits(
        "def _update(ctx, current_task):\n"
        "    manager.update_progress_for_project(ctx, current_task=current_task, logs=[current_task])\n"
        "\n"
        '_update(ctx, lmsg("tasks.progress.screenplay.saved", "剧本已保存", filename="a.txt"))\n'
    ) == []


def test_error_slot_is_caught_because_the_driver_throws_it():
    # 用户在 8080 上看到的「第 4 集自动化失败：…」就来自这里：
    # pipeline-driver.ts 抛的是 `new Error(step.error || step.message)`。
    # `_step` 用的是 `out["error"] = error` 下标赋值，不是 dict 字面量，所以下标赋值也得算交接点。
    assert len(_direct_hits(
        "def _step(stage, *, message=\"\", error=\"\"):\n"
        "    out = {\"ok\": False, \"stage\": stage, \"message\": message}\n"
        "    if error:\n"
        "        out[\"error\"] = error\n"
        "    return out\n"
        "\n"
        '_step("ep4", error="第 4 集尚未有故事，请先填写故事内容")\n'
    )) == 1


def test_next_hint_slot_is_deliberately_not_counted():
    # next_hint 在 PlanStep 契约里，但前端全仓无消费点；把它计进口径只会抬基线。
    assert _direct_hits(
        "def _step(stage, *, next_hint=\"\"):\n"
        "    return {\"stage\": stage, \"next_hint\": next_hint}\n"
        '\n\n_step("ep4", next_hint="下一步请生成草图")\n'
    ) == []


# --------------------------------------------------------------------------- #
# 误报方向：没流向面板的参数不能算文案


def test_non_flowing_label_argument_is_ignored():
    # huimeng `_to_upload_url(label="首帧")`：label 只进错误文案的拼装，不是进度/日志面。
    # （函数体内那行 `log(f"已转换 {label}")` 本身是老口径的正当命中，不算在这条上。）
    assert _direct_hits(
        "def _to_upload_url(value, *, label, log):\n"
        "    if not value:\n"
        '        raise ValueError(f"unsupported media reference for {label}")\n'
        "    log(f\"已转换 {label}\")\n"
        "    return value\n"
        '\n\n_to_upload_url(path, label="首帧", log=log)\n'
    ) == []


def test_fragment_inside_fstring_is_ignored():
    # cognee `stage_name="原文导入"`：真正上屏的中文在 `log(f"{stage_name}失败...")` 那条里，
    # 那条已经被 `log()` 口径抓到；把标签参数也算一遍只会制造重复噪音。
    assert _direct_hits(
        "async def _retry(stage_name, operation, log):\n"
        "    try:\n"
        "        await operation()\n"
        "    except Exception as exc:\n"
        '        log(f"{stage_name}失败，准备重试(1/1): {exc}")\n'
        '\n\nawait _retry(stage_name="原文导入", operation=op, log=log)\n'
    ) == []


def test_wrapper_param_never_handed_off_is_ignored():
    assert _direct_hits(
        "def _note(title, sink):\n"
        "    sink.append(title)\n"
        '\n\n_note("内部标记", sink=[])\n'
    ) == []


# --------------------------------------------------------------------------- #
# 老四类不能被扩门改坏


@pytest.mark.parametrize(
    "source, needle",
    [
        ('update_progress(0.1, "解析原文...")\n', "update_progress()"),
        ('manager.update_progress_for_project(ctx, current_task="解析原文...")\n', "current_task="),
        ('manager.update_progress_for_project(ctx, logs=["解析完成"])\n', "logs="),
        ('return {"message": "已开始生成 3 集剧本"}\n', '"message"'),
    ],
)
def test_legacy_positions_still_caught(source: str, needle: str) -> None:
    hits = _hits(source)
    assert len(hits) == 1
    assert needle in hits[0][1]


# --------------------------------------------------------------------------- #
# 闸门端到端：新文件里的包装中文必须把 CI 拦红


_WRAPPER_SOURCE = (
    "def _update(ctx, task_type, scope, progress, current_task):\n"
    "    manager.update_progress_for_project(\n"
    "        ctx, task_type, 0, scope=scope, progress=progress,\n"
    "        current_task=current_task, logs=[current_task],\n"
    "    )\n"
    '\n\n_update(ctx, "t", job_id, 0.4, "逐帧绘制画面...")\n'
)


def _fake_backend_tree(tmp_path: Path) -> Path:
    pkg = tmp_path / "src" / "novelvideo"
    pkg.mkdir(parents=True)
    (pkg / "runner.py").write_text(_WRAPPER_SOURCE, encoding="utf-8")
    return tmp_path


def test_gate_fails_on_wrapper_chinese_in_an_unlisted_file(tmp_path, monkeypatch) -> None:
    module = _load_scanner()
    monkeypatch.chdir(_fake_backend_tree(tmp_path))
    # 没有 allowlist 时的行为：新命中直接失败，而不是只提醒。这道就是 ci.yml 跑的那行。
    assert module.main([]) == 1


def test_gate_passes_once_the_count_is_frozen(tmp_path, monkeypatch) -> None:
    module = _load_scanner()
    tree = _fake_backend_tree(tmp_path)
    monkeypatch.chdir(tree)
    assert module.main(["--write"]) == 0
    frozen = json.loads((tree / "scripts" / "backend_i18n_allowlist.json").read_text(encoding="utf-8"))
    assert frozen == {"src/novelvideo/runner.py": 1}
    # 冻结后同一处存量不再报红；再往上加一条就会红。
    assert module.main([]) == 0
    (tree / "src" / "novelvideo" / "runner.py").write_text(
        _WRAPPER_SOURCE + '_update(ctx, "t", job_id, 0.9, "又漏一句中文")\n',
        encoding="utf-8",
    )
    assert module.main([]) == 1
