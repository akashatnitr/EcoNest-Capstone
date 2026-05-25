from orchestrator.llm.memory import _escape


def test_escape_quotes():
    text = "it's working"
    escaped = _escape(text)

    assert "\\'" in escaped
