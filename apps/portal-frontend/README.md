# DarkForge Portal Frontend

Static single-page dashboard. No build step required — plain HTML + htmx + Alpine.js loaded from CDN.

## Serving

This directory (`dist/`) is served automatically by the portal API at http://localhost:8090.

Run the API:
```bash
cd ../portal-api
uv run uvicorn app.main:app --reload --port 8090
```

## Files

- `dist/index.html` — main page; polls `/api/sandboxes` and `/api/cluster/summary` every 5 s via htmx
- `dist/style.css` — dark-mode styles, monospace IDs/nodes, color-coded pool badges
