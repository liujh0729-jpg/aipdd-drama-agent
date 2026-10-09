from pathlib import Path


def test_import_novel_closes_store_after_success(monkeypatch, tmp_path: Path) -> None:
    from novelvideo import cli

    novel = tmp_path / "novel.txt"
    novel.write_text("林昭走进钟楼。", encoding="utf-8")
    calls: list[str] = []

    class FakeStore:
        def __init__(self, project: str):
            calls.append(f"init:{project}")

        async def initialize(self):
            calls.append("initialize")

        async def ingest_novel(self, novel_path: str):
            calls.append(f"ingest:{Path(novel_path).name}")
            return {
                "char_count": 7,
                "characters": 1,
                "episodes": 1,
                "dataset": "novelvideo_foss_e2e",
            }

        async def close(self):
            calls.append("close")

    monkeypatch.setattr(cli, "CogneeStore", FakeStore)
    monkeypatch.setattr(cli, "_ensure_nest_asyncio", lambda: None)

    cli.import_novel(project="foss_e2e", novel=str(novel))

    assert calls == ["init:foss_e2e", "initialize", "ingest:novel.txt", "close"]


def test_cognee_ingest_closes_store_after_success(monkeypatch, tmp_path: Path) -> None:
    from novelvideo import cli

    novel = tmp_path / "novel.txt"
    novel.write_text("林昭走进钟楼。", encoding="utf-8")
    calls: list[str] = []

    class FakeStore:
        def __init__(self, project: str):
            calls.append(f"init:{project}")

        async def initialize(self):
            calls.append("initialize")

        async def ingest_novel(self, novel_path: str, rebuild: bool, target_episodes: int):
            calls.append(f"ingest:{Path(novel_path).name}:{rebuild}:{target_episodes}")
            return {
                "char_count": 7,
                "characters": 1,
                "episodes": 1,
                "dataset": "novelvideo_foss_e2e",
            }

        async def close(self):
            calls.append("close")

    monkeypatch.setattr(cli, "CogneeStore", FakeStore)
    monkeypatch.setattr(cli, "_ensure_nest_asyncio", lambda: None)

    cli.cognee_ingest(project="foss_e2e", novel=str(novel), rebuild=False, episodes=1)

    assert calls == ["init:foss_e2e", "initialize", "ingest:novel.txt:False:1", "close"]
