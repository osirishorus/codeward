# Codeward demo video

A 31-second [Remotion](https://www.remotion.dev) composition showing Codeward
running against the real [`fastapi/fastapi`](https://github.com/fastapi/fastapi)
codebase. Output: `codeward-demo.mp4` (1920×1080, 30fps, ~2.7 MB).

Scenes:

1. **Title** (3s) — Codeward · symbol-level intelligence for AI coding agents
2. **`codeward map`** (5s) — 1,129 files / 598 tests indexed in one call
3. **`codeward routes`** (6s) — framework-aware URL → handler mapping
4. **`codeward preflight` auto-injection** (8s) — the headline: blast-radius +
   dependents + tests pushed in before `apply_patch` runs
5. **`codeward symbol APIRoute`** (6s) — definition + ranked callers
6. **Install** (3s) — `pipx install codeward`

## Re-render

```bash
npm install
npx remotion render CodewardDemo codeward-demo.mp4
```

For a live preview while editing scenes:

```bash
npx remotion studio
```

## Re-capture against a fresh FastAPI

The captured terminal outputs live in `src/captures.ts`. To refresh them:

```bash
git clone --depth 1 https://github.com/fastapi/fastapi.git /tmp/demo-fastapi
cd /tmp/demo-fastapi
codeward map | head -25
codeward routes --filter /items --method GET docs_src/security
codeward preflight fastapi/routing.py
codeward symbol APIRoute
```

Paste the relevant slices into `src/captures.ts` and re-render.

## Structure

```
demo/
├── codeward-demo.mp4        # rendered output (committed for reviewers)
├── src/
│   ├── Root.tsx             # 1920×1080, 30fps composition entry
│   ├── Composition.tsx      # timeline (TIMELINE array)
│   ├── scenes.tsx           # all 6 scenes + design tokens + helpers
│   └── captures.ts          # real codeward output captured from FastAPI
└── package.json
```

Animations use `useCurrentFrame()` + `interpolate()` per Remotion best practices
— no CSS transitions, no Tailwind animation classes (both forbidden because
they don't render correctly frame-by-frame).
