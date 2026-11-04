# Real-world benchmark

Scans of four popular self-hosted projects. The goal is to find out how `repo-airgap` behaves on
real code, not on its own fixture. **These are spot-checked results, not a fully labeled
precision/recall study.**

- Date: 2026-11-04, repo-airgap `main` after commit "offline readiness hints + noise filters"
- Machine: Windows 11, Python 3.12, Windows Defender real-time scanning enabled (adds noticeable
  per-file `open()` cost; Linux is usually faster)
- Command: `repo-airgap scan <repo>` with defaults (tests, locale catalogs, and minified bundles excluded)

| Project (shallow clone, HEAD) | Files scanned | Time | Candidates | Offline hints | Report size |
| --- | ---: | ---: | ---: | ---: | ---: |
| [langgenius/dify](https://github.com/langgenius/dify) `7de3c293` | 15,095 | 53 s | 1,274 | 7 | 2.5 MB |
| [lobehub/lobe-chat](https://github.com/lobehub/lobe-chat) `279a65a5` | 23,159 | 69 s | 2,836 | 5 | 6.7 MB |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) `c38885a` | 1,385 | 13 s | 1,044 | 11 | 1.9 MB |
| [oobabooga/text-generation-webui](https://github.com/oobabooga/text-generation-webui) `c93f887` | 144 | 1 s | 151 | 11 | 0.3 MB |

## What the first real-world run exposed (now fixed)

Before this round, the same scan of **dify produced 11,886 candidates and a 358 MB JSON report in
150 s**. Problems found and fixed:

| Problem | Cause | Fix |
| --- | --- | --- |
| 78% of dify candidates came from tests | Test/spec/fixture files scanned like product code | Skipped by default; `--include-tests` to opt in |
| 358 MB report | A vendored, non-`.min` Monaco bundle had 535 KB lines copied into every snippet | Minified-bundle detection and 300-char snippet line cap |
| `developer.mozilla.org` ×718 | URLs inside that bundle | Same as above |
| `localhost`, `127.0.0.1`, `api` (Docker service) reported as egress | No local-host filter | Loopback/private/single-label hosts ignored |
| `www.w3.org` ×395 in open-webui | SVG/XML namespace URIs | Identifier hosts ignored unless the line contains a network call |
| Public "IPs" `1.2.1.0`, `4.3.5.1`, `130.0.0.0`, `3.138.75.75` | Version strings, User-Agent, CIDR lists, SVG path data | Version, CIDR, and SVG-geometry contexts excluded |
| `gradio` missing in text-generation-webui | `requirements.txt` used a direct wheel URL | Wheel URL / `name @ url` / `#egg=` parsing |
| `os.environ["X"] = ...` not recognized as a set switch | Env-assignment regex too narrow | Python dict/`setdefault`/subscript forms recognized |
| Scan 7 s for 144 files | `imported_packages()` ran once per package per line (×36) | Called once per line |

## Confirmed true positives (spot-checked by reading the source)

- **dify**: Google Analytics / GTM / Amplitude hosts and `@amplitude/analytics-browser`, `sentry-sdk`,
  `@sentry/react`; `s.connect(("8.8.8.8", 53))` in the Apollo config helper (a local-IP trick that
  fails in an air gap); `NEXT_TELEMETRY_DISABLED` already set in `docker/envs/core-services/web.defaults`.
- **lobe-chat**: Plausible, PostHog, Microsoft Clarity, GTM hosts; `@vercel/analytics`,
  `@vercel/speed-insights`; Next.js telemetry switch **not** set.
- **open-webui**: correctly reported as already setting `SCARF_NO_ANALYTICS`, `DO_NOT_TRACK`,
  `ANONYMIZED_TELEMETRY` (`Dockerfile`, `.env.example`), `HF_HUB_OFFLINE` (`backend/open_webui/env.py`),
  and `TIKTOKEN_CACHE_DIR`; `TRANSFORMERS_OFFLINE` reported missing.
- **text-generation-webui**: `GRADIO_ANALYTICS_ENABLED` set in `server.py`, `WANDB_MODE=offline` set in
  `training.py`; `from_pretrained` / `hf_hub_download` / tiktoken runtime downloads; Dockerfile fetches.

## Known remaining noise and gaps

- **Frontend calls to the app's own backend** (`fetch(`${WEBUI_BASE_URL}/api/...`)`) are reported as
  `NETWORK_SINK` with destination `dynamic`. They make up most of the ~600–700 sinks per large project.
- **Documentation links** in product code (`docs.dify.ai`, `docs.openwebui.com`, `github.com`) are
  reported as outbound URLs, although most are rendered as links and never fetched by the server.
- Config keys that look like domains (`'webui.url'`) can be reported as bare domains.
- Dependencies inside `node_modules` / site-packages are **not** scanned; framework telemetry is covered
  only through the switch database (`rules/offline_switches.json`).
- No AST: multi-line calls, URLs assembled at runtime, and URLs loaded from config files are missed.

## Reproduce

```bash
git clone --depth 1 https://github.com/open-webui/open-webui
repo-airgap scan open-webui
cat open-webui/.airgap/offline.env
```
