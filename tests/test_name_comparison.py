from __future__ import annotations

import os
from pathlib import Path

import pytest

from mykr_ops.filesystem import direct_casefold_matches, names_equal


def test_ascii_names_compare_case_insensitively() -> None:
    assert names_equal("01-Topic.md", "01-topic.MD")


@pytest.mark.skipif(os.name != "nt", reason="Windows ordinal name comparison")
def test_windows_ordinal_comparison_handles_legal_unicode_case_variants(tmp_path: Path) -> None:
    existing = tmp_path / "Résumé.md"
    existing.write_text("note", encoding="utf-8")

    matches = direct_casefold_matches(tmp_path, "rÉSUMÉ.MD")

    assert matches == [existing]


@pytest.mark.skipif(os.name == "nt", reason="Windows uses CompareStringOrdinal instead of fallback casefold")
def test_non_windows_name_comparison_uses_deterministic_casefold_fallback() -> None:
    assert names_equal("Straße.md", "STRASSE.MD")
