from assessment import checklist, evidence
from assessment.dead_air import Facts, Gap
from assessment.evaluate import Evidence, Finding
from assessment.transcript import Segment

SEGMENTS = [
    Segment(start_s=1825, end_s=1832, speaker="SPEAKER_00", text="OK, do it yourselves,"),
    Segment(start_s=1832, end_s=1836, speaker="SPEAKER_00", text="I'll be back."),
    Segment(start_s=2400, end_s=2405, speaker="SPEAKER_01", text="Sir, I have a doubt."),
]

FACTS = Facts(duration_s=3600, first_speech_s=10, dead_air_gaps=[], utilization_pct=90)


def finding(*quotes: tuple[float, str], item_id: str = "gd02") -> Finding:
    return Finding(
        id=item_id,
        found=True,
        evidence=[Evidence(start_s=t, quote=q) for t, q in quotes],
        observation="Teacher left the class at 30:30.",
    )


def test_keeps_exact_quote_ignoring_case_and_whitespace() -> None:
    items, rejected = evidence.check([finding((1830, "ok, do it   YOURSELVES"))], SEGMENTS, FACTS)

    assert items[0].found
    assert len(items[0].evidence) == 1
    assert rejected == []


def test_quote_spanning_two_segments_is_kept() -> None:
    items, _ = evidence.check([finding((1830, "do it yourselves, I'll be back"))], SEGMENTS, FACTS)

    assert items[0].found


def test_quote_without_filler_word_or_punctuation_is_kept() -> None:
    # Real case: Gemini dropped "uh," from the transcript line.
    segments = [
        Segment(
            start_s=1730.9,
            end_s=1738.7,
            speaker="7",
            text="Very good. Then we can go and capture the rook. Then this one, uh, Avan, "
            "can you try to find the moves?",
        )
    ]

    items, rejected = evidence.check(
        [finding((1738, "Then this one,  Avan, can you try to find the moves"))], segments, FACTS
    )

    assert items[0].found
    assert rejected == []


def test_made_up_quote_is_dropped_and_item_not_found() -> None:
    items, rejected = evidence.check([finding((1830, "everyone keep quiet"))], SEGMENTS, FACTS)

    assert not items[0].found
    assert items[0].evidence == []
    assert [(r.id, r.quote) for r in rejected] == [("gd02", "everyone keep quiet")]


def test_quote_two_minutes_off_is_dropped() -> None:
    items, rejected = evidence.check([finding((1830 + 120, "I'll be back"))], SEGMENTS, FACTS)

    assert not items[0].found
    assert len(rejected) == 1


def test_item_keeps_found_if_one_quote_survives() -> None:
    items, rejected = evidence.check(
        [finding((1830, "I'll be back"), (1830, "made up words"))], SEGMENTS, FACTS
    )

    assert items[0].found
    assert [e.quote for e in items[0].evidence] == ["I'll be back"]
    assert len(rejected) == 1


def test_gd04_found_on_facts_even_when_quote_cites_the_facts() -> None:
    # Real case: Gemini quoted the facts block, which is not in the transcript.
    facts = FACTS.model_copy(update={"utilization_pct": 98.5})

    items, rejected = evidence.check(
        [finding((3231, "Recording duration: 53:51"), item_id="gd04")], SEGMENTS, facts
    )

    assert items[0].found
    assert items[0].evidence == []
    assert len(rejected) == 1


def test_gd04_not_found_when_utilization_below_95() -> None:
    items, _ = evidence.check([finding((1830, "I'll be back"), item_id="gd04")], SEGMENTS, FACTS)

    assert not items[0].found


def test_avg02_needs_a_dead_air_gap() -> None:
    items, _ = evidence.check([finding((1830, "I'll be back"), item_id="avg02")], SEGMENTS, FACTS)

    assert not items[0].found


def test_avg02_found_with_gap_and_no_surviving_quote() -> None:
    facts = FACTS.model_copy(update={"dead_air_gaps": [Gap(start_s=1840, end_s=2100)]})

    items, _ = evidence.check([finding(item_id="avg02")], SEGMENTS, facts)

    assert items[0].found


def test_not_found_items_stay_not_found() -> None:
    facts = FACTS.model_copy(update={"utilization_pct": 99})
    item = finding(item_id="gd04").model_copy(update={"found": False})

    items, _ = evidence.check([item], SEGMENTS, facts)

    assert not items[0].found


def test_fact_gated_items_exist_in_checklist() -> None:
    assert set(evidence.FACT_GATES) <= {i.id for i in checklist.load()}
