<div align="center">

# repo-airgap — Privacy Audit & Air-Gapped Readiness Tool

[![CI](https://github.com/Hero-all/repo-airgap/actions/workflows/ci.yml/badge.svg)](https://github.com/Hero-all/repo-airgap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/repo-airgap?label=PyPI&color=0969da)](https://pypi.org/project/repo-airgap/)
[![Release](https://img.shields.io/github/v/release/Hero-all/repo-airgap?label=Release&color=0969da)](https://github.com/Hero-all/repo-airgap/releases)
[![License](https://img.shields.io/badge/License-MIT-0969da)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-e07a2b)](pyproject.toml)
[![Interface](https://img.shields.io/badge/Interface-CLI%20%2B%20Agent%20Skill-4c9a2a)](SKILL.md)
<br/>
[![Patch Engine](https://img.shields.io/badge/Patch%20Engine-Transactional%20%26%20Reversible-1d76db)](#workflow)
[![Architecture](https://img.shields.io/badge/Architecture-Human%20%2F%20Agent--in--the--Loop-7b2cbf)](#agent-skill)
[![Scanner](https://img.shields.io/badge/Scanner-100%25%20Local%20%26%20Zero%20Cloud%20Calls-2ea44f)](#scope-and-limits)

**Finds the places where open-source software quietly connects to the internet, so you can run it safely on an offline network**

[**English**](README.md) · [**简体中文**](README_CN.md)

</div>

```bash
pip install repo-airgap
repo-airgap scan /path/to/your-repo
```

<div align="center">

<img src="assets/workflow-preview.svg" alt="repo-airgap CLI Workflow Preview" width="100%"/>

</div>

---

## What is this? (in plain words)

Many companies, hospitals, schools and government offices download open-source software and run it on **internal networks with no internet access**. The trouble is that this software often contains features that quietly go online:

- **Usage reporting** ("telemetry"): which buttons you click and for how long, sent to the developer's servers;
- **Downloads at startup**: AI models, fonts, word lists. It works at home with internet, then hangs or crashes on an offline network;
- **Ads and upgrade prompts** fetched from the internet.

Nobody can read tens of thousands of files by hand to find all of this. **repo-airgap is a check-up tool that does it for you:**

1. **Check-up**: it finds every place in the code that may connect to the internet and lists them as "look at this first / normal / probably harmless";
2. **Advice**: many programs already have an official "turn off reporting" or "offline mode" switch. The tool collects those switches for you, **with no code changes**;
3. **Change it, and undo it**: for the online features you decide you don't want, it helps you (or an AI coding assistant) make precise edits. If you don't like the result, one command **restores every file exactly as it was**.

Think of it as an **electrical inspection before moving in**: it finds which sockets are wired to the outside, tells you which switches you can simply turn off, and takes a photo before rewiring anything so it can always be put back.

**What it does not do:**

- It only reads code. It never runs your software, and **the check itself never goes online**;
- It lists *possible* connections that a person should glance at. **It does not promise to find 100% of them**;
- It does not cut your network and cannot prove software "never goes online". Always try it on a real offline network in the end.

**Who it is for**: people who deploy open-source software on internal networks, security and privacy reviewers, and teams doing compliance checks.

---

## Technical overview

repo-airgap provides a local Python CLI and an Agent Skill for reviewing optional outbound requests, telemetry, and promotional UI in source repositories. The scanner produces static candidates; an Agent or human reviews business context, supplies exact edits, and then checks and applies a reversible modification plan.

The CLI makes **no remote model or API requests** and **does not execute target code** during scanning. Scan results are review candidates, not malice verdicts.

> [!NOTE]
> Benchmarked on four real open-source projects: dify, open-webui, lobe-chat and text-generation-webui (see [benchmarks](benchmarks/RESULTS.md)). Release history is in the [CHANGELOG](CHANGELOG.md).

### Offline readiness hints (no code changes)

Many frameworks ship an **official switch** to disable telemetry or force offline mode. `scan` detects them from your manifests (`package.json`, `requirements*.txt`, `pyproject.toml`) and code, looks for places in the repository that *assign* each switch, and writes suggested values to `.airgap/offline.env`. The screenshot above is the real, unedited output of `repo-airgap scan` on [text-generation-webui](https://github.com/oobabooga/text-generation-webui).

Each switch gets one status, based on repository files only:

| Status | Meaning |
| --- | --- |
| `configured` | A non-example file (Dockerfile, compose, committed `.env`, CI config, code) assigns the recommended value |
| `mismatch` / `conflict` | A different value is assigned / both matching and different values are assigned |
| `unresolved` | Assigned, but the value is interpolated or inherited (`${VAR}`, `- VAR`, a variable in code) |
| `example_only` | Appears only in example/template/docs files (`.env.example`, `*.sample`, `docs/`); not treated as configuration |
| `missing` | Not assigned anywhere that was scanned |

Reads and comparisons (`os.getenv("X", "0")`, `process.env.X === "1"`) are not assignments. No status proves that the deployed process receives the value; gitignored files such as a local `.env` are not read. Every switch that is not `configured` is written to `offline.env` as an active line, with the conflicting evidence noted above it.

It covers 27 frameworks/tools (Next.js, Nuxt, Gatsby, Astro, Storybook, Angular CLI, Turborepo, Expo, Prisma, Cypress, Playwright, Puppeteer, Electron, Hugging Face, Gradio, Streamlit, Chroma, Ray, W&B, Haystack, Unstructured, Semgrep, tiktoken, .NET, Yarn Berry, …). It also flags build-time and runtime downloads: `from_pretrained`, `hf_hub_download`, `nltk.download`, `torch.hub.load`, tiktoken encodings, `next/font/google`, and Dockerfile `RUN` fetches or public base images. Rules live in [`rules/offline_switches.json`](rules/offline_switches.json), and each entry links to its official documentation.

By default, test/fixture/spec files, minified bundles, and loopback/private/single-label hosts (e.g. `localhost`, `10.x`, Docker service names) are excluded from candidates. Use `--include-tests` to scan tests too.

## Install

Python 3.11+ is required.

### From PyPI (Recommended)

```bash
pip install repo-airgap

# Or with uv / pipx:
uv tool install repo-airgap
uvx repo-airgap scan /path/to/your-repo
```

### From GitHub (Latest `main`)

```bash
pip install git+https://github.com/Hero-all/repo-airgap.git
```

### From Local Checkout (Development)

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
# Only needed to run the generated Flask local-auth adapter:
uv pip install --python .venv/Scripts/python.exe -e ".[auth]"
.venv/Scripts/repo-airgap.exe --help
```

On macOS/Linux use `.venv/bin/python` and `.venv/bin/repo-airgap`. For a regular package installation use `python -m pip install .`; only `rich` and `pathspec` are core dependencies. Development checks use `pytest` and `ruff`. Offline machines need these dependencies staged beforehand; `scan` does not download them.

## Workflow

Replace `/target` with the absolute repository directory. The commands below assume `repo-airgap` is on `PATH`; otherwise use the virtual environment executable.

```text
repo-airgap scan /target
repo-airgap scan /target --format csv
repo-airgap scan /target --ignore-id promo_keyword --severity MEDIUM
repo-airgap review /target
repo-airgap review /target --set ITEM-001=DISABLE --set ITEM-002=KEEP
repo-airgap plan /target --decisions /reports/decisions.json
repo-airgap apply /target --plan /target/.airgap/plan.json
repo-airgap verify /target --command '["python","-m","pytest"]'
repo-airgap report /target
repo-airgap revert /target
```

The scan writes evidence under `/target/.airgap/`, without changing target source. Add `.airgap/` to the target's `.gitignore` manually if needed. `--output` exports an additional JSON copy to an external path, or a path under `.airgap`. CSV is written to stdout (`--format csv`); on older Windows PowerShell use an explicit UTF-8 output encoding when redirecting non-ASCII content.

`review` groups outbound candidates by destination/purpose and defaults unselected items to `KEEP`. Only items explicitly set to `DISABLE` may enter a normal sanitization plan; Google authentication items additionally support `REPLACE_LOCAL_AUTH`. Here, `DISABLE` means applying a reviewed source patch to selected call sites—it does not disconnect the OS network or prove zero runtime traffic. Reviewers provide exact replacements for selected candidates following the [decisions format](references/decisions.md). `plan` does not modify source; inspect the full diff before calling `apply`. Plans are refused if `.airgap/user_choices.json` is missing or out of sync with the current scan.

### Local Authentication Migration (Flask)

The initial local authentication adapter supports simple module-level Flask applications:

```text
repo-airgap auth inspect /target
repo-airgap review /target --set ITEM-001=REPLACE_LOCAL_AUTH
repo-airgap auth plan /target --mapping /path/flask-auth-mapping.json
repo-airgap apply /target --plan /target/.airgap/plan.json
repo-airgap auth user-add --database /private/auth.sqlite3 --email user@example.com --user-id U001 --password-env LOCAL_AUTH_PASSWORD
```

See [references/flask-auth-mapping.example.json](references/flask-auth-mapping.example.json) for a copyable mapping template. Migration generates a local SQLite user/session runtime with password hashing, CSRF protection, role checks, and failed-login rate limiting, while returning `410` on the legacy Google callback. It does not turn Google business services (Drive, Gmail, etc.) into local capabilities, nor does it auto-claim legacy accounts by email; user import requires verified `business_id` and `google_sub` mappings before an administrator sets a new password. Projects with existing `Flask-Login`, `Blueprint`, Google OAuth SDKs (`Authlib`, `Flask-Dance`, `google-auth-oauthlib`), complex OAuth flows, or Google business API dependencies stop at the manual-adaptation boundary to avoid intercepting routes while leaving import-time or init-time OAuth side effects active.

### Artifacts & Transaction Safety

| Output | Purpose |
| --- | --- |
| `.airgap/audit_candidates.json` | Candidates with file hashes and redacted context |
| `.airgap/outbound_inventory.json` | Outbound targets and call-site index |
| `.airgap/review_prompt.json` | Local Agent review handoff |
| `.airgap/plan.json`, `plan.patch` | Reviewed exact replacement snapshots and diff |
| `.airgap/manifest.json` | Current transaction and rollback snapshots |
| `.airgap/airgap-sanitized.patch` | Only this tool's applied changes |
| `.airgap/verification.json` | Explicit verification commands and runtime evidence |
| `.airgap/AIRGAP_REPORT.md` | Applied, retained, and unresolved audit evidence |

The original bytes are the baseline, including uncommitted user work. The tool never resets the working tree. Changed baselines stop application; later edits to touched files stop rollback before any file is restored. Unrelated files remain outside the transaction. Read-only files are refused before anything is written. Each file is replaced atomically and keeps its permission bits; if `apply` fails or is interrupted with Ctrl+C, it restores every file it wrote and leaves files changed by another process untouched. After a hard kill, `.airgap/manifest.json` (status `applying`/`reverting`) lets `revert` resume, and `.airgap/mutation.lock` records the owner PID, host and start time; it is never removed automatically. Confirm that process is gone before deleting it. Paths through symlinks or junctions are refused at plan, apply and revert time.

Known gaps: a write by another program in the instant between the final hash check and the atomic replace is not detected; atomic replacement gives the file a new inode, so hard links to it are not updated and ACLs/extended attributes/ownership are not copied (POSIX mode bits are).

Plans and manifests contain full source snapshots; patches can contain secrets. Secret redaction in scan/report output is best-effort. Keep `.airgap` and decision files local and private. Plans are trusted change intent, not signed artifacts—always inspect the full diff when accepting a plan from another person.

## Agent Skill

The entrypoint is [SKILL.md](SKILL.md). It includes classification, exact-edit, verification, and rollback instructions, with references for schemas and return-contract stubs. The project also supplies [`agents/openai.yaml`](agents/openai.yaml) and [`.cursorrules`](.cursorrules).

To make the skill discoverable, place the project folder (including `SKILL.md` and `references/`) under the agent's skills directory, or use that agent's supported local-skill installation method. Install the CLI first. This development checkout does not automatically change global agent settings or install a skill into every agent. Codex discovery typically uses `~/.codex/skills/repo-airgap/`. Invoke it as `$repo-airgap`, specifying the target repository and desired scope.

The distributable `dist/repo-airgap-skill-<version>.zip` includes a `repo-airgap/` folder with the Skill, CLI source, rules, references, and tests. Extract that folder into the agent's skills directory and install the CLI from that extracted folder. The zip excludes virtual environments and target audit evidence.

## Optional Tools

* **Semgrep JSON/SARIF Import**: `repo-airgap scan /target --semgrep-report /reports/semgrep.json`. Explicit source URI locations and rule IDs are imported; full taint traces and SARIF artifact-index references are not supported yet.
* **mitmproxy Exporter**: The optional [mitmproxy exporter](scripts/mitmproxy_export.py) writes normalized observation metadata without bodies, headers, or URL query strings.
* **Runtime Observations**: `repo-airgap verify /target --observations /reports/runtime.json` imports normalized runtime observations. OpenSnitch records may be converted manually; no native OpenSnitch adapter is bundled.

See [runtime evidence format and limits](references/runtime.md).

## Scope and Limits

* **Static Coverage**: Scan covers UTF-8 source, nested `.gitignore`, URLs, quoted bare domains, public IPs, common HTTP/socket/DNS/upload calls, telemetry manifests/imports, promotion keywords, and environment/container switches. It skips generated, binary, linked, oversized, minified, and legal files, plus tests and locale catalogs by default. Measured scan time on real projects ranges from about 1 s (144 files) to about 70 s (23k files); see [benchmarks](benchmarks/RESULTS.md).
* **Call Tracking (per file)**: Besides direct calls (`requests.get`, `fetch`, `axios`, `httpx`, `aiohttp`, `urllib`, XHR, `EventSource`, `$.ajax`, WebSocket), the scanner follows import aliases (`import requests as rq`, `from requests import post`, `import ky from 'ky'`, got/undici/node-fetch/superagent), client instances created in the same file (`requests.Session()`, `httpx.Client()`, `aiohttp.ClientSession()`, `axios.create()`, `new XMLHttpRequest()`), arguments of multi-line calls, URL constants assigned once in the same file, env/settings references (`config:<NAME>`) and `"https://" + host` concatenation.
* **Not Followed**: values passed across files or through function parameters, clients created in another module, project-specific wrapper classes, `getattr`/dynamic dispatch, and clients built from imported factories. Such calls are reported as `dynamic` or not at all; search for in-house HTTP wrappers before concluding a path has no egress. Only Python and JS/TS get call tracking; Go is matched by a few direct patterns; other languages only get generic URL/IP matching.
* **Static Analysis Boundaries**: Static rules do not prove complete data flow. A nearest function declaration is a navigation hint. Credentials/high-entropy strings near a sink are review signals, not standalone exfiltration findings. Dynamic destinations stay unknown.
* **Supported Edit Languages**: Exact source edits support Python/JS/TS/JSX/TSX/Vue/Svelte/HTML. Python syntax is checked during planning/application; JS/TS and framework builds require the target's build commands. Other languages use separately reviewed manual patches.
* **Verification Execution**: `verify` runs only supplied argv commands with no inferred shell or project installation. Those commands execute code: use a suitable isolated environment. It records results, not semantic equivalence. No supplied commands means `not_verified`.
* **No OS-Level Isolation Guarantee**: **The CLI does not disconnect the OS network or certify air-gap readiness.** Run critical flows under actual network isolation and collect independent evidence. Proxy observations cover only routed traffic; empty captures prove neither absence of requests nor complete path coverage.
* **False Positives**: Static rules still produce false positives and gaps on unfamiliar codebases; please report them as issues.
* **Legal Heuristics**: License/attribution safeguards are conservative heuristics. Review all changes; preserving legal lines does not prove compliance with every upstream license.

## Development and Reproducible Demo

```powershell
.venv/Scripts/python.exe -m compileall -q src
.venv/Scripts/python.exe -m pytest
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/python.exe scripts/demo.py --output .airgap-demo
.venv/Scripts/python.exe scripts/package_skill.py
```

The demo copies the fixture, scans it, creates and applies a reviewed telemetry stub, runs Python and Node return-contract tests, produces a report, and then proves byte-exact rollback. Node 22.6+ with `--experimental-strip-types` is needed for the TypeScript fixture runtime. There is no dependency installation for the fixture. Its process-level fetch/socket guards prevent those tested calls from real I/O; they are not OS-level isolation and do not cover all network mechanisms. Choose a fresh demo output directory on repeat runs; existing directories are never overwritten.

## Disclaimer

1. **Intended Use**: `repo-airgap` assists privacy audits, de-telemetry, and offline adaptation of software you have permission to modify, subject to its MIT, Apache-2.0, GPL, BSD, or other applicable license.
2. **No License Bypass**: It provides no license-key, paywall, or DRM bypass functionality.
3. **Respect Copyright**: Preserve upstream licenses, notices, and attribution, and comply with redistribution requirements.
