from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse

from project4_agentic_project_copilot.app.models import ChatRequest, ChatResponse, UploadResponse
from project4_agentic_project_copilot.app.service import ProjectCopilotService


PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="Agentic Project Copilot")
service: ProjectCopilotService | None = None


def get_service() -> ProjectCopilotService:
    global service
    if service is None:
        service = ProjectCopilotService()
    return service


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "ui" / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await get_service().chat(request)


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    content = await file.read()
    return await get_service().upload_file(
        filename=file.filename or "uploaded.txt",
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )
