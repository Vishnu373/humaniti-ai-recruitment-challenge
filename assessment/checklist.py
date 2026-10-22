"""Checklist items parsed from docs/class_quality_checklist.md, the only copy of the item IDs."""

import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Literal, get_args

Tier = Literal["excellent", "good", "average", "poor"]
TIERS: tuple[Tier, ...] = get_args(Tier)

CHECKLIST_PATH = Path(__file__).resolve().parent.parent / "docs" / "class_quality_checklist.md"

_TIER_HEADING = re.compile(r"^## \d+\. (\w+)")
# * **`exe03` Title:** description **[Transcript]**  (the source tag may be missing)
_ITEM = re.compile(r"^\* \*\*`(\w+)` (.*?)\*\*\s*(.*?)\s*(?:\*\*\[([^\]]+)\]\*\*)?\s*$")
# An untagged item (e.g. exe01's 4-stage flow) is judged from the transcript.
DEFAULT_SOURCES = ("Transcript",)


@dataclass(frozen=True)
class Item:
    id: str
    tier: Tier
    main: bool  # "Observed Points" (main) vs "Extra Classification Criteria"
    text: str
    sources: tuple[str, ...]  # e.g. ("Transcript", "Video")

    @property
    def title(self) -> str:
        """The bold title that `parse` puts at the start of `text`."""
        return self.text.split("**")[1]

    @property
    def video_only(self) -> bool:
        return self.sources == ("Video",)


def parse(markdown: str) -> list[Item]:
    items: list[Item] = []
    tier: Tier | None = None
    main = True
    in_item = False  # indented lines right after an item continue its text
    for line in markdown.splitlines():
        if in_item and line.startswith((" ", "\t")) and line.strip():
            last = items[-1]
            items[-1] = replace(last, text=f"{last.text} {line.strip()}")
            continue
        in_item = False
        if heading := _TIER_HEADING.match(line):
            name = heading.group(1).lower()
            if name not in TIERS:
                raise ValueError(f"Unknown tier heading in checklist: {line!r}")
            tier = next(t for t in TIERS if t == name)
            main = True
        elif line.startswith("## "):
            tier = None  # a non-tier section, e.g. the demo evaluation protocol
        elif line.startswith("### Primary"):
            main = True
        elif line.startswith("### Extra"):
            main = False
        elif item := _ITEM.match(line):
            if tier is None:
                raise ValueError(f"Checklist item outside a tier section: {line!r}")
            title, description, tag = item.group(2), item.group(3), item.group(4)
            text = f"**{title}** {description}".strip()
            sources = tuple(s.strip() for s in tag.split("/")) if tag else DEFAULT_SOURCES
            items.append(Item(item.group(1), tier, main, text, sources))
            in_item = True
    return items


@cache
def markdown() -> str:
    return CHECKLIST_PATH.read_text(encoding="utf-8")


@cache
def load() -> list[Item]:
    return parse(markdown())
