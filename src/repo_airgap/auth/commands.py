import getpass
import os
import sqlite3

from ..analyzer.network_inventory import inventory
from ..analyzer.prompt_builder import build_prompt
from ..io import dump_json, load_json
from ..patcher.patch_manager import save_plan
from ..safety import repository, state_dir
from .local_runtime import disable_user, import_users, provision_user, reset_password
from .migration import create_auth_plan, inspect_auth


def configure_auth_parser(subparsers):
    auth = subparsers.add_parser(
        "auth", help="User-selected local authentication migration and account administration"
    )
    subs = auth.add_subparsers(dest="auth_command", required=True)
    inspect = subs.add_parser("inspect", help="Read-only detection of Google sign-in integrations")
    inspect.add_argument("target")
    plan = subs.add_parser("plan", help="Plan a selected Flask Google-to-local sign-in migration")
    plan.add_argument("target")
    plan.add_argument("--mapping", required=True, type=str)
    add = subs.add_parser("user-add", help="Provision a local account using a private database")
    add.add_argument("--database", required=True)
    add.add_argument("--email", required=True)
    add.add_argument("--user-id", required=True)
    add.add_argument("--name", default="")
    add.add_argument("--role", choices=("user", "admin"), default="user")
    add.add_argument("--google-sub")
    add.add_argument("--password-env", help="Read password from this environment variable; otherwise prompt")
    reset = subs.add_parser("user-reset", help="Set a fresh password and revoke all sessions")
    reset.add_argument("--database", required=True)
    reset.add_argument("--email", required=True)
    reset.add_argument("--password-env")
    disable = subs.add_parser("user-disable", help="Disable an account and revoke all sessions")
    disable.add_argument("--database", required=True)
    disable.add_argument("--email", required=True)
    migrate = subs.add_parser(
        "users-import", help="Import explicit Google subjects/business IDs without passwords"
    )
    migrate.add_argument("--database", required=True)
    migrate.add_argument("--users", required=True)


def _password(args):
    if args.password_env:
        value = os.environ.get(args.password_env)
        if value is None:
            raise ValueError("Password environment variable is not set")
        return value
    value = getpass.getpass("New local password (12+ characters): ")
    if value != getpass.getpass("Confirm password: "):
        raise ValueError("Passwords do not match")
    return value


def auth_command(args):
    try:
        if args.auth_command in ("inspect", "plan"):
            root = repository(args.target)
            state = state_dir(root, create=True)
            if args.auth_command == "inspect":
                result, audit = inspect_auth(root)
                dump_json(state / "auth_inventory.json", result)
                # auth inspect is also a valid entry point: persist the same
                # immutable scan evidence required by review/auth plan.
                dump_json(state / "audit_candidates.json", audit)
                dump_json(state / "outbound_inventory.json", inventory(audit))
                dump_json(state / "review_prompt.json", build_prompt(audit))
                print(
                    f"Authentication signals: {len(result['findings'])}. Supported adapter: flask-session-v1."
                )
                print(state / "auth_inventory.json")
            else:
                plan = create_auth_plan(root, load_json(args.mapping))
                output = save_plan(root, plan)
                from ..io import atomic_write

                atomic_write(output.with_suffix(".patch"), plan["expected_diff"].encode("utf-8"))
                print(
                    f"Local auth plan: {output}. Provision users and persistent secret before starting the app."
                )
        elif args.auth_command == "user-add":
            provision_user(
                args.database,
                email=args.email,
                business_id=args.user_id,
                display_name=args.name,
                role=args.role,
                google_sub=args.google_sub,
                password=_password(args),
            )
            print("Local account provisioned; password not printed.")
        elif args.auth_command == "user-reset":
            reset_password(args.database, args.email, _password(args))
            print("Password updated; all existing sessions revoked.")
        elif args.auth_command == "user-disable":
            disable_user(args.database, args.email)
            print("Account disabled; sessions revoked.")
        else:
            records = load_json(args.users)
            if not isinstance(records, list):
                raise ValueError("User import must be a JSON array of explicit identity mappings")
            import_users(args.database, records)
            print(f"Imported {len(records)} identities. Set fresh local passwords before login.")
        return 0
    except sqlite3.Error as error:
        raise ValueError(f"Local account database operation failed: {error}") from error
