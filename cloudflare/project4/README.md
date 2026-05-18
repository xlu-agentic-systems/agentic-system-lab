# Project 4 Cloudflare Worker

Cloudflare deployment target for `project4_agentic_project_copilot`.

This keeps the local FastAPI app unchanged and deploys a Worker-native API/UI
that uses:

- Cloudflare Workers on `workers.dev`
- D1 for tasks, notes, sessions, uploaded document chunks, and traces
- OpenAI via `OPENAI_API_KEY` stored as a Cloudflare Secret
- Cloudflare Access for passwordless email-gated protection
- A 3 MB upload cap for text-like files

## First Deploy

```bash
cd cloudflare/project4
npm install
npx wrangler login
npm run d1:create
```

Copy the generated D1 `database_id` into `wrangler.toml`, replacing
`REPLACE_WITH_D1_DATABASE_ID`.

Then:

```bash
npm run d1:migrate:remote
npm run secret:openai
npm run deploy
```

Do not commit `.dev.vars` or any API keys.

## Protect With Cloudflare Access

After the first deploy:

1. Open Cloudflare Dashboard > Workers & Pages > `agentic-project-copilot`.
2. Go to Settings > Domains & Routes.
3. For the `workers.dev` route, choose Enable Cloudflare Access.
4. Configure the Access policy to allow only `zgz2002xl@gmail.com`.

The Worker also checks the `Cf-Access-Authenticated-User-Email` header when
Cloudflare Access is enabled, but the real enforcement should be done by
Cloudflare Access at the route.

## Local Dev

Create `cloudflare/project4/.dev.vars`:

```text
OPENAI_API_KEY=...
SKIP_ACCESS_CHECK=true
```

Then:

```bash
npm run d1:migrate:local
npm run dev
```
