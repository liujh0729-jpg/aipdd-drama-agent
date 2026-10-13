from __future__ import annotations

import re
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).parents[1]
RELEASE_FILE = "docker-compose.release.yml"
SOURCE_FILE = "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load((REPOSITORY_ROOT / RELEASE_FILE).read_text())


def test_repository_ships_only_source_and_release_compose_files() -> None:
    variants = sorted(
        {p.name for p in REPOSITORY_ROOT.glob("docker-compose*.y*ml")}
        | {p.name for p in REPOSITORY_ROOT.glob("compose.y*ml")}
    )
    # [aipdd fork delta] 白名单加入 fork 的部署编排 docker-compose.aipdd.yml（方案
    # A-1：拓扑对齐 release，仅把 api/web 换成本地 build）。本测试防的是“仓库里
    # 悄悄多出额外变体”（*.prod / *.dev / 旧文件名）；fork 编排是有意交付物，故
    # 显式列出，新增变体仍必须在此登记。
    assert variants == [
        "docker-compose.aipdd.yml",
        "docker-compose.release.yml",
        "docker-compose.yml",
    ]


def test_compose_builds_all_three_from_source() -> None:
    services = _compose()["services"]
    # [aipdd fork delta] Phase 0 新增第 4 个服务 renderer（HyperFrames producer 渲染引擎），
    # 同样从源码构建（services/hyperframes-renderer/Dockerfile）、aipdd/ 前缀、unless-stopped。
    assert set(services) == {"api", "newapi", "web", "renderer"}
    for name, service in services.items():
        assert "build" in service, f"{name} must carry a source build block"
        assert "pull_policy" not in service, f"{name} must not set pull_policy"
        assert service["image"].startswith("aipdd/"), name
        assert service.get("restart") == "unless-stopped", name


def test_gateway_builds_from_vendored_source() -> None:
    newapi = _compose()["services"]["newapi"]
    assert newapi["build"] == {
        "context": "${DRAMACLAW_GATEWAY_SRC:-./third_party/dramaclaw-gateway}",
        "dockerfile": "Dockerfile",
    }, newapi["build"]
    assert newapi["image"] == "aipdd/gateway"


def test_ce_images_are_local_build_tags_and_zero_external() -> None:
    # Fork is source-build only: api/web produce local tags, and no service pulls
    # a published registry image (the old claymorelab image-mode is retired).
    services = _compose()["services"]
    assert services["api"]["image"] == "aipdd/api"
    assert services["web"]["image"] == "aipdd/web"
    text = (REPOSITORY_ROOT / RELEASE_FILE).read_text()
    assert "claymorelab" not in text
    assert "${DRAMACLAW_VERSION" not in text
    assert "${DRAMACLAW_GATEWAY_VERSION" not in text


def test_no_compose_file_pulls_external_images() -> None:
    for name in (
        "docker-compose.yml",
        "docker-compose.release.yml",
        "docker-compose.aipdd.yml",
    ):
        assert "claymorelab" not in (REPOSITORY_ROOT / name).read_text(), name


def test_api_persists_generated_media_in_ce_data_volume() -> None:
    api = _compose()["services"]["api"]
    assert api["environment"] | {
        "NOVELVIDEO_DATA_ROOT": "/data",
        "NOVELVIDEO_OUTPUT_DIR": "/data/output",
        "NOVELVIDEO_STATE_DIR": "/data/state",
        "NOVELVIDEO_RUNTIME_DIR": "/data/runtime",
    } == api["environment"]
    assert "ce-data:/data" in api["volumes"]


def test_api_provisioner_env_matches_desktop_contract() -> None:
    api = _compose()["services"]["api"]
    env = api["environment"]
    # [aipdd fork delta req41] 网关地址是部署数据 / 设置页数据，编排文件一律留空：
    # 这里断言的正是「不许再把某个站点地址写进默认值」。换 newapi 地址只改 .env。
    assert env["NEWAPI_BASE_URL"] == "${NEWAPI_BASE_URL:-}"
    # 容器网络内的结构性服务名（不是公网入口），保留。
    assert env["NEWAPI_ADMIN_BASE_URL"] == "http://newapi:3000"
    assert env["NEWAPI_SQL_DSN"] == "local"
    assert env["NEWAPI_SQLITE_PATH"] == "/newapi-data/one-api.db"
    assert env["NEWAPI_ADMIN_USERNAME"] == "root"
    assert env["NEWAPI_PROVISIONER_ENABLED"] == "${NEWAPI_PROVISIONER_ENABLED:-true}"
    assert "newapi-data:/newapi-data" in api["volumes"]
    assert api["depends_on"] == {"newapi": {"condition": "service_started"}}


def test_gateway_shares_sqlite_volume_and_has_healthcheck() -> None:
    newapi = _compose()["services"]["newapi"]
    assert "newapi-data:/data" in newapi["volumes"]
    assert newapi["environment"]["SQL_DSN"] == ""
    assert "healthcheck" in newapi
    assert "http://localhost:3000/api/status" in " ".join(newapi["healthcheck"]["test"])


def test_compose_pins_env_file_long_syntax_ports_and_volumes() -> None:
    compose = _compose()
    api = compose["services"]["api"]
    assert api["env_file"] == [{"path": ".env", "required": False}]
    assert api["ports"] == ["${ST_API_PORT:-8780}:8780"]
    assert compose["services"]["newapi"]["ports"] == [
        "${ST_NEWAPI_BIND:-127.0.0.1}:${ST_NEWAPI_PORT:-3000}:3000"
    ]
    assert compose["services"]["web"]["ports"] == ["${ST_WEB_PORT:-8080}:80"]
    assert set(compose["volumes"]) == {"ce-data", "newapi-data"}


def test_source_file_extends_release_and_builds_all_three() -> None:
    source = yaml.safe_load((REPOSITORY_ROOT / SOURCE_FILE).read_text())

    assert set(source) == {"services", "volumes"}
    services = source["services"]
    assert set(services) == {"api", "newapi", "web", "renderer"}
    for name, service in services.items():
        assert set(service) == {"extends", "image", "build"}, f"{name} keys: {set(service)}"
        assert service["extends"] == {"file": RELEASE_FILE, "service": name}

    assert services["api"]["image"] == "aipdd/api"
    assert services["newapi"]["image"] == "aipdd/gateway"
    assert services["web"]["image"] == "aipdd/web"

    assert services["api"]["build"] == {
        "context": ".",
        "dockerfile": "Dockerfile",
        "args": {"INSTALL_WORLD": "${INSTALL_WORLD:-0}"},
    }
    assert services["newapi"]["build"] == {
        "context": "${DRAMACLAW_GATEWAY_SRC:-./third_party/dramaclaw-gateway}",
        "dockerfile": "Dockerfile",
    }
    assert services["web"]["build"] == {"context": "./frontend", "dockerfile": "Dockerfile"}
    # [aipdd fork delta] renderer 服务在源编排里只重申 extends/image/build（与 api/web 同形）。
    assert services["renderer"]["image"] == "aipdd/renderer"
    assert services["renderer"]["build"] == {
        "context": "./services/hyperframes-renderer",
        "dockerfile": "Dockerfile",
    }

    assert set(source["volumes"]) == {"ce-data", "newapi-data"}


def test_source_file_never_mentions_release_versions() -> None:
    source_text = (REPOSITORY_ROOT / SOURCE_FILE).read_text()

    assert "DRAMACLAW_VERSION" not in source_text
    assert "DRAMACLAW_GATEWAY_VERSION" not in source_text


def test_env_example_configures_data_root_instead_of_individual_directories() -> None:
    env_example = (REPOSITORY_ROOT / ".env.example").read_text()

    assert re.search(r"^NOVELVIDEO_OUTPUT_DIR=", env_example, re.MULTILINE) is None
    assert "# NOVELVIDEO_DATA_ROOT=" in env_example


def test_env_example_documents_image_variables() -> None:
    env_example = (REPOSITORY_ROOT / ".env.example").read_text()

    for key in ("DRAMACLAW_IMAGE_PREFIX", "DRAMACLAW_VERSION", "DRAMACLAW_GATEWAY_VERSION"):
        assert re.search(rf"^# {key}=", env_example, re.MULTILINE), key
