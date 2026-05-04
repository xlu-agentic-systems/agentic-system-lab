import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from project7_video_provider_mock.app.mock_video_provider import (
    CallbackResult,
    MockVideoProviderStore,
    MockVideoProviderSettings,
    SIGNATURE_HEADER,
    VideoGenerationRequest,
    create_app,
)


AUTH_HEADERS = {"X-Api-Key": "dev-key"}


def make_client(callback_sender=None) -> TestClient:
    settings = MockVideoProviderSettings(
        api_key="dev-key",
        webhook_secret="dev-secret",
        public_base_url="http://mock-video-provider.test",
        step_delay_seconds=0,
    )
    if callback_sender is None:
        app = create_app(settings=settings)
    else:
        app = create_app(settings=settings, callback_sender=callback_sender)
    return TestClient(app)


def make_client_with_store() -> tuple[TestClient, MockVideoProviderStore]:
    settings = MockVideoProviderSettings(
        api_key="dev-key",
        webhook_secret="dev-secret",
        public_base_url="http://mock-video-provider.test",
        step_delay_seconds=0,
    )
    store = MockVideoProviderStore()
    app = create_app(settings=settings, store=store)
    return TestClient(app), store


def test_mock_video_generation_job_completes_and_exposes_video_url() -> None:
    client = make_client()

    create_response = client.post(
        "/v1/video-generations",
        headers=AUTH_HEADERS,
        json={"prompt": "A presenter explaining a product launch in 30 seconds"},
    )

    assert create_response.status_code == 200
    created = create_response.json()["data"]
    assert created["status"] == "generating"

    video_response = client.get(f"/v1/videos/{created['video_id']}", headers=AUTH_HEADERS)

    assert video_response.status_code == 200
    video = video_response.json()["data"]
    assert video["status"] == "completed"
    assert video["video_url"] == f"http://mock-video-provider.test/mock-files/{created['video_id']}.mp4"

    file_response = client.get(f"/mock-files/{created['video_id']}.mp4")
    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "video/mp4"


def test_mock_video_generation_can_fail_rendering() -> None:
    client = make_client()

    create_response = client.post(
        "/v1/video-generations",
        headers=AUTH_HEADERS,
        json={"prompt": "Practice failure handling", "mock_outcome": "fail"},
    )

    assert create_response.status_code == 200
    video_id = create_response.json()["data"]["video_id"]
    video_response = client.get(f"/v1/videos/{video_id}", headers=AUTH_HEADERS)
    video = video_response.json()["data"]
    assert video["status"] == "failed"
    assert video["failure_code"] == "mock_render_failed"
    assert "Simulated" in video["failure_message"]


def test_mock_callback_url_receives_signed_webhook_payload() -> None:
    captured = []

    async def callback_sender(url, body, headers, timeout_seconds):
        captured.append((url, body, headers, timeout_seconds))
        return CallbackResult(status_code=204, ok=True)

    client = make_client(callback_sender=callback_sender)

    response = client.post(
        "/v1/video-generations",
        headers=AUTH_HEADERS,
        json={
            "prompt": "Create a webhook practice video",
            "callback_url": "https://main-app.test/webhooks/video-provider",
            "callback_id": "local-job-123",
        },
    )

    assert response.status_code == 200
    assert len(captured) == 1
    url, body, headers, timeout_seconds = captured[0]
    assert url == "https://main-app.test/webhooks/video-provider"
    assert timeout_seconds == 5.0
    assert headers["X-Video-Provider-Event-Type"] == "video_generation.completed"
    expected_signature = hmac.new(b"dev-secret", body, hashlib.sha256).hexdigest()
    assert headers[SIGNATURE_HEADER] == expected_signature

    payload = json.loads(body)
    assert payload["event_type"] == "video_generation.completed"
    assert payload["event_data"]["status"] == "completed"
    assert payload["event_data"]["callback_id"] == "local-job-123"

    events_response = client.get("/v1/webhooks/events", headers=AUTH_HEADERS)
    events = events_response.json()["data"]
    assert events[0]["ok"] is True
    assert events[0]["target_url"] == "https://main-app.test/webhooks/video-provider"


def test_registered_webhook_endpoint_gets_secret_and_receives_matching_events() -> None:
    captured = []

    async def callback_sender(url, body, headers, timeout_seconds):
        captured.append((url, body, headers, timeout_seconds))
        return CallbackResult(status_code=200, ok=True)

    client = make_client(callback_sender=callback_sender)

    endpoint_response = client.post(
        "/v1/webhooks/endpoints",
        headers=AUTH_HEADERS,
        json={
            "url": "https://main-app.test/webhooks/registered-video-provider",
            "events": ["video_generation.completed"],
        },
    )
    endpoint = endpoint_response.json()["data"]
    assert endpoint["secret"].startswith("whsec_")

    list_response = client.get("/v1/webhooks/endpoints", headers=AUTH_HEADERS)
    listed_endpoint = list_response.json()["data"][0]
    assert listed_endpoint["secret"] is None

    create_response = client.post(
        "/v1/video-generations",
        headers=AUTH_HEADERS,
        json={"prompt": "Trigger the registered webhook endpoint"},
    )

    assert create_response.status_code == 200
    assert len(captured) == 1
    url, body, headers, _timeout_seconds = captured[0]
    assert url == "https://main-app.test/webhooks/registered-video-provider"
    expected_signature = hmac.new(
        endpoint["secret"].encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    assert headers[SIGNATURE_HEADER] == expected_signature


def test_upload_asset_returns_retrievable_mock_asset() -> None:
    client = make_client()

    response = client.post(
        "/v1/assets",
        headers=AUTH_HEADERS,
        files={"file": ("reference.txt", b"asset bytes", "text/plain")},
    )

    assert response.status_code == 200
    asset = response.json()["data"]
    assert asset["mime_type"] == "text/plain"
    assert asset["size_bytes"] == len(b"asset bytes")

    asset_response = client.get(f"/mock-assets/{asset['asset_id']}")
    assert asset_response.status_code == 200
    assert asset_response.content == b"asset bytes"
    assert asset_response.headers["content-type"] == "text/plain; charset=utf-8"


def test_webhook_endpoint_can_be_patched_rotated_and_deleted() -> None:
    client = make_client()

    create_response = client.post(
        "/v1/webhooks/endpoints",
        headers=AUTH_HEADERS,
        json={"url": "https://main-app.test/webhooks/a"},
    )
    endpoint = create_response.json()["data"]

    patch_response = client.patch(
        f"/v1/webhooks/endpoints/{endpoint['endpoint_id']}",
        headers=AUTH_HEADERS,
        json={
            "url": "https://main-app.test/webhooks/b",
            "events": ["video_generation.failed"],
            "entity_id": "vid_123",
        },
    )
    patched = patch_response.json()["data"]
    assert patched["url"] == "https://main-app.test/webhooks/b"
    assert patched["events"] == ["video_generation.failed"]
    assert patched["entity_id"] == "vid_123"
    assert patched["secret"] is None

    rotate_response = client.post(
        f"/v1/webhooks/endpoints/{endpoint['endpoint_id']}/rotate-secret",
        headers=AUTH_HEADERS,
    )
    rotated = rotate_response.json()["data"]
    assert rotated["secret"].startswith("whsec_")
    assert rotated["secret"] != endpoint["secret"]

    delete_response = client.delete(
        f"/v1/webhooks/endpoints/{endpoint['endpoint_id']}",
        headers=AUTH_HEADERS,
    )
    assert delete_response.json()["data"] == {
        "deleted": True,
        "endpoint_id": endpoint["endpoint_id"],
    }

    list_response = client.get("/v1/webhooks/endpoints", headers=AUTH_HEADERS)
    assert list_response.json()["data"] == []


def test_webhook_events_can_be_filtered_and_bearer_auth_works() -> None:
    async def callback_sender(url, body, headers, timeout_seconds):
        return CallbackResult(status_code=200, ok=True)

    client = make_client(callback_sender=callback_sender)
    bearer_headers = {"Authorization": "Bearer dev-key"}

    success_response = client.post(
        "/v1/video-generations",
        headers=bearer_headers,
        json={
            "prompt": "Create a successful webhook event",
            "callback_url": "https://main-app.test/webhooks/video-provider",
        },
    )
    failed_response = client.post(
        "/v1/video-generations",
        headers=bearer_headers,
        json={
            "prompt": "Create a failed webhook event",
            "callback_url": "https://main-app.test/webhooks/video-provider",
            "mock_outcome": "fail",
        },
    )

    assert success_response.status_code == 200
    assert failed_response.status_code == 200
    success_video_id = success_response.json()["data"]["video_id"]

    completed_events_response = client.get(
        "/v1/webhooks/events?event_type=video_generation.completed",
        headers=bearer_headers,
    )
    completed_events = completed_events_response.json()["data"]
    assert len(completed_events) == 1
    assert completed_events[0]["event_type"] == "video_generation.completed"

    video_events_response = client.get(
        f"/v1/webhooks/events?entity_id={success_video_id}",
        headers=bearer_headers,
    )
    video_events = video_events_response.json()["data"]
    assert len(video_events) == 1
    assert video_events[0]["event_data"]["video_id"] == success_video_id


def test_mock_file_returns_conflict_before_video_completes() -> None:
    client, store = make_client_with_store()
    video = store.create_video(
        VideoGenerationRequest(prompt="Create a pending video"),
        client.app.state.mock_video_provider_settings,
    )

    response = client.get(f"/mock-files/{video.video_id}.mp4")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "resource_not_ready"


def test_mock_api_requires_api_key() -> None:
    client = make_client()

    response = client.get("/v1/videos/vid_missing")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
