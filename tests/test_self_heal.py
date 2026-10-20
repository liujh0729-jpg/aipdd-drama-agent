# SPDX-License-Identifier: Elastic-2.0
"""环境与代码自愈：上下文收集、模型分析、降级与白名单修复的安全边界。"""

from __future__ import annotations

import json

import pytest

from novelvideo import self_heal as sh


def _context() -> dict:
    return {
        "symptom": "生成草图失败",
        "tool_name": "aipdd_drama_generate_sketches",
        "error": "HTTP 500",
        "scope": "auto",
        "platform": "win32",
        "python": "3.11",
        "packaged": None,
        "repo_root": "E:/repo",
        "diagnostics": {
            "summary": {"fail": 1},
            "attention": [
                {"section": "model_gateway", "id": "gateway_reachable", "level": "fail", "detail": "HTTP 502"}
            ],
            "error": None,
        },
        "environment_plan": {
            "items": [{"id": "ffmpeg", "status": "missing", "auto_installable": False}],
            "summary": {"missing": 1},
        },
        "logs": [{"file": "api.log", "tail": "Traceback ..."}],
        "tool": {"name": "aipdd_drama_generate_sketches", "source": "def handler(): ..."},
        "traceback_frames": [{"file": "src/novelvideo/x.py", "line": 10, "snippet": "10: boom()"}],
    }


def _analysis(**overrides) -> dict:
    data = {
        "diagnosis": "网关不可达",
        "category": "model_gateway",
        "confidence": 0.82,
        "evidence": ["gateway_reachable: HTTP 502"],
        "repairs": [],
        "needs_human": False,
        "next_steps": ["检查网关"],
        "reply": "默认文本模型网关返回 502，请检查渠道配置。",
    }
    data.update(overrides)
    return data


@pytest.fixture
def stub_pipeline(monkeypatch):
    monkeypatch.setattr(sh, "build_context", lambda **_: _context())
    monkeypatch.setattr(sh, "resolve_text_model", lambda explicit="": "DC-test-text")
    monkeypatch.setattr(sh, "_audit", lambda entry: "audit-test")
    return monkeypatch


def test_diagnose_uses_default_text_model(stub_pipeline) -> None:
    seen: dict[str, str] = {}

    def fake_ask(*, model, system, user):
        seen["model"] = model
        seen["system"] = system
        seen["user"] = user
        return json.dumps(_analysis())

    stub_pipeline.setattr(sh, "_ask_model", fake_ask)
    out = sh.run_self_heal(symptom="生成草图失败", tool_name="aipdd_drama_generate_sketches")

    assert out["degraded"] is False
    assert out["model"] == "DC-test-text"
    assert out["analysis"]["diagnosis"] == "网关不可达"
    assert out["analysis"]["confidence"] == 0.82
    assert out["repairs"] == []
    assert out["restart_required"] is False
    assert seen["model"] == "DC-test-text"
    assert "JSON" in seen["system"]
    assert "生成草图失败" in seen["user"]


def test_diagnose_mode_never_applies_patch(stub_pipeline, tmp_path) -> None:
    root, target = _make_repo(tmp_path, "VALUE = 1\n")
    stub_pipeline.setattr(sh, "_repo_root", lambda: root)
    stub_pipeline.setattr(
        sh,
        "_ask_model",
        lambda **_: json.dumps(
            _analysis(
                repairs=[
                    {
                        "type": "patch_file",
                        "path": "src/novelvideo/demo.py",
                        "old": "VALUE = 1",
                        "new": "VALUE = 2",
                        "reason": "修复常量",
                    }
                ]
            )
        ),
    )

    out = sh.run_self_heal(symptom="值不对", mode="diagnose")

    assert out["repairs"][0]["status"] == "proposed"
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_degraded_when_model_slot_unresolved(stub_pipeline) -> None:
    def fail(_explicit=""):
        raise RuntimeError("槽位未绑定")

    stub_pipeline.setattr(sh, "resolve_text_model", fail)
    asked: list[str] = []
    stub_pipeline.setattr(sh, "_ask_model", lambda **_: asked.append("called") or "{}")

    out = sh.run_self_heal(symptom="生成失败")

    assert out["degraded"] is True
    assert "槽位未绑定" in (out["degraded_reason"] or "")
    assert out["analysis"]["category"] == "model_gateway"
    assert out["analysis"]["needs_human"] is True
    assert asked == []
    assert out["model"] is None
    # 即便环境计划里还有待办，降级根因（模型网关）也必须排在第一條。
    assert out["analysis"]["next_steps"][0].startswith("检查设置 → 模型与渠道")


def test_degraded_when_model_call_fails(stub_pipeline) -> None:
    def fail(**_):
        raise sh.SelfHealError("自愈分析模型网关返回 502：bad gateway")

    stub_pipeline.setattr(sh, "_ask_model", fail)
    out = sh.run_self_heal(symptom="生成失败", mode="repair")

    assert out["degraded"] is True
    assert "502" in (out["degraded_reason"] or "")
    assert out["analysis"]["repairs"] == []
    assert out["repairs"] == []


def test_invalid_mode_and_scope_raise(stub_pipeline) -> None:
    with pytest.raises(ValueError, match="mode"):
        sh.run_self_heal(symptom="x", mode="bogus")
    with pytest.raises(ValueError, match="scope"):
        sh.run_self_heal(symptom="x", scope="bogus")
    with pytest.raises(ValueError, match="symptom"):
        sh.run_self_heal(symptom="  ")


def test_redaction_removes_common_secrets() -> None:
    text = sh.redact_text("api_key=sk-abcdef123456 Bearer abc.def.ghi token: supersecret")
    assert "sk-abcdef123456" not in text
    assert "abc.def.ghi" not in text
    assert "supersecret" not in text
    assert "***" in text

    redacted = sh._redact_obj({"api_key": "xyz", "nested": {"Authorization": "Bearer zzz"}})
    assert redacted["api_key"] == "***"
    assert redacted["nested"]["Authorization"] == "***"


def test_parse_analysis_accepts_fenced_json() -> None:
    raw = "```json\n" + json.dumps(_analysis()) + "\n```"
    parsed = sh.parse_analysis(raw)
    assert parsed["category"] == "model_gateway"


def test_patch_applies_with_backup(tmp_path, monkeypatch) -> None:
    root, target = _make_repo(tmp_path, "def f():\n    return 1\n")
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": "src/novelvideo/demo.py",
                "old": "return 1",
                "new": "return 2",
                "reason": "修复返回值",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "applied"
    assert results[0]["restart_required"] is True
    assert "return 2" in target.read_text(encoding="utf-8")
    assert (tmp_path / "state" / "self_heal" / "backups").exists()
    backups = list((tmp_path / "state" / "self_heal" / "backups").rglob("demo.py"))
    assert backups and "return 1" in backups[0].read_text(encoding="utf-8")


def test_patch_requires_unique_match(tmp_path, monkeypatch) -> None:
    root, target = _make_repo(tmp_path, "X = 1\nX = 1\n")
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": "src/novelvideo/demo.py",
                "old": "X = 1",
                "new": "X = 2",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "rejected"
    assert "2 次" in results[0]["reason"]
    assert target.read_text(encoding="utf-8") == "X = 1\nX = 1\n"


def test_patch_rejects_syntax_error(tmp_path, monkeypatch) -> None:
    root, target = _make_repo(tmp_path, "def f():\n    return 1\n")
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": "src/novelvideo/demo.py",
                "old": "return 1",
                "new": "return (",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "rejected"
    assert "语法校验失败" in results[0]["reason"]
    assert target.read_text(encoding="utf-8") == "def f():\n    return 1\n"


def test_patch_rejects_path_outside_allowlist(tmp_path, monkeypatch) -> None:
    root, _ = _make_repo(tmp_path, "VALUE = 1\n")
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [{"type": "patch_file", "path": "README.md", "old": "a", "new": "b"}],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "rejected"
    assert "只允许修改" in results[0]["reason"]


def test_patch_allows_hermes_plugin_file(tmp_path, monkeypatch) -> None:
    root, target = _make_repo(
        tmp_path, "VALUE = 1\n", rel=".hermes/plugins/demo.py"
    )
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": ".hermes/plugins/demo.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "applied"
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_patch_rejects_security_prefix_case_insensitively(tmp_path, monkeypatch) -> None:
    root, _ = _make_repo(
        tmp_path, "VALUE = 1\n", rel="src/novelvideo/SECURITY/demo.py"
    )
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": "src/novelvideo/SECURITY/demo.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "rejected"
    assert "安全" in results[0]["reason"]


def test_patch_rejects_security_files(tmp_path, monkeypatch) -> None:
    root, _ = _make_repo(tmp_path, "VALUE = 1\n", rel="src/novelvideo/api/auth.py")
    _patch_env(monkeypatch, tmp_path, root)

    results = sh.apply_repairs(
        [
            {
                "type": "patch_file",
                "path": "src/novelvideo/api/auth.py",
                "old": "VALUE = 1",
                "new": "VALUE = 2",
            }
        ],
        mode="repair",
        allow_heavy=False,
    )

    assert results[0]["status"] == "rejected"
    assert "安全" in results[0]["reason"]


def test_install_env_item_requires_heavy_confirmation(monkeypatch) -> None:
    from novelvideo import env_setup_install

    monkeypatch.setattr(env_setup_install, "AUTO_INSTALLABLE_IDS", frozenset({"world"}))
    monkeypatch.setattr(
        env_setup_install,
        "install_item",
        lambda item: {"ok": True, "started": True, "item": item},
    )

    blocked = sh.apply_repairs(
        [{"type": "install_env_item", "item": "world"}], mode="repair", allow_heavy=False
    )
    assert blocked[0]["status"] == "needs_confirm"

    started = sh.apply_repairs(
        [{"type": "install_env_item", "item": "world"}], mode="repair", allow_heavy=True
    )
    # 安装是异步启动的：此刻只能如实报 started，不能谎称 applied。
    assert started[0]["status"] == "started"
    assert started[0]["item"] == "world"
    assert started[0]["result"]["started"] is True


def test_install_env_item_reports_manual_guidance_as_needs_manual(monkeypatch) -> None:
    from novelvideo import env_setup_install

    monkeypatch.setattr(env_setup_install, "AUTO_INSTALLABLE_IDS", frozenset({"world"}))
    monkeypatch.setattr(
        env_setup_install,
        "install_item",
        lambda item: {
            "ok": True,
            "manual": True,
            "item": item,
            "guide": "精简镜像不支持在该路径自动安装，请重建部署镜像。",
        },
    )

    results = sh.apply_repairs(
        [{"type": "install_env_item", "item": "world"}], mode="repair", allow_heavy=True
    )

    assert results[0]["status"] == "needs_manual"
    assert "重建部署镜像" in results[0]["guide"]


def test_run_self_heal_treats_string_false_as_not_heavy(stub_pipeline) -> None:
    from novelvideo import env_setup_install

    stub_pipeline.setattr(env_setup_install, "AUTO_INSTALLABLE_IDS", frozenset({"world"}))
    stub_pipeline.setattr(
        env_setup_install,
        "install_item",
        lambda item: {"ok": True, "started": True, "item": item},
    )
    stub_pipeline.setattr(
        sh,
        "_ask_model",
        lambda **_: json.dumps(
            _analysis(repairs=[{"type": "install_env_item", "item": "world"}])
        ),
    )

    out = sh.run_self_heal(
        symptom="环境缺 world", mode="repair", allow_heavy="false"
    )

    assert out["repairs"][0]["status"] == "needs_confirm"


def test_ensure_dir_rejects_outside_roots(tmp_path, monkeypatch) -> None:
    from novelvideo import config

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))

    rejected = sh.apply_repairs(
        [{"type": "ensure_dir", "path": str(tmp_path / "elsewhere")}],
        mode="repair",
        allow_heavy=False,
    )
    assert rejected[0]["status"] == "rejected"

    accepted = sh.apply_repairs(
        [{"type": "ensure_dir", "path": "logs/demo"}], mode="repair", allow_heavy=False
    )
    assert accepted[0]["status"] == "applied"
    assert (tmp_path / "state" / "logs" / "demo").is_dir()


def test_agent_instruction_relays_reply_and_proposals(stub_pipeline) -> None:
    stub_pipeline.setattr(
        sh,
        "_ask_model",
        lambda **_: json.dumps(
            _analysis(
                repairs=[
                    {"type": "ensure_dir", "path": "logs/demo", "reason": "缺目录"}
                ]
            )
        ),
    )

    out = sh.run_self_heal(symptom="目录缺失")

    instruction = out["agent_instruction"]
    assert "analysis.reply" in instruction
    assert "proposed" in instruction
    assert "不要直接倾倒原始 JSON" in instruction
    assert out["repairs"][0]["status"] == "proposed"


def test_agent_instruction_explains_degraded(stub_pipeline) -> None:
    def fail(_explicit=""):
        raise RuntimeError("槽位未绑定")

    stub_pipeline.setattr(sh, "resolve_text_model", fail)
    out = sh.run_self_heal(symptom="失败")

    assert out["degraded"] is True
    assert "degraded_reason" in out["agent_instruction"]
    assert "设置 → 模型与渠道" in out["agent_instruction"]


def test_collect_logs_includes_runtime_api_log(tmp_path, monkeypatch) -> None:
    from novelvideo import config

    runtime = tmp_path / "runtime"
    (runtime / "logs").mkdir(parents=True)
    (runtime / "api.log").write_text("line1\nline2\n", encoding="utf-8")
    (runtime / "logs" / "worker.log").write_text("worker\n", encoding="utf-8")
    monkeypatch.setattr(config, "RUNTIME_DIR", str(runtime))

    files = {row["file"] for row in sh._collect_logs()}
    assert {"api.log", "worker.log"} <= files


def _make_repo(tmp_path, source: str, rel: str = "src/novelvideo/demo.py"):
    root = tmp_path / "repo"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    return root, target


def _patch_env(monkeypatch, tmp_path, root) -> None:
    from novelvideo import config

    monkeypatch.setattr(sh, "_repo_root", lambda: root)
    monkeypatch.setattr(sh, "_packaged_kind", lambda: "")
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
