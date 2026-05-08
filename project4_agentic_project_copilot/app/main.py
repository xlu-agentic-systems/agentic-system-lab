from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from agentic_system_lab.observability import configure_observability_logging
from project4_agentic_project_copilot.app.models import (
    ChatRequest,
    ChatResponse,
    DeleteDocumentResponse,
    DocumentListResponse,
    SelectDocumentResponse,
    TraceDetail,
    TraceListResponse,
    UploadResponse,
)
from project4_agentic_project_copilot.app.service import ProjectCopilotService
from project4_agentic_project_copilot.app.trace_store import TraceNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parent.parent

configure_observability_logging(project="project4")

app = FastAPI(title="Agentic Project Copilot")
service: ProjectCopilotService | None = None


def get_service() -> ProjectCopilotService:
    global service
    if service is None:
        service = ProjectCopilotService()
    return service


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        PROJECT_ROOT / "ui" / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/debug")
async def debug() -> FileResponse:
    return FileResponse(
        PROJECT_ROOT / "ui" / "debug.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await get_service().chat(request)


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...), session_id: str | None = Form(default=None)) -> UploadResponse:
    content = await file.read()
    return await get_service().upload_file(
        filename=file.filename or "uploaded.txt",
        content_type=file.content_type or "application/octet-stream",
        content=content,
        session_id=session_id,
    )


@app.get("/documents", response_model=DocumentListResponse)
async def list_documents() -> DocumentListResponse:
    return await get_service().list_documents()


@app.get("/debug/traces", response_model=TraceListResponse)
async def list_traces(limit: int = 50) -> TraceListResponse:
    return await get_service().list_traces(limit=limit)


@app.get("/debug/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str) -> TraceDetail:
    try:
        return await get_service().get_trace(trace_id)
    except TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/documents/{document_id}/select", response_model=SelectDocumentResponse)
async def select_document(document_id: str, session_id: str = Form(...)) -> SelectDocumentResponse:
    try:
        return await get_service().select_document(session_id=session_id, document_id=document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/documents/{document_id}", response_model=DeleteDocumentResponse)
async def delete_document(document_id: str, session_id: str | None = None) -> DeleteDocumentResponse:
    return await get_service().delete_document(session_id=session_id, document_id=document_id)
