# Project 7: Video Provider Mock Service

Project 7 is a local FastAPI mock of an external video-generation provider API.
It is meant for practicing a separate app/backend/database workflow without
spending API credits or depending on a real provider service.

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

## API Surface

The mock implements the main production-shaped endpoints needed for backend
practice:

```text
POST /v1/video-generations
GET  /v1/video-generations/{session_id}
GET  /v1/videos/{video_id}
POST /v1/assets
POST /v1/webhooks/endpoints
GET  /v1/webhooks/endpoints
GET  /v1/webhooks/events
```

`POST /v1/video-generations` returns a `session_id` and `video_id`, then advances
the job in memory from `pending` to `processing` to `completed` or `failed`.

For one-off callback practice, pass a `callback_url`:

```json
{
  "prompt": "Create a 30-second product demo",
  "callback_url": "http://127.0.0.1:8000/webhooks/video-provider",
  "callback_id": "your-local-job-id"
}
```

The webhook payload is signed with HMAC-SHA256 using
`MOCK_VIDEO_PROVIDER_WEBHOOK_SECRET` and sent in the `Signature` header. The mock
also sends `X-Video-Provider-Signature` with the same value for convenience.

Mock-only controls:

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

## Tests

```bash
pytest project7_video_provider_mock/tests -q
```
