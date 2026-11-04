# repo-airgap

**Privacy Audit & Air-Gapped Deployment Readiness Tool**  
**隐私审计与内网离线化改造工具**

Version 0.1 provides a local Python CLI and an Agent Skill for reviewing optional
outbound requests, telemetry and promotional UI. The scanner produces candidates;
an Agent or human reviews business context, supplies exact edits, then checks and
applies a reversible plan. The CLI makes no model/API requests and does not
execute target code during scanning.

0.1 版提供本地 Python CLI 和 Agent Skill：扫描候选外联，结合调用方进行语义研判，
再生成可审阅、可回滚的精确改造计划。扫描结果不是恶意判定；CLI 不发送云端模型请求。

## Install / 安装

Python 3.11+ is required. From this checkout / 在当前项目目录：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
# Only needed to run the generated Flask local-auth adapter:
uv pip install --python .venv/Scripts/python.exe -e ".[auth]"
.venv/Scripts/repo-airgap.exe --help
```

On macOS/Linux use `.venv/bin/python` and `.venv/bin/repo-airgap`. For a regular
package installation use `python -m pip install .`; only `rich` and `pathspec`
are core dependencies. Development checks use pytest and ruff. Offline machines
need these dependencies staged beforehand; scan does not download them.

## Workflow / 使用流程

Replace `/target` with the absolute repository directory. The commands below
assume `repo-airgap` is on PATH; otherwise use the virtual environment executable.

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

The scan writes evidence under `/target/.airgap/`, without changing target source.
Add `.airgap/` to the target's `.gitignore` manually if needed. `--output` exports
an additional JSON copy to an external path, or a path under `.airgap`.
CSV is written to stdout (`--format csv`); on older Windows PowerShell use an
explicit UTF-8 output encoding when redirecting Chinese content.

`review` 会把外联按目标/用途列成清单，并把未选择项写成 `KEEP`。只有用户明确选为
`DISABLE` 的项目才允许进入普通净化计划；Google 登录另有 `REPLACE_LOCAL_AUTH` 选项。
这里的 `DISABLE` 指对选定源码调用点做审阅后的停用补丁，不等于操作系统断网，也不等于
运行时已经证明零外联。
审阅者再按 [decisions 格式](references/decisions.md) 为已选择的候选提供精确替换。
`plan` 不修改源码；检查完整 diff 后调用 `apply`。没有 `.airgap/user_choices.json`，
或选择与当前扫描不一致时，计划会拒绝执行。

本地认证迁移（首版仅支持简单的 Flask module-level app）使用：

```text
repo-airgap auth inspect /target
repo-airgap review /target --set ITEM-001=REPLACE_LOCAL_AUTH
repo-airgap auth plan /target --mapping /path/flask-auth-mapping.json
repo-airgap apply /target --plan /target/.airgap/plan.json
repo-airgap auth user-add --database /private/auth.sqlite3 --email user@example.com --user-id U001 --password-env LOCAL_AUTH_PASSWORD
```

可复制的映射模板见 [references/flask-auth-mapping.example.json](references/flask-auth-mapping.example.json)。
迁移会生成本地 SQLite 用户/会话运行时、密码哈希、CSRF、角色检查和失败限流，并将
旧 Google callback 返回 410。它不会把 Google Drive/Gmail 等业务服务变成本地能力，
也不会按 email 自动认领旧账号；用户导入必须提供经过核对的 `business_id` 与 `google_sub`，
再由管理员设置新密码。已有 Flask-Login、Blueprint、Google OAuth SDK（如 Authlib、
Flask-Dance、google-auth-oauthlib）、复杂 OAuth 或业务 Google API 依赖的项目会停在人工适配边界，
避免只拦截路由却留下导入时或初始化时的 OAuth 副作用。

| Output / 产物 | Purpose / 用途 |
| --- | --- |
| `.airgap/audit_candidates.json` | Candidates with file hashes and redacted context / 候选、哈希与上下文 |
| `.airgap/outbound_inventory.json` | Targets and call-site index / 外联目标与调用点 |
| `.airgap/review_prompt.json` | Local Agent handoff / 本地研判输入 |
| `.airgap/plan.json`, `plan.patch` | Reviewed exact replacement snapshots and diff / 改造计划 |
| `.airgap/manifest.json` | Current transaction and rollback snapshots / 当前事务与回滚快照 |
| `.airgap/airgap-sanitized.patch` | Only this tool's changes / 本工具实际改动 |
| `.airgap/verification.json` | Explicit commands and runtime evidence / 验证记录 |
| `.airgap/AIRGAP_REPORT.md` | Applied/retained/unresolved evidence / 审计报告 |

The original bytes are the baseline, including uncommitted user work. The tool
never resets the working tree. Changed baselines stop application; later edits
to touched files stop rollback before any file is restored. Other files remain
outside the transaction. Per-file atomic replacement and a recovery journal
support interrupted apply/revert. Inspect a leftover `mutation.lock` before
removing it after a crash; confirm no mutation process is still running.

Plans and manifests contain full source snapshots; patches can contain secrets.
Secret redaction in scan/report output is best-effort. Keep `.airgap` and
decisions local and private. Plans are trusted change intent, not signed artifacts;
always inspect the full diff when accepting a plan from another person.

## Agent Skill / 技能

The entrypoint is [SKILL.md](SKILL.md). It includes classification, exact-edit,
verification and rollback instructions, with references for schemas and stubs.
The project also supplies `agents/openai.yaml` and `.cursorrules`.

To make the skill discoverable, place the project folder (including `SKILL.md`
and `references/`) under the agent's skills directory, or use that agent's
supported local-skill installation method. Install the CLI first. This development
checkout does not automatically change global agent settings or install a skill
into every agent. Codex discovery typically uses `~/.codex/skills/repo-airgap/`.
Invoke it as `$repo-airgap`, specifying the target repository and desired scope.

The distributable `dist/repo-airgap-skill-0.1.0.zip` includes a `repo-airgap/`
folder with the Skill, CLI source, rules, references and tests. Extract that
folder into the agent's skills directory and install the CLI from that extracted
folder. The zip excludes environments and target audit evidence.

## Optional tools / 可选集成

* Import local Semgrep JSON/SARIF: `scan /target --semgrep-report /reports/semgrep.json`.
  Explicit source URI locations and rule IDs are imported; full taint traces and
  SARIF artifact-index references are not supported in 0.1.
* The optional [mitmproxy exporter](scripts/mitmproxy_export.py) writes normalized
  observation metadata without bodies, headers or URL query strings.
* `verify /target --observations /reports/runtime.json` imports normalized
  observations. OpenSnitch records may be converted manually; no native
  OpenSnitch adapter is bundled.

See [runtime evidence format and limits](references/runtime.md).

## Scope and limits / 能力边界

* Scan covers UTF-8 source, nested `.gitignore`, URLs, quoted bare domains,
  public IPs, common HTTP/socket/DNS/upload calls, telemetry manifests/imports,
  promotion keywords, and environment/container switches. It skips generated,
  binary, linked, oversized and legal files. A 3–5 second scan is a PRD target,
  not a measured guarantee for arbitrary repositories.
* Static rules do not prove complete data flow. A nearest function declaration
  is a navigation hint. Credentials/high-entropy strings near a sink are review
  signals, not standalone exfiltration findings. Dynamic destinations stay unknown.
* Exact source edits support Python/JS/TS/JSX/TSX/Vue/Svelte/HTML. Python syntax is
  checked during planning/application; JS/TS and framework builds require the
  target's build commands. Other languages use separately reviewed manual patches.
* `verify` runs only supplied argv commands with no inferred shell or project
  installation. Those commands execute code: use a suitable isolated environment.
  It records results, not semantic equivalence. No supplied commands means
  `not_verified`.
* **The CLI does not disconnect the OS network or certify air-gap readiness.**
  Run critical flows under actual isolation and collect independent evidence.
  Proxy observations cover only routed traffic; empty captures prove neither
  absence of requests nor complete path coverage.
* License/attribution safeguards are conservative heuristics. Review all changes;
  preserving legal lines does not prove compliance with every upstream license.

CLI 本身完全本地执行，Agent 使用哪种模型由用户的 Agent 环境决定。
扫描静态证据和执行测试均不能保证任意项目“100% 无外联”或“绝不损坏”。

## Development and reproducible demo / 开发与演示

```powershell
.venv/Scripts/python.exe -m compileall -q src
.venv/Scripts/python.exe -m pytest
.venv/Scripts/ruff.exe check src tests scripts
.venv/Scripts/python.exe scripts/demo.py --output .airgap-demo
.venv/Scripts/python.exe scripts/package_skill.py
```

The demo copies the fixture, scans it, creates and applies a reviewed telemetry
stub, runs Python and Node return-contract tests, produces a report, then proves
byte-exact rollback. Node 22.6+ with `--experimental-strip-types` is needed for the
TypeScript fixture runtime. There is no dependency installation for the fixture.
Its process-level fetch/socket guards prevent those tested calls from real I/O;
they are not OS-level isolation and do not cover all network mechanisms.
Choose a fresh demo output directory on repeat runs; existing directories are
never overwritten. The Skill frontmatter can also be checked with the installed
`skill-creator` validator, when available.

## References / 参考

* [repo_scanner](https://github.com/0vatsa/repo_scanner): reference detection ideas;
  no implementation or rules copied from this repository, which had no declared
  license at the time of research.
* [Semgrep](https://github.com/semgrep/semgrep): optional report import.
* [mitmproxy](https://github.com/mitmproxy/mitmproxy) and
  [OpenSnitch](https://github.com/evilsocket/opensnitch): optional runtime evidence.
* [ungoogled-chromium design](https://github.com/ungoogled-software/ungoogled-chromium/blob/master/docs/design.md):
  ordered patch and baseline ideas. Chromium-specific patches are not reused.
* The supplied `disruptiq/network-mapper-agent` URL returned 404 during research;
  no code dependency or maturity claim relies on it.

## Disclaimer / 免责声明

1. **Intended Use / 合法用途**: `repo-airgap` assists privacy audits, de-telemetry
   and offline adaptation of software you have permission to modify, subject to
   its MIT, Apache-2.0, GPL, BSD or other applicable license.
   本工具仅协助对拥有合法修改权限的软件进行隐私审计、去遥测及内网离线适配。
2. **No License Bypass / 严禁破解商业授权**: It provides no license-key, paywall
   or DRM bypass functionality. 本工具不提供或支持绕过商业授权、付费墙或 DRM 的功能。
3. **Respect Copyright / 尊重原作者署名**: Preserve upstream licenses, notices
   and attribution, and comply with redistribution requirements.
   本工具跳过法律文件，并阻止改变匹配到的版权/授权校验行；分发修改代码仍须遵守原协议。
