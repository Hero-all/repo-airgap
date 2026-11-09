# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.4.0] - 2026-11-09

**In plain words / 白话版**

- The tool used to say "telemetry is already turned off" just because an *example* settings file
  mentioned it — even when that example actually said "on". It now only trusts real settings
  files and tells you exactly where it looked.
  以前只要示例配置文件里出现了关闭遥测的开关，工具就说「已经关好了」，哪怕示例里写的其实是「开」。现在只认真正生效的配置文件，并告诉你它在哪看到的、写的是什么。
- It now catches more of the ways programs quietly connect to the internet (renamed imports,
  reused connection objects, calls split across several lines).
  能发现更多「偷偷联网」的写法，比如把联网库改了个名字再用、先建好连接对象再反复调用、一句代码拆成好几行写。
- Modifying and undoing changes is safer: pressing Ctrl+C halfway no longer leaves files
  half-changed, and read-only files are left alone instead of failing midway.
  改代码和撤销更安全：改到一半按 Ctrl+C 不会再留下「改了一半」的文件；设成只读的文件不会被强行改动。

Fixes from an external review of v0.3.0. Each item was reproduced first; regression tests were
confirmed to fail on v0.3.0.

### Fixed
- **Switch status overstated configuration.** `.env.example` with `NEXT_TELEMETRY_DISABLED=0` was
  reported as `present` and `offline.env` said `# already set: NEXT_TELEMETRY_DISABLED=1`. The old
  matcher accepted any `NAME` followed by `=`, `:` or `,`, so reads such as
  `os.environ.get("X", "0")` and comparisons such as `process.env.X === "1"` also counted.
  Statuses are now `configured` / `mismatch` / `conflict` / `unresolved` / `example_only` /
  `missing`, each with location, source kind and literal value; `deployment_effect` is always
  `unverified`. Example/template/docs files never count as configuration. Every switch that is not
  `configured` is an active line in `offline.env`.
- **`apply` interrupted by Ctrl+C left a half-applied transaction** (only `Exception` triggered the
  rollback). Rollback now runs for any interruption and decides by file content, so a file replaced
  just before the interrupt is also restored; files changed by another process are left untouched
  and listed.
- **Read-only files**: on Windows `apply` failed with `PermissionError` and left a read-only
  `.airgap-write-*` temp file in the source tree; on POSIX the file would have been replaced
  silently. Read-only targets are now refused before any write, and temp files are always removed.
- `mutation.lock` records PID, host and start time, and the error message explains how to recover.
  It is still never removed automatically.
- New files created by a transaction get the umask default instead of 0600; directory entries are
  fsynced after replace on POSIX.

### Added
- Per-file call tracking (`scanner/call_context.py`): import aliases (`import requests as rq`,
  `from requests import post`, `import ky from 'ky'`, got/undici/node-fetch/superagent), client
  instances created in the same file (`requests.Session`, `httpx.Client`, `aiohttp.ClientSession`,
  `axios.create`, `XMLHttpRequest`), multi-line call arguments, URL constants assigned once in the
  file, env/settings references (`config:<NAME>`), and `"https://" + host` concatenation.
  Direct patterns for `EventSource`, `$.ajax`, `urllib3`, `http.client`, `websocket(s)`.
- `env_accept` in `rules/offline_switches.json` for spellings confirmed in library source
  (Hugging Face `1/ON/YES/TRUE`, Chroma boolean forms, W&B `disabled`, tiktoken any path).
- Scan report limitations now state what call tracking and switch status do not cover.

### Changed
- A constructor call that closes on its own line no longer takes its destination label from the
  next line (`requests.Session()` was labelled from the following call).
- Benchmarks: round 3 before/after table in [benchmarks/RESULTS.md](benchmarks/RESULTS.md); scans
  are 10–35% slower.

## [0.3.0] — 2026-11-04

Precision and review-noise round, measured before/after on four real projects
([benchmarks/RESULTS.md](benchmarks/RESULTS.md)).

### Added
- Calls without a literal host are labeled: `self` for same-origin relative paths
  (`fetch('/api/...')`, LOW) and `config:<NAME>` for deployer-configured base URLs
  (`` fetch(`${API_BASE_URL}/...`) ``, `f"{self.base_url}/..."`, MEDIUM).
- URLs that are only displayed or linked (`href`, docs hosts, `/issues`, `/blob`, …) with no
  network call or fetching attribute on the line are tagged `reference_link` (LOW).
- `review` groups same-origin calls, each configured base URL, and reference links into single
  KEEP-by-default items instead of one item per host.
- Scan summary shows a HIGH / MEDIUM / LOW breakdown per category.

### Fixed
- Only real HTTP/socket calls count as network sinks; `re.search()`, `router.get()`,
  `formData.append()` and similar are no longer reported (open-webui: 1,105 → 209 sinks).
- Instance HTTP clients such as `this.http.request(...)` are still detected.
- Network-sink false positives: `httpx.Client` type hints, `httpx.TimeoutException`, identifiers
  ending in `Requests.` (`scmChangeRequests.$inferInsert`), `getActiveSocket()` /
  `PatchedWebSocket(`, and application methods named `upload_file`.

### Changed
- README headings cleaned up; badge anchors fixed.
- Skill bundle file name follows the package version.

## [0.2.0] — 2026-11-04

### Added
- **Offline readiness hints**: 27 official telemetry/offline switches (Next.js, Gradio, Hugging Face,
  Chroma, Streamlit, Playwright, Electron, …) and 8 build/runtime download signals. `scan` reports
  which switches are already set and writes `.airgap/offline.env`.
- `--include-tests`; tests, locale catalogs and minified bundles are skipped by default.
- Real-world benchmark on dify, lobe-chat, open-webui and text-generation-webui.

### Fixed
- dify scan: 11,886 candidates / 358 MB report → 1,274 / 2.5 MB (snippet line cap, minified-bundle
  detection, loopback/private/single-label hosts, XML/JSON-Schema identifier URIs, version strings,
  CIDR ranges and SVG geometry misread as IPs).
- Wheel-URL / `name @ url` / `#egg=` requirements; `os.environ[...]` assignments recognized as set.
- Per-line import parsing ran once per known package (×36).

### Changed
- README screenshot regenerated from real, unedited CLI output.

## [0.1.0] — 2026-11-04

### Added
- Initial release: local `scan` / `review` / `plan` / `apply` / `verify` / `report` / `revert` CLI,
  Agent Skill (`SKILL.md`), Flask local-auth adapter, bilingual README.
- CI on Ubuntu and Windows (Python 3.11/3.12, Node 22) and tag-driven GitHub Releases.

[0.3.0]: https://github.com/Hero-all/repo-airgap/compare/e3e2ad6...v0.3.0
[0.2.0]: https://github.com/Hero-all/repo-airgap/compare/v0.1.0...e3e2ad6
[0.1.0]: https://github.com/Hero-all/repo-airgap/releases/tag/v0.1.0
