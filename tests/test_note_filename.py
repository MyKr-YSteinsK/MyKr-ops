from __future__ import annotations

import pytest

from mykr_ops.notes import FilenameError, _validate_component, parse_note_filename


def test_parses_normal_note() -> None:
    parsed = parse_note_filename("01-Introduction_CS_Algorithms.md")

    assert parsed.sequence == "01"
    assert parsed.topic == "Introduction"
    assert parsed.first_level == "CS"
    assert parsed.course == "Algorithms"
    assert parsed.target_name == "01-Introduction.md"


def test_preserves_underscores_in_topic() -> None:
    parsed = parse_note_filename("08-Python_data_notes_CS_DataProcessing.md")

    assert parsed.topic == "Python_data_notes"
    assert parsed.target_name == "08-Python_data_notes.md"


@pytest.mark.parametrize(
    "name, message",
    [
        ("1-Topic_CS_Course.md", "sequence"),
        ("001-Topic_CS_Course.md", "sequence"),
        ("00-Topic_CS_Course.md", "sequence"),
        ("01-Topic__Course.md", "empty"),
        ("01-Topic_CS_.md", "empty"),
        ("01-Topic_CS_Course.MD", "lowercase"),
        ("01-Top:ic_CS_Course.md", "invalid Windows"),
        ("01-Topic_CON_Course.md", "reserved"),
        ("01-Topic_CS_CON.txt.md", "reserved"),
        ("01-Topic_COM¹_Course.md", "reserved"),
        ("01-Topic_CS_LPT².md", "reserved"),
        ("01-Topic_ CS_Course.md", "whitespace"),
        ("01-Topic_CS_Course..md", "period"),
    ],
)
def test_rejects_invalid_note_filename(name: str, message: str) -> None:
    with pytest.raises(FilenameError, match=message):
        parse_note_filename(name)


def test_directory_components_reject_underscores() -> None:
    with pytest.raises(FilenameError, match="underscore"):
        _validate_component("CS_notes", "first-level directory", allow_underscores=False)


@pytest.mark.parametrize(
    "name",
    [
        "01-Topic_COM" + chr(0x00B9) + "_Course.md",
        "01-Topic_COM" + chr(0x00B2) + "_Course.md",
        "01-Topic_COM" + chr(0x00B3) + "_Course.md",
        "01-Topic_CS_LPT" + chr(0x00B9) + ".md",
        "01-Topic_CS_LPT" + chr(0x00B2) + ".txt.md",
        "01-Topic_CS_LPT" + chr(0x00B3) + ".md",
    ],
)
def test_rejects_superscript_windows_device_names(name: str) -> None:
    with pytest.raises(FilenameError, match="reserved"):
        parse_note_filename(name)


@pytest.mark.parametrize(
    "name",
    [
        "01-Topic_COM" + chr(0x9E7F) + chr(0x7F1A) + "_Course.md",
        "01-Topic_COM" + chr(0x864F) + chr(0x5EEF) + "_Course.md",
        "01-Topic_COM" + chr(0x9C81) + chr(0x4E63) + "_Course.md",
        "01-Topic_CS_LPT" + chr(0x9E7F) + ".md",
        "01-Topic_CS_LPT" + chr(0x864F) + ".md",
        "01-Topic_CS_LPT" + chr(0x9C81) + ".md",
        "01-Topic_" + chr(0x8BFE) + chr(0x7A0B) + "_" + chr(0x732B) + chr(0x5496) + ".md",
    ],
)
def test_unrelated_unicode_device_prefixes_remain_valid(name: str) -> None:
    assert parse_note_filename(name).original_name == name


def test_unrelated_unicode_directory_components_remain_valid() -> None:
    parsed = parse_note_filename("01-Topic_课程_猫咪.md")

    assert parsed.first_level == "课程"
    assert parsed.course == "猫咪"
