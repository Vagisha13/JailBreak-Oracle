# Jailbreak Oracle — Frontend

Next.js 16 / React 19 dashboard for the Jailbreak Oracle red-teaming platform.
Authenticates against the FastAPI backend, starts and monitors campaigns, and
renders findings, mutation trees, budgets, and reports.

## Stack

- Next.js 16 (App Router), React 19, TypeScript
- Tailwind CSS v4
- Axios (`src/lib/api.ts`) with JWT bearer attach and 401 logout/redirect
- Vitest smoke tests for the API client

## Requirements

- Node `>=20` (the Dockerfile builds on `node:20-alpine`)

## Development

```bash
npm install
npm run dev        # http://localhost:3000
```

The app calls `NEXT_PUBLIC_API_URL || http://localhost:8000/api/v1`. Set
`NEXT_PUBLIC_API_URL` to point at a deployed backend.

```bash
npm run lint       # eslint
npx tsc --noEmit   # typecheck
npm test           # vitest (src/lib/api.test.ts)
npm run build      # production build
```

## Security

- `next.config.ts` disables the `X-Powered-By` header and applies OWASP-style
  response headers to every route (HSTS, `X-Frame-Options: SAMEORIGIN`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  `Permissions-Policy`).
- Auth tokens live in `localStorage` (`oracle_token`/`oracle_user`); the axios
  response interceptor clears them and redirects to `/login` on any 401.

## Layout

```
src/lib/api.ts        axios client + interceptors (unit-tested)
src/lib/types.ts      shared API types
src/app/...           App Router pages (/, /login, /campaigns, /reports)
src/components/...    UI components
```