# DarkForge Portal Frontend

Single-page command center for the DarkForge dev portal. No build step — plain HTML + CSS + ES5-flavored JS with Alpine.js loaded from CDN.

## Serving

This directory (`dist/`) is served automatically by the portal API at <http://localhost:8090>.

Run the API:
```bash
cd ../portal-api
uv run uvicorn app.main:app --reload --port 8090
```

See [`apps/portal-api/README.md`](../portal-api/README.md) for the port-forward + API-key prerequisites.

## Layout — 6 cards

1. **Cluster lifecycle** — power button driving `POST /api/cluster/{start,stop}`, polls `/api/cluster/state` every 3 s.
2. **Run Swarm** — form (N, model, image) kicks off `examples/hypothesis_swarm.py`; live leaderboard via SSE on `/api/swarm/runs/{id}/events`.
3. **Create Sandbox** — form proxying `POST /api/sandboxes` to the control plane.
4. **Kimi Chat** — Kimi-K2.6 by default (auto-falls-back to K2.5). Detects ```python``` code blocks and adds a "▶ Run in sandbox" button that calls `POST /api/sandbox/exec`. Matplotlib output renders inline as a base64 `<img>`.
5. **Observability** — Pool CR gauge (`available / poolMax`) + recent-events feed; both poll every 3 s.
6. **Sandboxes** — live table with delete buttons.

## Files

- `dist/index.html` — single page; `<body x-data="root()">` with one Alpine factory per card.
- `dist/app.js` — Alpine factories + fetch/SSE helpers. Pure DOM, no `x-html`, no innerHTML.
- `dist/style.css` — dark-mode chrome, CSS-grid 12-col card layout, conic-gradient pool gauge.

## Identity banner

The topbar shows a `⚠ DEV MODE` chip plus the resolved `az` user and current `kubectl` context. This is loud on purpose — see [`docs/PORTAL-AUTH.md`](../../docs/PORTAL-AUTH.md) for the prod migration path.
