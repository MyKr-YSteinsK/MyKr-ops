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
        ("01-Topic_COM鹿_Course.md", "reserved"),
        ("01-Topic_COM虏_Course.md", "reserved"),
        ("01-Topic_COM鲁_Course.md", "reserved"),
        ("01-Topic_CS_LPT鹿.md", "reserved"),
        ("01-Topic_CS_LPT虏.txt.md", "reserved"),
        ("01-Topic_CS_LPT鲁.md", "reserved"),
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


def test_unrelated_unicode_directory_components_remain_valid() -> None:
    parsed = parse_note_filename("01-Topic_课程_猫咪.md")

    assert parsed.first_level == "课程"
    assert parsed.course == "猫咪"
