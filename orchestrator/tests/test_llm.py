from orchestrator.llm.models import LLMMessage


def test_llm_message():
    msg = LLMMessage(role="user", content="hello")

    assert msg.role == "user"
    assert msg.content == "hello"
