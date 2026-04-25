from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from project4_agentic_project_copilot.app.models import ChatResponse, DecisionLog


DEFAULT_TRACE_PATH = Path(__file__).resolve().parent.parent / "data" / "conversation_traces.jsonl"


class CopilotTrace(BaseModel):
    session_id: str
    user_message: str
    response: str
    decision_log: DecisionLog
    citations_count: int = 0
    has_sql: bool = False
    has_tool_call: bool = False
    errors: list[str] = Field(default_factory=list)


class JsonlTraceStore:
    def __init__(self, path: Path | str = DEFAULT_TRACE_PATH) -> None:
        self.path = Path(path)

    async def append_response(self, user_message: str, response: ChatResponse) -> None:
        trace = CopilotTrace(
            session_id=response.session_id,
            user_message=user_message,
            response=response.response,
            decision_log=response.decision_log,
            citations_count=len(response.citations),
            has_sql=response.generated_sql is not None,
            has_tool_call=response.tool_call is not None,
            errors=[response.tool_result.error] if response.tool_result and response.tool_result.error else [],
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as file:
            file.write(trace.model_dump_json() + "\n")
