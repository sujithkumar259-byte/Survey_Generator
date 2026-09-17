from __future__ import annotations

import pytest

from survey_generator.llm.client import LLMError, _extract_json


def test_extract_json_variants():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('prefix [1, 2] suffix') == [1, 2]
    assert _extract_json('{"items": [1, 2]}') == {"items": [1, 2]}


@pytest.mark.parametrize("raw", ["", "not json", "```json\nnot json\n```"])
def test_extract_json_invalid(raw):
    with pytest.raises(LLMError):
        _extract_json(raw)
