from __future__ import annotations

import argparse
import asyncio
import logging

from app.models import ConversationRequest
from app.service import ConversationService


async def run_cli(session_id: str, user_id: str) -> None:
    service = ConversationService()
    print(f"Session {session_id} for {user_id}. Type 'exit' to quit.")
    while True:
        message = input("you> ").strip()
        if message.lower() in {"exit", "quit"}:
            break
        response = await service.handle_message(
            ConversationRequest(session_id=session_id, user_id=user_id, message=message)
        )
        print(f"bot> {response.final_response}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the support orchestrator CLI.")
    parser.add_argument("--session-id", default="cli-session")
    parser.add_argument("--user-id", default="user-1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(run_cli(args.session_id, args.user_id))


if __name__ == "__main__":
    main()
