"""项目三类目录路径统一管理。"""

import shutil
import sqlite3
from pathlib import Path

from novelvideo.config import OUTPUT_DIR, RUNTIME_DIR, STATE_DIR

_SQLITE_MAGIC = b"SQLite format 3\x00"


class ProjectPaths:
    """统一管理项目的 output/state/runtime 目录。"""

    # Sidecar files are consolidated into the main .db by VACUUM INTO, so we
    # deliberately skip them during migration.
    _SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

    LEGACY_STATE_ITEMS = (
        "data.db",
        "data.db-wal",
        "data.db-shm",
        "cognee_system",
        "project_config.json",
    )

    LEGACY_RUNTIME_ITEMS = (
        "logs",
        "temp_sketch_panels",
    )

    def __init__(self, user: str, project: str):
        self.user = user
        self.project = project
        self._output_dir_override: Path | None = None
        self._state_dir_override: Path | None = None
        self._runtime_dir_override: Path | None = None

    @classmethod
    def from_explicit_dirs(
        cls,
        user: str,
        project: str,
        *,
        output_dir: str | Path,
        state_dir: str | Path,
        runtime_dir: str | Path,
    ) -> "ProjectPaths":
        """构造带显式目录覆盖的实例。

        封装 override 赋值，让调用方（如路由从 `ProjectRecord` 重建路径）不必再
        手改 `_output_dir_override` 等私有属性。
        """

        paths = cls(user, project)
        paths._output_dir_override = Path(output_dir)
        paths._state_dir_override = Path(state_dir)
        paths._runtime_dir_override = Path(runtime_dir)
        return paths

    @classmethod
    def from_context(cls, ctx) -> "ProjectPaths":
        return cls.from_explicit_dirs(
            ctx.owner_username,
            ctx.project_name,
            output_dir=ctx.output_dir,
            state_dir=ctx.state_dir,
            runtime_dir=ctx.runtime_dir,
        )

    @property
    def output_dir(self) -> Path:
        if self._output_dir_override is not None:
            return self._output_dir_override
        return Path(OUTPUT_DIR) / self.user / self.project

    @property
    def state_dir(self) -> Path:
        if self._state_dir_override is not None:
            return self._state_dir_override
        return Path(STATE_DIR) / self.user / self.project

    @property
    def runtime_dir(self) -> Path:
        if self._runtime_dir_override is not None:
            return self._runtime_dir_override
        return Path(RUNTIME_DIR) / self.user / self.project

    @property
    def data_db(self) -> Path:
        return self.state_dir / "data.db"

    @property
    def cognee_system_dir(self) -> Path:
        return self.state_dir / "cognee_system"

    @property
    def project_config(self) -> Path:
        return self.state_dir / "project_config.json"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def staging_dir(self) -> Path:
        return self.runtime_dir / "staging"

    @property
    def temp_sketch_panels_dir(self) -> Path:
        return self.runtime_dir / "temp_sketch_panels"

    # ------------------------------------------------------------------ #
    # Globally shared paths (director OS foundation)                      #
    # ------------------------------------------------------------------ #
    # Definitions / training data / artifacts are shared across **all
    # users and all projects** in this installation. This is the true
    # flywheel — anyone's runs feed everyone's registry and training
    # pool. Project facts (data.db, sketches, verify_reports) stay
    # per-project under `state/<user>/<project>/` and `output/...`.
    #
    # The old `user_shared_*` properties are kept as deprecated aliases
    # that now resolve to the global paths, so stale callers don't blow
    # up during the transition. Prefer `global_shared_*` for new code.
    @property
    def global_shared_dir(self) -> Path:
        return Path(STATE_DIR) / "_shared"

    @property
    def global_shared_verification_db(self) -> Path:
        return self.global_shared_dir / "verification.db"

    @property
    def global_shared_training_db(self) -> Path:
        return self.global_shared_dir / "director_training.db"

    @property
    def global_shared_artifacts_dir(self) -> Path:
        return self.global_shared_dir / "artifacts"

    # ------------------------------------------------------------------ #
    # Back-compat aliases (deprecated, prefer global_shared_* above)      #
    # ------------------------------------------------------------------ #
    @property
    def user_shared_dir(self) -> Path:
        return self.global_shared_dir

    @property
    def user_shared_verification_db(self) -> Path:
        return self.global_shared_verification_db

    @property
    def user_shared_training_db(self) -> Path:
        return self.global_shared_training_db

    @property
    def user_shared_artifacts_dir(self) -> Path:
        return self.global_shared_artifacts_dir

    def has_legacy_payload(self) -> bool:
        legacy_items = (*self.LEGACY_STATE_ITEMS, *self.LEGACY_RUNTIME_ITEMS)
        return any((self.output_dir / name).exists() for name in legacy_items)

    def exists(self) -> bool:
        return (
            self.state_dir.exists()
            or self.runtime_dir.exists()
            or self.has_legacy_payload()
        )

    def ensure_dirs(self) -> None:
        for directory in (
            self.output_dir,
            self.state_dir,
            self.runtime_dir,
            self.logs_dir,
            self.staging_dir,
            self.temp_sketch_panels_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def _migration_marker(self) -> Path:
        return self.state_dir / ".migrated"

    def bootstrap_from_legacy_output(self) -> None:
        """将旧单目录布局中的状态/运行时文件复制到新目录（仅首次）。"""
        if self._migration_marker.exists():
            return
        if not self.has_legacy_payload():
            # Nothing to migrate — create marker so we don't re-probe on every open.
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._migration_marker.touch()
            return
        self.ensure_dirs()
        self._copy_missing_items(self.output_dir, self.state_dir, self.LEGACY_STATE_ITEMS)
        self._copy_missing_items(self.output_dir, self.runtime_dir, self.LEGACY_RUNTIME_ITEMS)
        self._migration_marker.touch()

    @classmethod
    def _copy_missing_items(cls, src_root: Path, dst_root: Path, names: tuple[str, ...]) -> None:
        for name in names:
            src = src_root / name
            dst = dst_root / name
            if not src.exists():
                continue
            if cls._is_sqlite_sidecar(src):
                # Main .db handles WAL consolidation; sidecars don't get copied.
                continue
            try:
                if src.is_dir():
                    # Merge into dst — ensure_dirs() may have pre-created an
                    # empty target (logs/, temp_sketch_panels/, ...).
                    cls._copy_tree(src, dst)
                elif not dst.exists():
                    cls._copy_file(src, dst)
            except FileExistsError:
                # Another worker finished the same copy between our exists()
                # check and copytree — treat as success.
                continue

    @classmethod
    def _is_sqlite_sidecar(cls, path: Path) -> bool:
        return any(path.name.endswith(suffix) for suffix in cls._SQLITE_SIDECAR_SUFFIXES)

    @classmethod
    def _is_sqlite_db(cls, path: Path) -> bool:
        if not path.is_file() or cls._is_sqlite_sidecar(path):
            return False
        try:
            with open(path, "rb") as f:
                return f.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
        except OSError:
            return False

    @classmethod
    def _copy_file(cls, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if cls._is_sqlite_db(src):
            cls._sqlite_backup(src, dst)
        else:
            shutil.copy2(src, dst)

    @classmethod
    def _copy_tree(cls, src: Path, dst: Path) -> None:
        """Merge *src* into *dst*; SQLite files go through VACUUM INTO."""
        dst.mkdir(parents=True, exist_ok=True)
        for entry in src.iterdir():
            target = dst / entry.name
            if cls._is_sqlite_sidecar(entry):
                continue
            if entry.is_dir():
                cls._copy_tree(entry, target)
            elif entry.is_file() and not target.exists():
                cls._copy_file(entry, target)

    @staticmethod
    def _sqlite_backup(src: Path, dst: Path) -> None:
        """Consolidated snapshot via VACUUM INTO — atomic and WAL-safe."""
        with sqlite3.connect(src) as conn:
            conn.execute("VACUUM INTO ?", (str(dst),))
