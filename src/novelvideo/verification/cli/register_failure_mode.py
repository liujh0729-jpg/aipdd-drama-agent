"""CLI: register (or update) a failure mode in the user-shared verification.db.

This is the runtime landing pad for the **Registry-First Process (RFP)**:
whenever a skill (agent) observes a new class of sketch defect that
isn't covered by any active `code` in the shared registry, the skill
must stop, upsert a new entry via this CLI, then proceed with the
normal labels → execute → gate pipeline. The new entry's
`negative_prompt_clause` will automatically flow into subsequent edit
prompts (generator + correction + director) without further code edits.

Four text fields are mandatory on first insert; update calls can be
partial.

Usage (first insert):

    uv run python -m novelvideo.verification.cli.register_failure_mode \\
      --project-dir /Users/.../xuanchuanpian \\
      --code shot_scale_angle_mismatch \\
      --layer director \\
      --detection "..." \\
      --prevention-rule "..." \\
      --correction-template "..." \\
      --negative-prompt-clause "..." \\
      --gate-enabled 0

Later update (any subset):

    uv run python -m novelvideo.verification.cli.register_failure_mode \\
      --project-dir /Users/.../xuanchuanpian \\
      --code shot_scale_angle_mismatch \\
      --gate-enabled 1

Output is a JSON summary echoing what was stored + the new
`registry_version` (so callers can verify the change landed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


VALID_LAYERS = {"generator", "correction", "director"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register or update a sketch failure mode in verification.db."
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="SuperTale project directory (used only to resolve the user-shared verification.db)",
    )
    parser.add_argument("--code", required=True, help="snake_case failure-mode id")
    parser.add_argument(
        "--layer",
        choices=sorted(VALID_LAYERS),
        help="Which layer this mode applies to (required on first insert)",
    )
    parser.add_argument("--detection", help="VLM yes/no prompt text")
    parser.add_argument("--prevention-rule", help="Rubric text for skill authors / reviewers")
    parser.add_argument(
        "--correction-template",
        help="Template the skill uses when writing edit_instruction for this mode",
    )
    parser.add_argument(
        "--negative-prompt-clause",
        help="Sentence the registry auto-appends to generator / edit prompts",
    )
    parser.add_argument(
        "--gate-enabled",
        type=int,
        choices=[0, 1],
        default=None,
        help="Whether the visual gate should check for this mode (0=off, 1=on)",
    )
    parser.add_argument("--fixture-path", default=None, help="Optional fixture dir for regression tests")
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    from novelvideo.utils.project_paths import ProjectPaths

    parts = project_dir.parts
    if len(parts) < 2:
        print(json.dumps({"ok": False, "error": f"cannot resolve user from {project_dir}"}))
        return 2
    user, project = parts[-2], parts[-1]
    db_path = ProjectPaths(user, project).global_shared_verification_db

    from novelvideo.verification import failure_registry, version_hash
    from novelvideo.verification.global_registry_db import open_defs_db

    db = await open_defs_db(db_path)
    try:
        await failure_registry.ensure_seeded(db)

        existing = await failure_registry.get_by_code(db, args.code)
        fields: dict[str, object] = {}
        if args.layer is not None:
            fields["layer"] = args.layer
        if args.detection is not None:
            fields["detection"] = args.detection
        if args.prevention_rule is not None:
            fields["prevention_rule"] = args.prevention_rule
        if args.correction_template is not None:
            fields["correction_template"] = args.correction_template
        if args.negative_prompt_clause is not None:
            fields["negative_prompt_clause"] = args.negative_prompt_clause
        if args.gate_enabled is not None:
            fields["gate_enabled"] = int(args.gate_enabled)
        if args.fixture_path is not None:
            fields["fixture_path"] = args.fixture_path

        if existing is None:
            # First insert — layer + detection are mandatory.
            for required in ("layer", "detection"):
                if required not in fields or not fields[required]:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": f"new code {args.code!r} requires --{required.replace('_', '-')}",
                            }
                        )
                    )
                    return 2

        await failure_registry.upsert(db, args.code, **fields)

        stored = await failure_registry.get_by_code(db, args.code)
        registry_version = await version_hash.compute_registry_version(db)
    finally:
        await db.close()

    print(
        json.dumps(
            {
                "ok": True,
                "verification_db": str(db_path),
                "code": args.code,
                "operation": "insert" if existing is None else "update",
                "fields_touched": sorted(fields.keys()),
                "stored": stored,
                "registry_version_after": registry_version,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
