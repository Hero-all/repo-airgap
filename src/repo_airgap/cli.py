import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .analyzer.network_inventory import inventory as build_outbound_inventory
from .analyzer.prompt_builder import build_prompt
from .auth.commands import auth_command, configure_auth_parser
from .engine import scan
from .io import atomic_write, dump_json, load_json
from .models import SEVERITIES
from .patcher.patch_manager import apply_plan, create_plan, revert, save_plan
from .patcher.verifier import verify
from .reporter import csv_report, report
from .review import display_inventory, review, save_choices
from .safety import repository, state_dir


def parser():
    p = argparse.ArgumentParser(
        prog="repo-airgap", description="Local privacy audit and reviewed source adaptation"
    )
    p.add_argument("--version", action="version", version=__version__)
    subs = p.add_subparsers(dest="command", required=True)
    s = subs.add_parser("scan", help="Read-only source audit (writes evidence only)")
    s.add_argument("target")
    s.add_argument("--output", type=Path)
    s.add_argument("--format", choices=("table", "json", "csv"), default="table")
    s.add_argument("--ignore-id", action="append", default=[])
    s.add_argument("--severity", choices=tuple(SEVERITIES), default="INFO")
    s.add_argument("--rules-dir", type=Path)
    s.add_argument("--semgrep-report", type=Path)
    s = subs.add_parser(
        "review", help="List outbound purposes and record user choices; unspecified items stay KEEP"
    )
    s.add_argument("target")
    group = s.add_mutually_exclusive_group()
    group.add_argument("--choices", type=Path)
    group.add_argument("--set", action="append", default=[], dest="selections", metavar="ITEM-001=DISABLE")
    group.add_argument("--interactive", action="store_true")
    s = subs.add_parser("plan", help="Build an exact plan from reviewed decisions; no source edits")
    s.add_argument("target")
    s.add_argument("--decisions", type=Path, required=True)
    s.add_argument("--output", type=Path)
    s = subs.add_parser("apply", help="Apply a reviewed plan; invoking this command authorizes its edits")
    s.add_argument("target")
    s.add_argument("--plan", type=Path, required=True)
    s = subs.add_parser("verify", help="Run explicitly supplied checks; import optional runtime observations")
    s.add_argument("target")
    s.add_argument(
        "--command", dest="checks", action="append", default=[], help='JSON argv, e.g. ["npm","test"]'
    )
    s.add_argument("--observations", type=Path)
    s.add_argument("--timeout", type=int, default=120)
    s = subs.add_parser("report", help="Generate a Markdown evidence report")
    s.add_argument("target")
    s = subs.add_parser("revert", help="Restore only this tool's snapshots; refuse conflicts")
    s.add_argument("target")
    s.add_argument("--manifest", type=Path)
    configure_auth_parser(subs)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "auth":
            return auth_command(args)
        root = repository(args.target)
        if args.command == "scan":
            state = state_dir(root, create=True)
            if (state / "manifest.json").exists():
                m = load_json(state / "manifest.json")
                if m.get("status") in ("applied", "applying", "reverting"):
                    raise ValueError(
                        "Active transaction: keep original audit evidence; revert before rescanning"
                    )
            result = scan(
                root,
                rules_dir=args.rules_dir,
                ignore_ids=args.ignore_id,
                min_severity=args.severity,
                semgrep_report=args.semgrep_report,
            ).to_dict()
            output = args.output.resolve() if args.output else state / "audit_candidates.json"
            # Do not allow --output to overwrite arbitrary source in a read-only audit.
            if output.is_relative_to(root) and not output.is_relative_to(state):
                raise ValueError(
                    "Scan output inside target must be under .airgap; use an external report path otherwise"
                )
            if output.is_relative_to(state) and output.name in (
                "manifest.json",
                "plan.json",
                "verification.json",
                "outbound_inventory.json",
                "review_prompt.json",
                "review_inventory.json",
                "user_choices.json",
                "auth_inventory.json",
            ):
                raise ValueError("Scan output cannot overwrite a reserved state artifact")
            dump_json(state / "audit_candidates.json", result)
            dump_json(state / "outbound_inventory.json", build_outbound_inventory(result))
            dump_json(state / "review_prompt.json", build_prompt(result))
            if output != state / "audit_candidates.json":
                dump_json(output, result)
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.format == "csv":
                print(csv_report(result), end="")
            else:
                table = Table(title="repo-airgap — review candidates")
                table.add_column("Category")
                table.add_column("Count", justify="right")
                for category, count in result["summary"].items():
                    table.add_row(category, str(count))
                Console().print(table)
                print(
                    f"Evidence: {state}\nFiles: {result['total_files_scanned']}; warnings: {len(result['warnings'])}"
                )
        elif args.command == "review":
            review_inventory = review(root, load_json(args.choices) if args.choices else None)
            display_inventory(review_inventory)
            selected = []
            if args.interactive:
                for item in review_inventory["items"]:
                    action = (
                        input(f"{item['id']} {item['title']} [{'/'.join(item['allowed_actions'])}] (KEEP): ")
                        .strip()
                        .upper()
                        or "KEEP"
                    )
                    selected.append({"item_id": item["id"], "action": action})
            else:
                for value in args.selections:
                    item_id, separator, action = value.partition("=")
                    if not separator:
                        raise ValueError("Use --set ITEM-001=DISABLE (or KEEP/REVIEW/REPLACE_LOCAL_AUTH)")
                    selected.append({"item_id": item_id, "action": action.upper()})
            if selected or args.interactive:
                save_choices(
                    root,
                    review_inventory,
                    {"scan_timestamp": review_inventory["scan_timestamp"], "choices": selected},
                )
                print("User choices saved; source unchanged. Unspecified items remain KEEP.")
            elif not args.choices:
                print("No choices recorded. Review items with the user before planning any edits.")
        elif args.command == "plan":
            state = state_dir(root)
            if (state / "manifest.json").exists() and load_json(state / "manifest.json").get("status") in (
                "applied",
                "applying",
                "reverting",
            ):
                raise ValueError("Revert the active transaction before creating a new plan")
            result = create_plan(root, load_json(state / "audit_candidates.json"), load_json(args.decisions))
            if (
                args.output
                and args.output.resolve().is_relative_to(root)
                and not args.output.resolve().is_relative_to(state)
            ):
                raise ValueError("Plan output inside target must be under .airgap")
            path = save_plan(root, result, args.output)
            atomic_write(path.with_suffix(".patch"), result["expected_diff"].encode("utf-8"))
            print(f"Plan: {path}; {len(result['files'])} files. Review before apply.")
        elif args.command == "apply":
            result = apply_plan(root, args.plan)
            print(f"Applied {result['id']}: {len(result['files'])} files; verification pending.")
        elif args.command == "revert":
            result = revert(root, args.manifest)
            print(f"Reverted {result['id']}; unrelated files preserved.")
        elif args.command == "verify":
            if args.timeout < 1:
                raise ValueError("Timeout must be positive")
            result = verify(root, args.checks, args.observations, args.timeout)
            print(
                f"Checks: {result['status']}; runtime: {result['runtime']['status']}. No air-gap certification."
            )
            return 1 if result["status"] == "failed" else 0
        elif args.command == "report":
            print(report(root))
        return 0
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as e:
        print(f"repo-airgap: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
