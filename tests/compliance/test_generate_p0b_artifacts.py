# SPDX-License-Identifier: Elastic-2.0
# SPDX-FileCopyrightText: 2026 SuperTale contributors

from __future__ import annotations

import pytest
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/compliance/generate_p0b_artifacts.py"
SPEC = importlib.util.spec_from_file_location("generate_p0b_artifacts_compliance", SCRIPT_PATH)
assert SPEC is not None
generator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = generator
SPEC.loader.exec_module(generator)
normalize_license = generator.normalize_license


def test_full_mit_license_text_normalizes_to_spdx_expression() -> None:
    expression, evidence = normalize_license(
        (
            "MIT License Copyright (c) 2022 OpenAI, Shantanu Jain Permission is hereby "
            "granted, free of charge, to any person obtaining a copy of this software "
            "and associated documentation files to deal in the Software without restriction. "
            "THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED."
        ),
        [],
    )

    assert expression == "MIT"
    assert evidence == "python package metadata License field"


def test_gplv3_or_later_classifier_normalizes_to_spdx_expression() -> None:
    expression, evidence = normalize_license(
        "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007",
        ["License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)"],
    )

    assert expression == "GPL-3.0-or-later"
    assert evidence == "python package classifier"


def test_unresolved_license_metadata_fails_instead_of_emitting_unknown_ref() -> None:
    with pytest.raises(ValueError, match="Unable to resolve package license"):
        normalize_license("", [])


@pytest.mark.parametrize(
    ("raw_license", "expected"),
    [
        ("MIT style", "MIT"),
        ("MIT-CMU", "MIT-CMU"),
    ],
)
def test_known_nonstandard_metadata_forms_normalize(raw_license: str, expected: str) -> None:
    expression, _ = normalize_license(raw_license, [])

    assert expression == expected
