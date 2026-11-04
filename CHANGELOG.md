# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Offline readiness hints**: 27 official telemetry/offline switches (Next.js, Gradio, Hugging Face,
  Chroma, Streamlit, Playwright, Electron, …) and 8 build/runtime download signals. `scan` reports
  which switches are already set and writes `.airgap/offline.env`.
- `--include-tests`; tests, locale catalogs and minified bundles are skipped by default.
- Calls without a literal host are labeled: `self` for same-origin relative paths
  (`fetch('/api/...')`, LOW) and `config:<NAME>` for deployer-configured base URLs
  (`` fetch(`${API_BASE_URL}/...`) ``, `f"{self.base_url}/..."`, MEDIUM).
- URLs that are only displayed or linked (`href`, docs hosts, `/issues`, `/blob`, …) with no
  network call or fetching attribute on the line are tagged `reference_link` (LOW).
- `review` groups same-origin calls, each configured base URL, and reference links into single
  KEEP-by-default items instead of one item per host.
- Scan summary shows a HIGH / MEDIUM / LOW breakdown per category.
- Real-world benchmark on dify, lobe-chat, open-webui and text-generation-webui
  ([benchmarks/RESULTS.md](benchmarks/RESULTS.md)).

### Fixed
- dify scan: 11,886 candidates / 358 MB report → 1,274 / 2.5 MB (snippet line cap, minified-bundle
  detection, loopback/private/single-label hosts, XML/JSON-Schema identifier URIs, version strings,
  CIDR ranges and SVG geometry misread as IPs).
- Network-sink false positives: `httpx.Client` type hints, `httpx.TimeoutException`, identifiers
  ending in `Requests.` (`scmChangeRequests.$inferInsert`), `getActiveSocket()` /
  `PatchedWebSocket(`, and application methods named `upload_file`.
- Wheel-URL / `name @ url` / `#egg=` requirements; `os.environ[...]` assignments recognized as set.
- Per-line import parsing ran once per known package (×36).

### Changed
- Development status set to Alpha; README screenshot regenerated from real, unedited CLI output.

## [0.1.0] — 2026-11-04

### Added
- Initial release: local `scan` / `review` / `plan` / `apply` / `verify` / `report` / `revert` CLI,
  Agent Skill (`SKILL.md`), Flask local-auth adapter, bilingual README.
- CI on Ubuntu and Windows (Python 3.11/3.12, Node 22) and tag-driven GitHub Releases.
