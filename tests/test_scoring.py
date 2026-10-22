import pytest

from assessment import checklist, scoring


def score(*found: str, duration_s: float = 45 * 60) -> scoring.Score:
    return scoring.score(set(found), duration_s)


@pytest.mark.parametrize(
    ("found", "tier"),
    [
        (("pr07", "exe01", "exe03"), "poor"),  # rule 1: red flag
        (("pr03", "pr06", "exe01", "exe03"), "poor"),  # rule 2: two Poor items
        (("avg01", "exe03", "gd02", "gd03"), "average"),  # rule 3
        (("pr06", "exe01", "exe03"), "average"),  # rule 4: one Poor item caps
        (("exe01", "exe03", "gd01"), "excellent"),  # rule 5
        (("exe03", "gd02", "gd03"), "good"),  # rule 6
        (("exe03", "gd01"), "average"),  # rule 7: no threshold met
        ((), "average"),
    ],
)
def test_ladder_matches_scoring_logic_examples(found: tuple[str, ...], tier: str) -> None:
    assert score(*found).tier == tier


def test_extras_do_not_decide_the_tier() -> None:
    assert score("avg05", "exe03", "gd02", "gd03").tier == "good"
    assert score("exe01", "exe04", "exe06", "exe07", "gd04").tier == "average"


def test_reason_names_the_rule_and_items() -> None:
    result = score("pr02", "pr07")

    assert result.reason == "Red flag found: pr02, pr07"


def test_short_class_is_provisional() -> None:
    assert score("exe03", "gd01", "gd02", duration_s=4 * 60).provisional
    assert not score("exe03", "gd01", "gd02", duration_s=5 * 60).provisional


def test_rule_ids_exist_in_the_checklist() -> None:
    ids = {i.id for i in checklist.load()}

    assert set(scoring.RED_FLAGS) | set(scoring.EXCELLENT_REQUIRED) <= ids


def test_unknown_found_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="xyz99"):
        score("xyz99")
