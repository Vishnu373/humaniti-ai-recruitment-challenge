"""Make R2 delete every object in the bucket 24 hours after it was created.

Sets a bucket lifecycle rule, so R2 itself does the deleting: nothing has to keep running and
it works while the Space sleeps. R2 removes expired objects within about a day of expiry, so an
object can live up to ~48 h. This covers videos, transcripts, findings and job records alike.

Usage: .venv/bin/python -m storage.r2_cleanup [show|apply|remove]   (default: show)
"""

import json
import sys

from storage import r2

EXPIRY_RULE: dict[str, object] = {
    "ID": "delete-after-24h",
    "Status": "Enabled",
    "Filter": {"Prefix": ""},  # every object
    "Expiration": {"Days": 1},
}


def without_expiry_rule(rules: list[dict[str, object]]) -> list[dict[str, object]]:
    return [r for r in rules if r.get("ID") != EXPIRY_RULE["ID"]]


def with_expiry_rule(rules: list[dict[str, object]]) -> list[dict[str, object]]:
    """Add the rule, replacing an older copy; other rules (e.g. R2's multipart abort) stay."""
    return [*without_expiry_rule(rules), EXPIRY_RULE]


def main(command: str) -> None:
    rules = r2.lifecycle_rules()
    if command == "apply":
        rules = with_expiry_rule(rules)
        r2.put_lifecycle_rules(rules)
        print("Applied: every object is deleted 1 day after it was created.")
    elif command == "remove":
        rules = without_expiry_rule(rules)
        r2.put_lifecycle_rules(rules)
        print("Removed the 24 h rule; objects are kept until deleted.")
    elif command != "show":
        sys.exit(f"Unknown command {command!r}; use show, apply or remove.")
    print(json.dumps(rules, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "show")
