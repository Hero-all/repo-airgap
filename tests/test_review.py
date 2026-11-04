from repo_airgap.cli import main
from repo_airgap.io import load_json
from repo_airgap.review import build_inventory
from repo_airgap.scanner.auth_detector import AUTH_SIGNALS


def test_review_lists_all_and_defaults_unselected_to_keep(target):
    assert main(["scan", str(target)]) == 0
    assert main(["review", str(target)]) == 0
    state = target / ".airgap"
    inventory = load_json(state / "review_inventory.json")
    assert inventory["items"]
    assert all(item["candidate_ids"] and item["evidence"] for item in inventory["items"])
    assert not (state / "user_choices.json").exists()

    chosen = next(item for item in inventory["items"] if item["kind"] == "telemetry")
    assert main(["review", str(target), "--set", f"{chosen['id']}=DISABLE"]) == 0
    choices = load_json(state / "user_choices.json")
    actions = {item["item_id"]: item["action"] for item in choices["choices"]}
    assert actions[chosen["id"]] == "DISABLE"
    assert all(
        action == "DISABLE" if item_id == chosen["id"] else action == "KEEP"
        for item_id, action in actions.items()
    )


def test_review_refuses_source_changed_after_scan(target):
    assert main(["scan", str(target)]) == 0
    (target / "telemetry.py").write_text("# changed after scan\n", encoding="utf-8")
    assert main(["review", str(target)]) == 2


def test_generic_auth_provider_is_not_labeled_as_google(target):
    report = {
        "repo_path": str(target),
        "scan_timestamp": "t",
        "candidates": [
            {
                "id": "CAND-00001",
                "category": "AUTH_PROVIDER",
                "destination": None,
                "matched_content": "from authlib.integrations.flask_client import OAuth",
                "file_path": "app.py",
                "line_number": 1,
            }
        ],
    }
    item = build_inventory(report)["items"][0]
    assert item["provider"] == "unknown"
    assert "REPLACE_LOCAL_AUTH" not in item["allowed_actions"]


def test_common_google_auth_signals_are_detected():
    for line in (
        "from google_auth_oauthlib.flow import InstalledAppFlow",
        "passport.use(new GoogleStrategy(options, verify))",
        "const provider = new GoogleAuthProvider()",
        "import { GoogleLogin } from '@react-oauth/google'",
    ):
        assert AUTH_SIGNALS.search(line), line
