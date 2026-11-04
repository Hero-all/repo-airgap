"""User-facing inventory and explicit choices, before semantic edits are proposed."""

import re

from rich.console import Console
from rich.table import Table

from .io import dump_json, load_json, utcnow
from .safety import safe_path, state_dir

GOOGLE_LOGIN = re.compile(
    r"accounts\.google\.com|oauth2\.googleapis\.com|google.*(?:oauth|login)|googleprovider|"
    r"next-auth/providers/google|signInWithPopup|auth/google|"
    r"google[-_.]auth[-_.]oauthlib|google\.oauth2|google_auth_oauthlib|"
    r"passport[-_]google[-_]oauth|GoogleStrategy|GoogleAuthProvider|"
    r"@react-oauth/google|react[-_]google[-_]login|flask[-_]dance",
    re.I,
)


def build_inventory(audit):
    groups = {}
    for candidate in audit["candidates"]:
        destination = candidate.get("destination")
        evidence = " ".join(str(candidate.get(k) or "") for k in ("destination", "matched_content"))
        is_auth = candidate["category"] == "AUTH_PROVIDER"
        is_google = bool(GOOGLE_LOGIN.search(evidence))
        provider = None
        if is_auth or is_google:
            provider = "google" if is_google else "unknown"
            key, kind = (
                ("google-login" if is_google else "auth-provider:" + (destination or candidate["file_path"])),
                "authentication",
            )
            title, impact = (
                ("Google 登录", "直接切断可能使用户无法登录；替换需要真实的本地账号、会话及权限校验。")
                if is_google
                else (
                    "外部认证提供方（待确认）",
                    "可能是核心登录依赖；需先确认提供方和业务影响，不能直接按 Google 迁移。",
                )
            )
        elif candidate["category"] == "TELEMETRY_SDK" or "known_telemetry_domain" in candidate.get(
            "related_signals", []
        ):
            key, kind = "telemetry:" + (destination or candidate["matched_content"]), "telemetry"
            title, impact = (
                "遥测/错误报告：" + (destination or candidate["matched_content"]),
                "停用可能失去使用统计或错误诊断；需核查调用方。",
            )
        elif candidate["category"] == "PROMO_UI":
            key, kind = "promotion:" + candidate["file_path"], "promotion"
            title, impact = "推广组件：" + candidate["file_path"], "隐藏界面还需检查数据加载及启动副作用。"
        elif destination == "self":
            key, kind = "self-api", "self_api"
            title, impact = (
                "应用自身后端（同源相对路径）",
                "前端调用自己的后端接口，通常是核心功能；内网部署时随应用一起部署即可，一般保持 KEEP。",
            )
        elif destination and destination.startswith("config:"):
            key, kind = "destination:" + destination, "configured_backend"
            title, impact = (
                "可配置服务地址：" + destination.split(":", 1)[1],
                "地址来自配置/环境变量，内网部署时改为内网服务地址即可，通常无需改源码。",
            )
        elif "reference_link" in candidate.get("related_signals", []):
            key, kind = "reference-links", "reference"
            title, impact = (
                "文档/参考链接（同一行未发现网络调用）",
                "多为界面上的跳转链接，不会被程序主动请求；内网中点击会打不开，一般保持 KEEP。",
            )
        else:
            key, kind = "destination:" + (destination or candidate["file_path"]), "outbound"
            title, impact = (
                destination or candidate["file_path"],
                "用途待核查；可能是核心 API、CDN 或动态网络目标。",
            )
        group = groups.setdefault(
            key,
            {
                "id": "",
                "kind": kind,
                "provider": provider if (is_auth or is_google) else None,
                "title": title,
                "impact": impact,
                "candidate_ids": [],
                "locations": [],
                "categories": [],
                "evidence": [],
                "default_action": "KEEP",
                "allowed_actions": ["KEEP", "DISABLE", "REVIEW"]
                + (["REPLACE_LOCAL_AUTH"] if provider == "google" else []),
            },
        )
        group["candidate_ids"].append(candidate["id"])
        if candidate["category"] not in group["categories"]:
            group["categories"].append(candidate["category"])
        evidence_value = candidate.get("matched_content") or candidate.get("destination") or "dynamic"
        if evidence_value not in group["evidence"]:
            group["evidence"].append(evidence_value)
        location = f"{candidate['file_path']}:{candidate['line_number']}"
        if location not in group["locations"]:
            group["locations"].append(location)
    items = list(groups.values())
    for index, item in enumerate(items, 1):
        item["id"] = f"ITEM-{index:03d}"
    return {
        "repo_path": audit["repo_path"],
        "scan_timestamp": audit["scan_timestamp"],
        "default_action": "KEEP",
        "items": items,
        "note": (
            "静态推测供用户选择；未选择项保持 KEEP。DISABLE 仅允许对关联源码候选生成计划，"
            "不代表操作系统防火墙断网或运行时已证明零外联。选择不等于补丁已生成或应用。"
        ),
    }


def display_inventory(inventory):
    table = Table(title="外联清单 — 未选择项保留；DISABLE 表示源码级停用", show_lines=True)
    for title in ("编号", "发现/推测用途", "候选/证据", "影响", "位置", "可选操作"):
        table.add_column(title)
    for item in inventory["items"]:
        table.add_row(
            item["id"],
            item["title"],
            "\n".join([", ".join(item.get("candidate_ids", [])), *item.get("evidence", [])[:3]]),
            item["impact"],
            "\n".join(item["locations"][:4]),
            ", ".join(item["allowed_actions"]),
        )
    Console().print(table)


def save_choices(root, inventory, selections):
    entries = selections.get("choices", [])
    if not isinstance(entries, list):
        raise ValueError("choices must be an array")
    if selections.get("scan_timestamp") != inventory["scan_timestamp"]:
        raise ValueError("Choices must reference the current scan_timestamp")
    by_id = {i["id"]: i for i in inventory["items"]}
    seen = set()
    for choice in entries:
        if not isinstance(choice, dict):
            raise ValueError("Every choice must be an object")
        item = by_id.get(choice.get("item_id"))
        if not item or item["id"] in seen or choice.get("action") not in item["allowed_actions"]:
            raise ValueError("Unknown/duplicate review item or invalid action")
        seen.add(item["id"])
    result = {
        "repo_path": str(root),
        "scan_timestamp": inventory["scan_timestamp"],
        "chosen_at": utcnow(),
        "choices": [
            {
                "item_id": item["id"],
                "candidate_ids": item["candidate_ids"],
                "action": next((c["action"] for c in entries if c["item_id"] == item["id"]), "KEEP"),
            }
            for item in inventory["items"]
        ],
    }
    dump_json(state_dir(root, create=True) / "user_choices.json", result)
    return result


def assert_selection(root, audit, candidate_id, action):
    path = state_dir(root) / "user_choices.json"
    if not path.exists():
        raise ValueError(
            "User choices missing: run review and record the user's selection before planning edits"
        )
    choices = load_json(path)
    if choices.get("repo_path") != str(root) or choices.get("scan_timestamp") != audit["scan_timestamp"]:
        raise ValueError("User choices are stale; review the current scan")
    if not isinstance(choices.get("choices"), list) or not any(
        isinstance(c, dict) and candidate_id in c.get("candidate_ids", []) and c.get("action") == action
        for c in choices["choices"]
    ):
        raise ValueError(f"Candidate {candidate_id} was not selected for {action}; default is KEEP")


def review(root, selections=None):
    state = state_dir(root, create=True)
    audit = load_json(state / "audit_candidates.json")
    # Refuse to attach choices to evidence whose source has changed.
    from .io import sha256

    for c in audit["candidates"]:
        if sha256(safe_path(root, c["file_path"]).read_bytes()) != c["file_sha256"]:
            raise ValueError("Source changed since scan; rescan before user review")
    result = build_inventory(audit)
    dump_json(state / "review_inventory.json", result)
    if selections is not None:
        save_choices(root, result, selections)
    return result
