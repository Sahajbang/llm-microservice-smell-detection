"""Step 4/5 support tests: JSON extraction/repair from LLM responses."""

import pytest

from pipeline.json_utils import extract_json


def test_extracts_plain_json():
    assert extract_json('{"a": 1, "b": true}') == {"a": 1, "b": True}


def test_extracts_json_from_markdown_fence():
    text = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps!'
    assert extract_json(text) == {"a": 1}


def test_extracts_json_from_fence_without_language_tag():
    text = '```\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_extracts_json_with_leading_and_trailing_prose():
    text = 'Sure, here is my analysis: {"a": 1} -- let me know if you need more.'
    assert extract_json(text) == {"a": 1}


def test_raises_value_error_on_unparseable_text():
    with pytest.raises(ValueError):
        extract_json("this is not json at all")
