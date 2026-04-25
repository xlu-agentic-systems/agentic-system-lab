import asyncio

from project1_multi_agent_return_bot.app.llm import OpenAILlmClient
from project1_multi_agent_return_bot.app.models import RoutingOutput


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
                "output_parsed": RoutingOutput(
                    intent="return_request",
                    extracted_fields={},
                    missing_fields=["order_id", "item_id", "return_reason"],
                    clarification_question="Please provide the order ID, item ID, and reason for the return.",
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
            task_name="return_routing_agent",
            system_prompt="system",
            user_payload={"message": "I need a refund"},
            response_model=RoutingOutput,
        )
    )

    assert parsed.intent == "return_request"
    assert fake_client.responses.kwargs["model"] == "test-model"
    assert fake_client.responses.kwargs["text_format"] is RoutingOutput
    assert fake_client.responses.kwargs["input"][0] == {"role": "system", "content": "system"}
