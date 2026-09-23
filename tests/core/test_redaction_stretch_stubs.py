"""Stub tests asserting stretch redaction layers stay deferred (spec §13)."""
import pytest

from app.core.redaction import ContentRegexLayer, SaveTimeLintLayer


def test_save_time_lint_deferred():
    with pytest.raises(NotImplementedError):
        SaveTimeLintLayer().redact({})


def test_content_regex_deferred():
    with pytest.raises(NotImplementedError):
        ContentRegexLayer().redact({})
