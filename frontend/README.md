# frontend/

Next.js (App Router) + CopilotKit seam + AG-UI + Tailwind. The chat
surface, BFF routes, login, interrupt UI, component tests and recorded AG-UI
Playwright flows live here, plus the admin-only `/admin` audit dashboard,
with server pagination, filters and isolated Langfuse trace links.

## Layout

```
frontend/
├── package.json
├── src/
│   ├── app/                    # App Router: (chat)/, admin/, login/
│   ├── components/
│   │   ├── ui/                 # shadcn/ui primitives (generated)
│   │   ├── chat/               # message list, citation chips, confirmation cards
│   │   └── admin/              # audit table, trace viewer links
│   ├── lib/                    # AG-UI client setup, API client, auth helpers
│   └── hooks/
└── tests/                      # vitest + testing-library; playwright e2e
```

## Running standalone

Prerequisite: Node 20+. The frontend needs a reachable backend — either the full composed stack (`make up` from the repo root) or a backend running on the host (see "Rodando só o backend" in the root README).

```bash
npm install
BACKEND_URL=http://localhost:8000 npm run dev
# Windows PowerShell: $env:BACKEND_URL="http://localhost:8000"; npm run dev
```

Open `http://localhost:3000` (demo login: `ana@demo` / `demo123`).

`BACKEND_URL` is server-side only: the browser always calls the same-origin proxy `/api/backend/*`, and the Next.js route handler forwards it to the backend. Defaults to `http://backend:8000` (the compose hostname), so running outside compose requires the override above. No `NEXT_PUBLIC_*` variables are needed.

Useful scripts:

```bash
npm run lint        # eslint
npm run typecheck   # tsc --noEmit
npm run test        # vitest + coverage
npm run test:e2e    # playwright (needs the full stack up)
```

## Rules

1. All agent communication through CopilotKit/AG-UI — no bespoke chat transport.
2. Confirmation UI for critical ops is generative UI driven by agent interrupt state, not hardcoded modals guessed from text.
3. No secrets in the client; the browser talks only to the Next.js BFF/route handlers or the backend with the user's JWT.
4. Components: server components by default; `"use client"` only where interaction requires it.
