import asyncio

from project2_agent_orchestrator.app.llm import OpenAILlmClient
from project2_agent_orchestrator.app.models import OrchestratorDecision


def run(coro):
    return asyncio.run(coro)


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return type(
            "FakeResponse",
            (),
            {
                "output_parsed": OrchestratorDecision(
                    selected_agents=["shipping_agent"],
                    execution_mode="single_agent",
                    reasoning="test",
                )
            },
        )()


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_openai_llm_client_uses_responses_parse_with_structured_output() -> None:
    fake_client = FakeOpenAI()
    llm_client = OpenAILlmClient(model="test-model")
    llm_client._client = fake_client

    parsed = run(
        llm_client.parse(
            task_name="orchestrator_routing",
            system_prompt="system",
            user_payload={"message": "Where is my package?"},
            response_model=OrchestratorDecision,
        )
    )

    assert parsed.selected_agents == ["shipping_agent"]
    assert fake_client.responses.kwargs["model"] == "test-model"
    assert fake_client.responses.kwargs["text_format"] is OrchestratorDecision
    assert fake_client.responses.kwargs["input"][0] == {"role": "system", "content": "system"}
