from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Annotated, Awaitable, Callable, Literal
from urllib import error, request

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal["pending", "processing", "completed", "failed"]
MockOutcome = Literal["success", "fail"]
WebhookEventType = Literal["video_generation.completed", "video_generation.failed"]

SIGNATURE_HEADER = "Signature"


class MockVideoProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = "dev-key"
    webhook_secret: str = "dev-secret"
    public_base_url: str = "http://127.0.0.1:9000"
    step_delay_seconds: float = Field(default=2.0, ge=0, le=120)
    callback_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)

    @classmethod
    def from_env(cls) -> "MockVideoProviderSettings":
        return cls(
            api_key=os.getenv("MOCK_VIDEO_PROVIDER_API_KEY", "dev-key"),
            webhook_secret=os.getenv("MOCK_VIDEO_PROVIDER_WEBHOOK_SECRET", "dev-secret"),
            public_base_url=os.getenv("MOCK_VIDEO_PROVIDER_PUBLIC_BASE_URL", "http://127.0.0.1:9000"),
            step_delay_seconds=float(os.getenv("MOCK_VIDEO_PROVIDER_STEP_DELAY_SECONDS", "2.0")),
            callback_timeout_seconds=float(os.getenv("MOCK_VIDEO_PROVIDER_CALLBACK_TIMEOUT_SECONDS", "5.0")),
        )


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str = Field(min_length=1, max_length=10_000)
    callback_url: str | None = None
    callback_id: str | None = None
    mock_outcome: MockOutcome = "success"
    mock_delay_seconds: float | None = Field(default=None, ge=0, le=120)


class WebhookEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    events: list[str] | None = None
    entity_id: str | None = None


class WebhookEndpointPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    events: list[str] | None = None
    entity_id: str | None = None


@dataclass
class MockAsset:
    asset_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes
    created_at: int


@dataclass
class MockVideo:
    session_id: str
    video_id: str
    prompt: str
    status: JobStatus
    callback_url: str | None
    callback_id: str | None
    outcome: MockOutcome
    delay_seconds: float
    video_url: str
    thumbnail_url: str
    duration: float
    created_at: int
    updated_at: int
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass
class MockWebhookEndpoint:
    endpoint_id: str
    url: str
    events: list[str] | None
    entity_id: str | None
    secret: str
    created_at: int
    updated_at: int


@dataclass
class MockWebhookDelivery:
    event_id: str
    event_type: WebhookEventType
    event_data: dict[str, object]
    target_url: str
    status_code: int | None
    ok: bool
    error: str | None
    created_at: str


@dataclass
class CallbackResult:
    status_code: int | None
    ok: bool
    error: str | None = None


CallbackSender = Callable[[str, bytes, dict[str, str], float], Awaitable[CallbackResult]]


@dataclass
class MockVideoProviderStore:
    videos: dict[str, MockVideo] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    assets: dict[str, MockAsset] = field(default_factory=dict)
    webhook_endpoints: dict[str, MockWebhookEndpoint] = field(default_factory=dict)
    deliveries: list[MockWebhookDelivery] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock)

    def create_video(
        self,
        request_body: VideoGenerationRequest,
        settings: MockVideoProviderSettings,
    ) -> MockVideo:
        now = int(time.time())
        session_id = _new_id("sess")
        video_id = _new_id("vid")
        base_url = settings.public_base_url.rstrip("/")
        delay_seconds = (
            request_body.mock_delay_seconds
            if request_body.mock_delay_seconds is not None
            else settings.step_delay_seconds
        )
        outcome: MockOutcome = request_body.mock_outcome
        if "mock:fail" in request_body.prompt.lower():
            outcome = "fail"
        video = MockVideo(
            session_id=session_id,
            video_id=video_id,
            prompt=request_body.prompt,
            status="pending",
            callback_url=request_body.callback_url,
            callback_id=request_body.callback_id,
            outcome=outcome,
            delay_seconds=delay_seconds,
            video_url=f"{base_url}/mock-files/{video_id}.mp4",
            thumbnail_url=f"{base_url}/mock-files/{video_id}.jpg",
            duration=_estimate_duration(request_body.prompt),
            created_at=now,
            updated_at=now,
        )
        with self.lock:
            self.videos[video_id] = video
            self.sessions[session_id] = video_id
        return video

    def get_video(self, video_id: str) -> MockVideo | None:
        with self.lock:
            return self.videos.get(video_id)

    def get_video_by_session(self, session_id: str) -> MockVideo | None:
        with self.lock:
            video_id = self.sessions.get(session_id)
            if video_id is None:
                return None
            return self.videos.get(video_id)

    def update_video_status(
        self,
        video_id: str,
        status_value: JobStatus,
        *,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> MockVideo | None:
        with self.lock:
            video = self.videos.get(video_id)
            if video is None:
                return None
            video.status = status_value
            video.updated_at = int(time.time())
            video.failure_code = failure_code
            video.failure_message = failure_message
            return video

    def create_asset(self, filename: str, mime_type: str, content: bytes) -> MockAsset:
        asset_id = _new_id("asset")
        asset = MockAsset(
            asset_id=asset_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            content=content,
            created_at=int(time.time()),
        )
        with self.lock:
            self.assets[asset_id] = asset
        return asset

    def get_asset(self, asset_id: str) -> MockAsset | None:
        with self.lock:
            return self.assets.get(asset_id)

    def create_webhook_endpoint(self, request_body: WebhookEndpointRequest) -> MockWebhookEndpoint:
        now = int(time.time())
        endpoint = MockWebhookEndpoint(
            endpoint_id=_new_id("ep"),
            url=request_body.url,
            events=request_body.events,
            entity_id=request_body.entity_id,
            secret=f"whsec_{uuid.uuid4().hex}",
            created_at=now,
            updated_at=now,
        )
        with self.lock:
            self.webhook_endpoints[endpoint.endpoint_id] = endpoint
        return endpoint

    def list_webhook_endpoints(self) -> list[MockWebhookEndpoint]:
        with self.lock:
            return list(self.webhook_endpoints.values())

    def get_webhook_endpoint(self, endpoint_id: str) -> MockWebhookEndpoint | None:
        with self.lock:
            return self.webhook_endpoints.get(endpoint_id)

    def update_webhook_endpoint(
        self, endpoint_id: str, request_body: WebhookEndpointPatchRequest
    ) -> MockWebhookEndpoint | None:
        with self.lock:
            endpoint = self.webhook_endpoints.get(endpoint_id)
            if endpoint is None:
                return None
            if request_body.url is not None:
                endpoint.url = request_body.url
            if "events" in request_body.model_fields_set:
                endpoint.events = request_body.events
            if "entity_id" in request_body.model_fields_set:
                endpoint.entity_id = request_body.entity_id
            endpoint.updated_at = int(time.time())
            return endpoint

    def delete_webhook_endpoint(self, endpoint_id: str) -> bool:
        with self.lock:
            return self.webhook_endpoints.pop(endpoint_id, None) is not None

    def rotate_webhook_secret(self, endpoint_id: str) -> MockWebhookEndpoint | None:
        with self.lock:
            endpoint = self.webhook_endpoints.get(endpoint_id)
            if endpoint is None:
                return None
            endpoint.secret = f"whsec_{uuid.uuid4().hex}"
            endpoint.updated_at = int(time.time())
            return endpoint

    def matching_webhook_endpoints(
        self, event_type: WebhookEventType, video: MockVideo
    ) -> list[MockWebhookEndpoint]:
        with self.lock:
            endpoints = list(self.webhook_endpoints.values())
        matching = []
        for endpoint in endpoints:
            if endpoint.events is not None and event_type not in endpoint.events:
                continue
            if endpoint.entity_id is not None and endpoint.entity_id not in {
                video.video_id,
                video.session_id,
            }:
                continue
            matching.append(endpoint)
        return matching

    def record_delivery(self, delivery: MockWebhookDelivery) -> None:
        with self.lock:
            self.deliveries.append(delivery)

    def list_deliveries(
        self,
        *,
        event_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 10,
    ) -> list[MockWebhookDelivery]:
        with self.lock:
            deliveries = list(reversed(self.deliveries))
        if event_type:
            deliveries = [delivery for delivery in deliveries if delivery.event_type == event_type]
        if entity_id:
            deliveries = [
                delivery
                for delivery in deliveries
                if delivery.event_data.get("video_id") == entity_id
                or delivery.event_data.get("session_id") == entity_id
            ]
        return deliveries[:limit]


async def send_http_callback(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
) -> CallbackResult:
    def _send() -> CallbackResult:
        callback_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(callback_request, timeout=timeout_seconds) as response:
                response.read()
                return CallbackResult(
                    status_code=response.status,
                    ok=200 <= response.status < 300,
                )
        except error.HTTPError as exc:
            return CallbackResult(status_code=exc.code, ok=False, error=str(exc))
        except (error.URLError, TimeoutError, OSError) as exc:
            return CallbackResult(status_code=None, ok=False, error=str(exc))

    return await asyncio.to_thread(_send)


def create_app(
    settings: MockVideoProviderSettings | None = None,
    store: MockVideoProviderStore | None = None,
    callback_sender: CallbackSender = send_http_callback,
) -> FastAPI:
    settings = settings or MockVideoProviderSettings.from_env()
    store = store or MockVideoProviderStore()
    app = FastAPI(title="Mock Video Provider API")
    app.state.mock_video_provider_settings = settings
    app.state.mock_video_provider_store = store

    @app.exception_handler(HTTPException)
    async def video_provider_http_exception_handler(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"message": str(exc.detail)}})

    def require_auth(
        x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_api_key == settings.api_key:
            return
        if authorization == f"Bearer {settings.api_key}":
            return
        raise _api_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthorized",
            "No valid mock video provider API key was provided.",
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/video-generations", dependencies=[Depends(require_auth)])
    async def create_video_generation(
        request_body: VideoGenerationRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, dict[str, object]]:
        video = store.create_video(request_body, settings)
        background_tasks.add_task(_advance_video_job, video.video_id, store, settings, callback_sender)
        return {
            "data": {
                "session_id": video.session_id,
                "status": "generating",
                "video_id": video.video_id,
                "created_at": video.created_at,
            }
        }

    @app.get("/v1/video-generations/{session_id}", dependencies=[Depends(require_auth)])
    async def get_video_generation(session_id: str) -> dict[str, dict[str, object]]:
        video = store.get_video_by_session(session_id)
        if video is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "resource_not_found", "Session not found.")
        return {"data": _video_generation_data(video)}

    @app.get("/v1/videos/{video_id}", dependencies=[Depends(require_auth)])
    async def get_video(video_id: str) -> dict[str, dict[str, object]]:
        video = store.get_video(video_id)
        if video is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "video_not_found", "Video not found.")
        return {"data": _video_data(video)}

    @app.post("/v1/assets", dependencies=[Depends(require_auth)])
    async def upload_asset(file: Annotated[UploadFile, File()]) -> dict[str, dict[str, object]]:
        content = await file.read()
        if len(content) > 32 * 1024 * 1024:
            raise _api_error(
                status.HTTP_400_BAD_REQUEST,
                "invalid_parameter",
                "Asset exceeds the mock 32 MB upload limit.",
                param="file",
            )
        asset = store.create_asset(
            filename=file.filename or "upload.bin",
            mime_type=file.content_type or "application/octet-stream",
            content=content,
        )
        return {
            "data": {
                "asset_id": asset.asset_id,
                "url": f"{settings.public_base_url.rstrip('/')}/mock-assets/{asset.asset_id}",
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
            }
        }

    @app.get("/mock-assets/{asset_id}")
    async def get_mock_asset(asset_id: str) -> Response:
        asset = store.get_asset(asset_id)
        if asset is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "asset_not_found", "Asset not found.")
        return Response(content=asset.content, media_type=asset.mime_type)

    @app.get("/mock-files/{file_name}")
    async def get_mock_file(file_name: str) -> Response:
        video_id = file_name.rsplit(".", 1)[0]
        video = store.get_video(video_id)
        if video is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "video_not_found", "Video not found.")
        if video.status != "completed":
            raise _api_error(
                status.HTTP_409_CONFLICT,
                "resource_not_ready",
                "Mock video is not completed yet.",
            )
        if file_name.endswith(".jpg"):
            return Response(content=b"mock thumbnail", media_type="image/jpeg")
        return Response(content=b"mock video bytes", media_type="video/mp4")

    @app.post("/v1/webhooks/endpoints", dependencies=[Depends(require_auth)])
    async def create_webhook_endpoint(
        request_body: WebhookEndpointRequest,
    ) -> dict[str, dict[str, object]]:
        endpoint = store.create_webhook_endpoint(request_body)
        return {"data": _webhook_endpoint_data(endpoint, include_secret=True)}

    @app.get("/v1/webhooks/endpoints", dependencies=[Depends(require_auth)])
    async def list_webhook_endpoints(
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> dict[str, object]:
        endpoints = store.list_webhook_endpoints()[:limit]
        return {
            "data": [_webhook_endpoint_data(endpoint, include_secret=False) for endpoint in endpoints],
            "next_token": None,
        }

    @app.patch("/v1/webhooks/endpoints/{endpoint_id}", dependencies=[Depends(require_auth)])
    async def update_webhook_endpoint(
        endpoint_id: str,
        request_body: WebhookEndpointPatchRequest,
    ) -> dict[str, dict[str, object]]:
        endpoint = store.update_webhook_endpoint(endpoint_id, request_body)
        if endpoint is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "webhook_not_found", "Webhook not found.")
        return {"data": _webhook_endpoint_data(endpoint, include_secret=False)}

    @app.delete("/v1/webhooks/endpoints/{endpoint_id}", dependencies=[Depends(require_auth)])
    async def delete_webhook_endpoint(endpoint_id: str) -> dict[str, dict[str, object]]:
        deleted = store.delete_webhook_endpoint(endpoint_id)
        if not deleted:
            raise _api_error(status.HTTP_404_NOT_FOUND, "webhook_not_found", "Webhook not found.")
        return {"data": {"deleted": True, "endpoint_id": endpoint_id}}

    @app.post("/v1/webhooks/endpoints/{endpoint_id}/rotate-secret", dependencies=[Depends(require_auth)])
    async def rotate_webhook_secret(endpoint_id: str) -> dict[str, dict[str, object]]:
        endpoint = store.rotate_webhook_secret(endpoint_id)
        if endpoint is None:
            raise _api_error(status.HTTP_404_NOT_FOUND, "webhook_not_found", "Webhook not found.")
        return {"data": _webhook_endpoint_data(endpoint, include_secret=True)}

    @app.get("/v1/webhooks/events", dependencies=[Depends(require_auth)])
    async def list_webhook_events(
        event_type: str | None = None,
        entity_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
    ) -> dict[str, object]:
        deliveries = store.list_deliveries(event_type=event_type, entity_id=entity_id, limit=limit)
        return {
            "data": [_delivery_data(delivery) for delivery in deliveries],
            "next_token": None,
        }

    return app


async def _advance_video_job(
    video_id: str,
    store: MockVideoProviderStore,
    settings: MockVideoProviderSettings,
    callback_sender: CallbackSender,
) -> None:
    video = store.get_video(video_id)
    if video is None:
        return
    if video.delay_seconds:
        await asyncio.sleep(video.delay_seconds)
    video = store.update_video_status(video_id, "processing")
    if video is None:
        return
    if video.delay_seconds:
        await asyncio.sleep(video.delay_seconds)

    if video.outcome == "fail":
        video = store.update_video_status(
            video_id,
            "failed",
            failure_code="mock_render_failed",
            failure_message="Simulated video provider render failure.",
        )
        event_type: WebhookEventType = "video_generation.failed"
    else:
        video = store.update_video_status(video_id, "completed")
        event_type = "video_generation.completed"
    if video is None:
        return
    await _dispatch_webhooks(video, event_type, store, settings, callback_sender)


async def _dispatch_webhooks(
    video: MockVideo,
    event_type: WebhookEventType,
    store: MockVideoProviderStore,
    settings: MockVideoProviderSettings,
    callback_sender: CallbackSender,
) -> None:
    targets: list[tuple[str, str]] = []
    if video.callback_url:
        targets.append((video.callback_url, settings.webhook_secret))
    targets.extend(
        (endpoint.url, endpoint.secret)
        for endpoint in store.matching_webhook_endpoints(event_type, video)
    )

    for target_url, secret in targets:
        event_id = _new_id("evt")
        event_data = _event_data(video)
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "event_data": event_data,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: signature,
            "X-Video-Provider-Signature": signature,
            "X-Video-Provider-Event-Type": event_type,
        }
        result = await callback_sender(
            target_url,
            body,
            headers,
            settings.callback_timeout_seconds,
        )
        store.record_delivery(
            MockWebhookDelivery(
                event_id=event_id,
                event_type=event_type,
                event_data=event_data,
                target_url=target_url,
                status_code=result.status_code,
                ok=result.ok,
                error=result.error,
                created_at=payload["created_at"],
            )
        )


def _video_generation_data(video: MockVideo) -> dict[str, object]:
    data: dict[str, object] = {
        "session_id": video.session_id,
        "status": "generating" if video.status in {"pending", "processing"} else video.status,
        "video_id": video.video_id,
        "created_at": video.created_at,
    }
    if video.status in {"completed", "failed"}:
        data["video"] = _video_data(video)
    return data


def _video_data(video: MockVideo) -> dict[str, object]:
    data: dict[str, object] = {
        "id": video.video_id,
        "session_id": video.session_id,
        "status": video.status,
        "created_at": video.created_at,
        "updated_at": video.updated_at,
    }
    if video.status == "completed":
        data.update(
            {
                "video_url": video.video_url,
                "thumbnail_url": video.thumbnail_url,
                "duration": video.duration,
            }
        )
    if video.status == "failed":
        data.update(
            {
                "failure_code": video.failure_code,
                "failure_message": video.failure_message,
            }
        )
    return data


def _event_data(video: MockVideo) -> dict[str, object]:
    data = _video_data(video)
    data["video_id"] = video.video_id
    if video.callback_id:
        data["callback_id"] = video.callback_id
    return data


def _webhook_endpoint_data(
    endpoint: MockWebhookEndpoint, *, include_secret: bool
) -> dict[str, object]:
    return {
        "endpoint_id": endpoint.endpoint_id,
        "url": endpoint.url,
        "events": endpoint.events,
        "entity_id": endpoint.entity_id,
        "secret": endpoint.secret if include_secret else None,
        "created_at": endpoint.created_at,
        "updated_at": endpoint.updated_at,
    }


def _delivery_data(delivery: MockWebhookDelivery) -> dict[str, object]:
    return {
        "event_id": delivery.event_id,
        "event_type": delivery.event_type,
        "event_data": delivery.event_data,
        "target_url": delivery.target_url,
        "status_code": delivery.status_code,
        "ok": delivery.ok,
        "error": delivery.error,
        "created_at": delivery.created_at,
    }


def _api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    param: str | None = None,
) -> HTTPException:
    body: dict[str, dict[str, str]] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if param is not None:
        body["error"]["param"] = param
    return HTTPException(status_code=status_code, detail=body)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _estimate_duration(prompt: str) -> float:
    word_count = len(prompt.split())
    return round(min(300.0, max(8.0, word_count * 1.2)), 1)


app = create_app()
