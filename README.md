# One AI

**Universal AI Orchestration Platform**

> *One Interface. Multiple AI Capabilities. One Coordinated Workflow.*

Describe a task in plain language. One AI turns it into a validated task graph, writes a tailored prompt for every step,
routes each step to a provider you have configured (OpenAI, Anthropic Claude, Google Gemini, or a free built-in **demo**
provider), passes results between steps, checks them against acceptance criteria, retries within strict limits, asks for your
approval on consequential steps, and stops at the spending/token/call limits you set.

> **Status: working prototype, honestly scoped.** See [What is and isn't verified](#what-is-and-isnt-verified) and [Limitations](#limitations).

## Quick start (no API keys, no cost)

Requirements: Python 3.11+ (developed on 3.12), Node 20+.

```bash
# 1. backend
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # optional; all keys can stay blank
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. frontend (second terminal)
cd frontend
npm install
npm run dev                                               # http://localhost:5173 (proxies /api to :8000)
```

Open http://localhost:5173, leave **Demo** mode selected, click *Use an example* → *Create plan* → *Approve plan* → *Run workflow*.
Everything produced in demo mode is labelled **SIMULATED**.

Single-process alternative: `cd frontend && npm run build`, then run the backend; it serves `frontend/dist` automatically.

### Tests
```bash
cd backend && pytest            # or: python -m unittest discover -s tests -t .
cd frontend && npm test         # vitest (graph/layout helpers)
```
Backend tests need no network and no API keys (they use a scripted mock provider and fake SDK clients).

## Connecting real providers
All three use the **official SDKs and APIs**. A ChatGPT / Claude / Gemini *chat subscription does not include API access*; you need
an API key from each provider's developer console, and API usage may be billed (or limited by a free tier) by that provider.

| Provider | Get a key | Env var | Notes |
|---|---|---|---|
| OpenAI | platform.openai.com → API keys | `OPENAI_API_KEY` | Text via Responses API; images via Images API if an image model is set |
| Anthropic | console.anthropic.com → API keys | `ANTHROPIC_API_KEY` | Text only. **No image generation**: image tasks route elsewhere or fall back to manual handoff |
| Google Gemini | aistudio.google.com/apikey | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Text; image generation only if you set an image-capable model |

Two ways to supply keys:
1. **Environment** (recommended): put them in `backend/.env` or export them before starting the backend.
2. **Session key** in *Providers → API key*: sent once from your browser to *your own* backend and held **in process memory only**. It is
   not written to disk, the database, exports or logs, **not encrypted**, and lost on restart. Use only over localhost or HTTPS.

Then in *Providers* click **Test connection** (lists models; no generation, normally not billed) and, in the composer, choose **Integration** mode.
Model names in the code are *suggestions*: verify current names in each provider's docs and change them in *Providers* or via `*_MODEL` env vars.

## Cost and limits
- Per project: **max USD**, **max tokens**, **max calls**, max tasks, max retries per task, max run duration, max parallel tasks. Values are clamped to hard ceilings.
- **Hard stop**: before every call the worst case (estimated input + max output tokens) is checked; if it would break a limit the call is *not made*, the task fails with `budget`, and remaining tasks are blocked.
- **No prices are bundled** (they change; this build could not verify them). Copy `backend/config/pricing.example.json` → `pricing.json`, fill in current USD prices, set `PRICING_FILE`.
  Without a price: estimates show **unknown**, and if a USD limit is set the call is **refused** (fail-closed). Token/call limits always apply.
- Token estimates use a ~4 chars/token heuristic; provider-reported usage is recorded after each call. Retries and the optional LLM judge also consume budget.
- Provider **quota/remaining credit is not exposed by these public APIs**, so the UI says so rather than guessing. It shows last-request status and errors (auth, rate limit, quota).
- Nothing here promises free API access. Provider free tiers (if any) have their own limits and terms.

## How it works
```
request ─► Planner ─(JSON, schema-validated, 1 repair round)─► Task graph (cycle/dependency checks)
              │                                                   │
         Prompt builder (per task) ◄── Router (capabilities, prefs, price, availability)
                                                                  ▼
 Executor: ready tasks run in parallel → build prompt + upstream outputs → budget check → provider call
        → postprocess (files/pptx/docx/image) → verify+repair (code) → evaluate (structural [+ LLM judge])
        → retry w/ feedback (bounded) / fail over to another provider / approval / manual handoff → artifacts
```
- **Task kinds** (`backend/app/core/capabilities.py`) declare required capabilities *and* which artifact types they may promise.
  A script task can never claim a video file; every task lists only artifacts it actually produced.
- **Statuses:** planned, waiting, running, needs approval, completed, failed, blocked. Runs are **resumable**: approvals and manual handoffs pause the run, not the process.
- **Manual handoff:** if no eligible provider supports a task (e.g. video clips, voiceover; no built-in adapter implements them), you get the full prompt, links to the providers' own sites, and a box to paste the result back. Nothing is scraped or auto-submitted.
- **Coding workflow:** spec → files written to a controlled workspace (path-safe, change-tracked) → optional build/test → compiler output sent back to the provider for ≤2 repair rounds → zip download and sandboxed static preview.
- **Provider adapters** (`backend/app/providers/`): implement `ProviderAdapter` (`_generate_text`, optional `_generate_image`, `_list_models`) and register it in `registry.py`; the engine needs no changes.

## What works in demo mode vs needs integrations
| Workflow | Demo (no keys) | Integration |
|---|---|---|
| Planning, task graph, prompts, routing preview, approvals, retries, history, export | ✅ (heuristic planner, simulated content) | ✅ real planner via your provider |
| Writing, research notes, social, specs, reviews, video script/storyboard/assembly *plans* | ✅ simulated text | ✅ real text |
| Website / JS project generation, zip, static preview | ✅ simulated placeholder site | ✅ real files; build/test only if `ENABLE_COMMAND_EXECUTION=true` |
| Presentation (.pptx) and document (.docx) files | ✅ real files, simulated content | ✅ real files, real content |
| Image generation | ✅ simulated SVG placeholder | OpenAI, or Gemini with an image model set; not Anthropic |
| Video footage, voiceover, final video render | ❌ manual handoff | ❌ manual handoff (not implemented in any adapter) |
| Web-verified research with citations | ❌ | ❌ (no search tool integrated: research is labelled *unverified*) |

## Security and privacy
- Keys stay on the backend; the frontend never receives or stores them. `.env`, `data/`, `pricing.json` are git-ignored; `.env.example` has placeholders only.
- Logging passes through a redaction filter (registered keys + common key patterns). The app does not log prompts or outputs.
- Workspace writes reject absolute paths, `..`, symlinks, forbidden names (`.env`, `.git`, `node_modules`), unknown extensions and oversize files; the whole batch is validated before anything is written. Project/artifact ids are validated before touching the filesystem.
- Generated-code execution is **off by default**. When on, only an allow-list (`npm install --ignore-scripts`, `npm run build|test|lint`, `python3 -m pytest|compileall`) runs: no shell, scrubbed environment (no secrets), timeouts and output caps. **This is not a sandbox**: build scripts in generated code still run with your user's privileges. Enable it only inside a container/VM.
- Preview is served with `Content-Security-Policy: sandbox …; connect-src 'none'` and rendered in a sandboxed iframe.
- Uploads: small UTF-8 text files only, extension allow-list, base64 JSON body. Request size limit; strict CORS list.
- **No user accounts.** It is a single-user local tool bound to `127.0.0.1`. If you expose it, set `APP_ACCESS_TOKEN` *and* put it behind HTTPS; this is minimal protection, not multi-user auth.
- Projects, prompts and results are stored **unencrypted** in `DATA_DIR` (SQLite + files). Your content is sent to whichever provider handles each task and is governed by that provider's data policies.

## Deployment
- **Local (recommended):** as above. Cost: $0 apart from any provider API usage you opt into.
- **Free-tier hosting** (Render/Fly/Railway/etc.) *may* work for a demo but free tiers change, sleep, and often have **ephemeral disks** (SQLite data lost on restart) and limited CPU. Check current terms; nothing here depends on a paid service. Build the frontend, run `uvicorn app.main:app --host 0.0.0.0` (behind the host's HTTPS), set `APP_ACCESS_TOKEN`, keep `ENABLE_COMMAND_EXECUTION=false`, and store keys as the host's secrets.
- Persistence is behind `ProjectStore` (`backend/app/storage.py`) so another backend can be swapped in.

## What is and isn't verified
Built and checked in an offline sandbox (Python 3.12, Node 22; no network, so no `pip install` / `npm install`).
- ✅ **Executed and passing:** the orchestration core (graph/plan validation, budget, router, planner repair, executor, retries, failover, approvals, manual handoff, code workflow with a fake build runner, workspace safety, exporters, demo end-to-end, persistence) and provider adapters against **fake SDK clients**: 88 collected: 81 passed, 7 skipped (5 HTTP tests need FastAPI; 2 need the Google SDK).
- ⚠️ **Written but NOT executed here:** the FastAPI layer (`api.py`, `tests/test_api.py`), the whole React/TypeScript frontend (only a bracket-balance check and the pure `graph.ts` logic were run), and all **live calls to OpenAI/Anthropic/Gemini** (SDK call shapes follow each SDK's documented usage but were never exercised against the real services; the Gemini adapter's SDK path is untested here).
- Dependency versions in `requirements.txt` / `package.json` are version *floors/ranges*, not resolved pins: run `pip freeze`/`npm install` once and commit the lockfiles.
- Build/test execution of generated code (`ENABLE_COMMAND_EXECUTION=true`) was tested only through a fake runner.

## Limitations
- Demo planner is keyword-based; demo content is placeholder text.
- No video generation, voiceover, or video assembly; no web search/citation verification; no vision/file inputs beyond UTF-8 text attachments.
- Prompts are not streamed; a run cannot interrupt an in-flight provider request (cancel takes effect between steps; request timeouts are passed to the SDKs).
- The LLM judge is an extra model's opinion, not proof of quality; structural checks are shallow.
- Token estimates are heuristic; USD figures exist only where you provide prices. Concurrent tasks can overshoot a limit by at most the already-reserved in-flight calls.
- Single process, single user, in-memory run control (a server restart marks interrupted runs resumable, but in-flight work is lost).
- JS/TS is the only language with automatic build detection.

## Next most valuable improvements
1. Run the frontend/API/live-provider paths end to end, commit lockfiles, add Playwright smoke tests.
2. Container-based sandbox for generated-code execution + live preview of Vite projects.
3. Provider web-search tools for verifiable research citations; image/video/TTS adapters where official APIs allow.
4. Streaming progress, per-task cost display from real usage, authenticated multi-user mode with encrypted storage.

## Layout
```
backend/app/{config,logging_utils,storage,service,api,main}.py
backend/app/core/      capabilities, schemas, graph, pricing, budget, router, prompts, planner, executor, evaluator, caller, workspace, exporters
backend/app/providers/ base, openai_adapter, anthropic_adapter, gemini_adapter, demo, registry
backend/tests/         core, providers, orchestration (mock-based integration), api
frontend/src/          App, api, types, ui, components/*, lib/graph(.test)
```
