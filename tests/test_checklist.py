from assessment import checklist


def test_parses_all_23_items_from_docs() -> None:
    items = checklist.load()

    assert len(items) == 23
    assert len({i.id for i in items}) == 23


def test_tier_counts_match_scoring_logic() -> None:
    items = checklist.load()

    counts = {tier: sum(1 for i in items if i.tier == tier) for tier in checklist.TIERS}
    assert counts == {"excellent": 7, "good": 4, "average": 5, "poor": 7}
    assert [i.id for i in items if i.tier == "excellent" and i.main] == [
        "exe01",
        "exe02",
        "exe03",
        "exe04",
    ]
    assert [i.id for i in items if i.tier == "good" and not i.main] == ["gd04"]


def test_video_only_items_come_from_source_tags() -> None:
    assert [i.id for i in checklist.load() if i.video_only] == ["exe02", "exe05", "pr01"]


def test_parse_reads_text_and_sources() -> None:
    markdown = """
## 1. Excellent

### Primary Observed Points

* **`exe01` Complete Flow:**
  1. *Check-in:* Icebreaker.
  2. *Warm-up:* Puzzle.

---

## 3. Average

### Primary Observed Points

* **`avg01` Media Dependency:** Relies on videos. **[Transcript / Video]**

### Extra Classification Criteria

* **`avg05` Monopolized Attention:** Allows dominant students. **[Transcript]**

---

## Excerpt & Demo Evaluation Protocol

1. **Duration Check:** Not an item.
"""
    items = checklist.parse(markdown)

    assert items == [
        checklist.Item(
            id="exe01",
            tier="excellent",
            main=True,
            text="**Complete Flow:** 1. *Check-in:* Icebreaker. 2. *Warm-up:* Puzzle.",
            sources=("Transcript",),
        ),
        checklist.Item(
            id="avg01",
            tier="average",
            main=True,
            text="**Media Dependency:** Relies on videos.",
            sources=("Transcript", "Video"),
        ),
        checklist.Item(
            id="avg05",
            tier="average",
            main=False,
            text="**Monopolized Attention:** Allows dominant students.",
            sources=("Transcript",),
        ),
    ]
