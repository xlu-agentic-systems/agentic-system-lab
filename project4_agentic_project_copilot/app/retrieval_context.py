from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project4_agentic_project_copilot.app.models import DocumentReference, RetrievalScope, SessionContext


@dataclass(frozen=True)
class DocumentRetrievalTarget:
    scope: RetrievalScope
    document_ids: list[str] | None
    documents: list[DocumentReference]
    reasoning: str | None = None
    reason: str | None = None


class RetrievalContextResolver:
    def __init__(self, document_store) -> None:
        self.document_store = document_store

    def resolve(self, context: SessionContext, message: str = "") -> DocumentRetrievalTarget:
        scope = scope_from_message(message) or context.retrieval_scope
        if scope == "all":
            documents = [document_reference(document) for document in self.document_store.list_documents()]
            if not documents:
                return DocumentRetrievalTarget(
                    scope=scope,
                    document_ids=[],
                    documents=[],
                    reasoning="The user asked about files, but no uploaded document chunks are available.",
                    reason="I do not have an uploaded file in this workspace yet. Upload a markdown, text, or PDF file first, then ask about it.",
                )
            return DocumentRetrievalTarget(scope=scope, document_ids=None, documents=documents)

        if scope == "selected":
            selected = self.existing_selected_documents(context)
            if not selected:
                return DocumentRetrievalTarget(
                    scope=scope,
                    document_ids=[],
                    documents=[],
                    reasoning="The user asked about selected files, but this session has no selected documents.",
                    reason="I do not have selected files for this session yet. Attach files or switch retrieval scope back to current.",
                )
            return DocumentRetrievalTarget(
                scope=scope,
                document_ids=[document.document_id for document in selected],
                documents=selected,
            )

        if not context.current_document_id:
            return DocumentRetrievalTarget(
                scope=scope,
                document_ids=[],
                documents=[],
                reasoning="The user asked about files, but this session has no current document.",
                reason="I do not have a file selected for this session yet. Upload a markdown, text, or PDF file first, then ask about it.",
            )
        filename = context.current_document_filename or context.current_document_id
        document = DocumentReference(document_id=context.current_document_id, filename=filename)
        return DocumentRetrievalTarget(scope=scope, document_ids=[context.current_document_id], documents=[document])

    def existing_selected_documents(self, context: SessionContext) -> list[DocumentReference]:
        existing = []
        for reference in context.selected_documents:
            document = self.document_store.get_document(reference.document_id)
            if document is not None and self.document_store.has_documents(document_id=document.document_id):
                existing.append(document_reference(document))
        if len(existing) != len(context.selected_documents):
            context.selected_documents = existing
        return existing

    def has_target(self, context: SessionContext) -> bool:
        if context.retrieval_scope == "all":
            return self.document_store.has_documents()
        if context.retrieval_scope == "selected":
            return bool(self.existing_selected_documents(context))
        return bool(context.current_document_id)


def document_reference(document) -> DocumentReference:
    return DocumentReference(document_id=document.document_id, filename=document.filename)


def attach_document(context: SessionContext, document: DocumentReference) -> None:
    context.selected_documents = [
        existing for existing in context.selected_documents if existing.document_id != document.document_id
    ]
    context.selected_documents.append(document)


def detach_document(context: SessionContext, document_id: str) -> bool:
    before = len(context.selected_documents)
    context.selected_documents = [
        document for document in context.selected_documents if document.document_id != document_id
    ]
    return len(context.selected_documents) != before


def detach_missing_documents(context: SessionContext, document_ids: list[str]) -> None:
    missing = set(document_ids)
    context.selected_documents = [
        document for document in context.selected_documents if document.document_id not in missing
    ]


def references_from_chunks(chunks: list[Any]) -> list[DocumentReference]:
    references: list[DocumentReference] = []
    seen = set()
    for chunk in chunks:
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        references.append(DocumentReference(document_id=chunk.document_id, filename=chunk.filename))
    return references


def asks_about_files(text: str) -> bool:
    return any(term in text for term in ("this file", "file", "document", "doc", "pdf", "markdown"))


def scope_from_message(message: str) -> RetrievalScope | None:
    text = message.strip().lower()
    if any(
        phrase in text
        for phrase in (
            "selected documents",
            "selected document",
            "selected docs",
            "selected doc",
            "selected files",
            "selected file",
            "attached documents",
            "attached document",
            "attached docs",
            "attached doc",
            "attached files",
            "attached file",
        )
    ):
        return "selected"
    if any(
        phrase in text
        for phrase in (
            "all documents",
            "all docs",
            "all files",
            "all uploaded documents",
            "all uploaded docs",
            "all uploaded files",
            "across documents",
            "across docs",
            "across files",
            "which document",
            "which doc",
            "which file",
        )
    ):
        return "all"
    if any(phrase in text for phrase in ("this file", "current file", "focused file", "this document", "current document")):
        return "current"
    return None


def should_answer_current_document(text: str, context: SessionContext) -> bool:
    if not (context.current_document_id or context.selected_documents or context.retrieval_scope == "all"):
        return False
    if asks_about_files(text):
        return True
    return _is_document_followup(text) and _recent_file_context(context)


def _is_document_followup(text: str) -> bool:
    normalized = text.strip(" ?!.").lower()
    if normalized in {
        "how about now",
        "what about now",
        "try now",
        "now",
        "ok now",
        "can you answer now",
        "can you do it now",
    }:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "what does it say",
            "what is it doing",
            "tell me about it",
            "say something about it",
            "summarize it",
            "explain it",
            "describe it",
        )
    )


def _recent_file_context(context: SessionContext) -> bool:
    recent_text = " ".join(turn.content.lower() for turn in context.history[-6:])
    return any(term in recent_text for term in ("file", "document", "pdf", "markdown", "uploaded", "upload"))
