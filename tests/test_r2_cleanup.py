from storage import r2_cleanup

# R2 adds a rule like this to new buckets; a lifecycle PUT replaces every rule, so it must survive.
MULTIPART_RULE: dict[str, object] = {
    "ID": "Default Multipart Abort Rule",
    "Status": "Enabled",
    "Filter": {"Prefix": ""},
    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
}


def test_expiry_rule_deletes_every_object_after_one_day() -> None:
    assert r2_cleanup.EXPIRY_RULE == {
        "ID": "delete-after-24h",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "Expiration": {"Days": 1},
    }


def test_with_expiry_rule_adds_it_and_keeps_other_rules() -> None:
    assert r2_cleanup.with_expiry_rule([MULTIPART_RULE]) == [
        MULTIPART_RULE,
        r2_cleanup.EXPIRY_RULE,
    ]


def test_with_expiry_rule_replaces_an_older_copy_instead_of_duplicating() -> None:
    old = {**r2_cleanup.EXPIRY_RULE, "Expiration": {"Days": 7}}

    assert r2_cleanup.with_expiry_rule([old, MULTIPART_RULE]) == [
        MULTIPART_RULE,
        r2_cleanup.EXPIRY_RULE,
    ]


def test_without_expiry_rule_removes_only_ours() -> None:
    rules = [MULTIPART_RULE, r2_cleanup.EXPIRY_RULE]

    assert r2_cleanup.without_expiry_rule(rules) == [MULTIPART_RULE]
