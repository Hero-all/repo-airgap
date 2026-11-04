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
  fails in an air gap); `NEXT_TELEMETRY_DISABLED=1` set in `web/Dockerfile` and a CI workflow (its
  entry in `docker/envs/core-services/web.defaults` is interpolated, so round 3 reports it as
  evidence without a literal value).
- **lobe-chat**: Plausible, PostHog, Microsoft Clarity, GTM hosts; `@vercel/analytics`,
  `@vercel/speed-insights`; Next.js telemetry switch **not** set.
- **open-webui**: correctly reported as already setting `SCARF_NO_ANALYTICS`, `DO_NOT_TRACK`,
  `ANONYMIZED_TELEMETRY` (`Dockerfile`; its `.env.example` entry is no longer counted as
  configuration since round 3), `HF_HUB_OFFLINE` (`backend/open_webui/env.py`),
  and `TIKTOKEN_CACHE_DIR`; `TRANSFORMERS_OFFLINE` reported missing.
- **text-generation-webui**: `GRADIO_ANALYTICS_ENABLED` set in `server.py`, `WANDB_MODE=offline` set in
  `training.py`; `from_pretrained` / `hf_hub_download` / tiktoken runtime downloads; Dockerfile fetches.

## Known remaining noise and gaps

- ~~Frontend calls to the app's own backend reported as `dynamic`~~ — addressed in round 2 below
  (`self` / `config:<NAME>`); calls through plain variables (`fetch(url)`) are still `dynamic`.
- ~~Documentation links reported as outbound URLs~~ — tagged `reference_link` in round 2 when the line
  has no call or fetching attribute; links built elsewhere are still reported normally.
- Config keys that look like domains (`'webui.url'`) can be reported as bare domains.
- Dependencies inside `node_modules` / site-packages are **not** scanned; framework telemetry is covered
  only through the switch database (`rules/offline_switches.json`).
- No AST. Round 3 added per-file tracking of import aliases, client instances, multi-line call
  arguments, same-file URL constants and config references; values that cross files or function
  parameters are still missed.

## Round 2: sink precision and grouping (branch `feat/reduce-review-noise`)

Same repositories and commits, scanned with `main` (`e3e2ad6`) and with the branch. "HIGH" is the
number of candidates a reviewer is asked to look at first.

| Project | Candidates (before → after) | HIGH (before → after) | Review items (before → after) |
| --- | ---: | ---: | ---: |
| dify | 1,274 → 699 | 1,062 → **430** (−60%) | 178 → 158 |
| lobe-chat | 2,836 → 2,570 | 2,135 → **1,693** (−21%) | 523 → 503 |
| open-webui | 1,044 → 1,020 | 936 → **308** (−67%) | 118 → 113 |
| text-generation-webui | 151 → 145 | 40 → **33** | 10 → 10 |

What changed:

- **Sink precision.** 577 of dify's 725 sinks and 234 of lobe-chat's sinks were not calls:
  `httpx.Timeout`/`httpx.Response` type hints, `except httpx.RequestError`, Drizzle columns such as
  `scmChangeRequests.topicId`, and local variables named `requests`. A sample of the removed lines
  was read by hand; one real call (`this.http.request({...})` in dify's Node SDK) had been dropped
  and was restored with a regression test.
- **Configured backends.** open-webui's 499 frontend calls like `` fetch(`${WEBUI_API_BASE_URL}/...`) ``
  are now `config:WEBUI_API_BASE_URL` (MEDIUM) and grouped into 11 review items, one per base URL.
- **Same-origin calls** (`fetch('/api/...')`) are `self` (LOW), one review item.
- **Reference links** (115–172 per large project) are LOW and one review item.

Still not solved: lobe-chat has ~470 distinct literal hosts (model-provider endpoints, icons, CDNs).
Each is a separate review item, so its review list is still long.

## Round 3: switch status and common missed calls (after external review of v0.3.0)

Same repositories and commits; v0.3.0 (`40c15b9`) versus this round, run back to back on the same
machine with nothing else running. Times are much higher than in the first table because Windows
Defender was scanning the clones this time; compare the two columns with each other only.

| Project | Time (s) | Network sinks | HIGH sinks | Sinks with a resolved host or `config:` | Switch statuses |
| --- | ---: | ---: | ---: | ---: | --- |
| text-generation-webui | 0.9 → 1.2 | 12 → 22 | 12 → 22 | 0 → 1 | 2 `present` → 2 `configured` |
| open-webui | 14.3 → 17.5 | 667 → 726 | 166 → 200 | 501 → 539 | 5 `present` → 5 `configured` |
| lobe-chat | 94.9 → 110.8 | 449 → 449 | 372 → 336 | 64 → 118 | unchanged (4 `missing`) |
| dify | 65.1 → 75.3 | 218 → 237 | 215 → 222 | 6 → 20 | 1 `present` → 1 `configured` |

- **New sinks are calls that were missed before**, mostly through client instances: `session.get`
  in text-generation-webui's `download-model.py`, `self.session.post` in `llama_cpp_server.py`,
  `async with session.post(` in open-webui, `client.request(` in dify, `xhr.open('PUT', url)` in
  lobe-chat, plus aliases such as `urllib_request.urlopen` (dify) and aiohttp `ClientSession`
  imported by name (open-webui). Samples were read by hand.
- **Fewer anonymous `dynamic` sinks in lobe-chat** (364 → 310): same-file constants such as
  `fetch(TOKEN_URL)` now resolve to the host, and `config.tokenEndpoint` / `SLACK_OAUTH_ACCESS_URL`
  become `config:<NAME>`.
- **False positives found in the first pass of this round and fixed before measuring:** undici
  `new Agent()` dispatchers were reported as requests, and a Worker `fetch(request, env) {` handler
  borrowed a `config:` label from a call inside its body. Both have regression tests.
- **Switch statuses did not change on these four projects**, because their real settings are in
  Dockerfiles and code. The reported bug (a `.env.example` value counted as configuration, and
  `NEXT_TELEMETRY_DISABLED=0` shown as "already set: =1") is covered by
  `tests/test_switch_status.py`.
- **Cost:** scans are 10–35% slower, mainly from per-file alias/instance collection.

## Reproduce

```bash
git clone --depth 1 https://github.com/open-webui/open-webui
repo-airgap scan open-webui
cat open-webui/.airgap/offline.env
```
