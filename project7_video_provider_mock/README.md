# Project 7: Video Provider Mock Service

Project 7 is a local FastAPI mock of an external video-generation provider API.
It is meant for practicing a separate app/backend/database workflow without
spending API credits or depending on a real provider service.

The mock is local-development infrastructure. Do not expose it on a public
network with the default `dev-key`, and only point callbacks/webhooks at systems
you control.

## Run

```bash
uvicorn project7_video_provider_mock.app.mock_video_provider:app --reload --port 9000
```

Then point your practice backend at:

```bash
VIDEO_PROVIDER_BASE_URL=http://127.0.0.1:9000
VIDEO_PROVIDER_API_KEY=dev-key
VIDEO_PROVIDER_WEBHOOK_SECRET=dev-secret
```

Protected endpoints accept either auth style:

```bash
X-Api-Key: dev-key
Authorization: Bearer dev-key
```

## API Surface

```text
GET    /health
POST   /v1/video-generations
GET    /v1/video-generations/{session_id}
GET    /v1/videos/{video_id}
GET    /mock-files/{video_id}.mp4
GET    /mock-files/{video_id}.jpg
POST   /v1/assets
GET    /mock-assets/{asset_id}
POST   /v1/webhooks/endpoints
GET    /v1/webhooks/endpoints
PATCH  /v1/webhooks/endpoints/{endpoint_id}
DELETE /v1/webhooks/endpoints/{endpoint_id}
POST   /v1/webhooks/endpoints/{endpoint_id}/rotate-secret
GET    /v1/webhooks/events
```

`POST /v1/video-generations` returns a `session_id` and `video_id`, then advances
the in-memory job from `pending` to `processing` to `completed` or `failed`.

## Create And Poll A Job

```bash
curl -s http://127.0.0.1:9000/v1/video-generations \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: dev-key' \
  -d '{
    "prompt": "Create a 30-second product demo",
    "callback_id": "local-job-123",
    "mock_delay_seconds": 0
  }'
```

Response shape:

```json
{
  "data": {
    "session_id": "sess_...",
    "status": "generating",
    "video_id": "vid_...",
    "created_at": 1710000000
  }
}
```

Poll either the generation session or the video:

```bash
curl -s http://127.0.0.1:9000/v1/video-generations/sess_... -H 'X-Api-Key: dev-key'
curl -s http://127.0.0.1:9000/v1/videos/vid_... -H 'X-Api-Key: dev-key'
```

Completed videos include mock media URLs:

```json
{
  "data": {
    "id": "vid_...",
    "session_id": "sess_...",
    "status": "completed",
    "video_url": "http://127.0.0.1:9000/mock-files/vid_....mp4",
    "thumbnail_url": "http://127.0.0.1:9000/mock-files/vid_....jpg",
    "duration": 8.0
  }
}
```

Mock media URLs return `409 resource_not_ready` until the video is completed.

## Assets

Upload reference files to test provider-style asset flows:

```bash
curl -s http://127.0.0.1:9000/v1/assets \
  -H 'X-Api-Key: dev-key' \
  -F 'file=@./reference.png'
```

The mock stores assets in memory and returns a retrievable `/mock-assets/{asset_id}`
URL. Uploads are capped at 32 MB.

## Webhooks

For one-off callback practice, pass `callback_url` when creating a generation:

```json
{
  "prompt": "Create a 30-second product demo",
  "callback_url": "http://127.0.0.1:8000/webhooks/video-provider",
  "callback_id": "your-local-job-id"
}
```

For provider-style webhook configuration, register an endpoint:

```bash
curl -s http://127.0.0.1:9000/v1/webhooks/endpoints \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: dev-key' \
  -d '{
    "url": "http://127.0.0.1:8000/webhooks/video-provider",
    "events": ["video_generation.completed", "video_generation.failed"]
  }'
```

The create and rotate-secret responses include the endpoint secret once. List and
patch responses return `secret: null`, matching provider APIs that do not reveal
stored webhook secrets after creation.

Webhook payloads are signed with HMAC-SHA256. One-off `callback_url` deliveries
use `MOCK_VIDEO_PROVIDER_WEBHOOK_SECRET`; registered endpoints use their own
`whsec_...` secret. The signature is sent in both `Signature` and
`X-Video-Provider-Signature`.

```python
import hashlib
import hmac


def verify_video_provider_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Use `/v1/webhooks/events?event_type=video_generation.completed` or
`/v1/webhooks/events?entity_id=vid_...` to inspect recorded deliveries.

## Mock Controls

- `mock_outcome: "fail"` simulates a failed render.
- `mock_delay_seconds: 0` makes a specific job complete immediately.
- Including `mock:fail` in the prompt also simulates failure.

Environment variables:

```bash
MOCK_VIDEO_PROVIDER_API_KEY=dev-key
MOCK_VIDEO_PROVIDER_WEBHOOK_SECRET=dev-secret
MOCK_VIDEO_PROVIDER_PUBLIC_BASE_URL=http://127.0.0.1:9000
MOCK_VIDEO_PROVIDER_STEP_DELAY_SECONDS=2.0
MOCK_VIDEO_PROVIDER_CALLBACK_TIMEOUT_SECONDS=5.0
```

## State Model

State is process-local and in memory. Jobs, assets, webhook endpoints, and
delivery history reset when the server restarts. Event listing returns
`next_token: null`; there is no durable persistence, pagination cursor, retry
queue, or dead-letter store.

## Tests

```bash
python3 -m pytest project7_video_provider_mock/tests -q
```
