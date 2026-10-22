import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from google import genai
from google.genai import types

from app.config import Settings
from app.errors import AppError
from assessment import checklist, evaluate
from assessment.dead_air import Facts, Gap
from assessment.transcript import Segment


def llm_output(drop: str | None = None, extra: str | None = None, dup: str | None = None) -> str:
    ids = [i.id for i in checklist.load() if i.id != drop]
    if extra:
        ids.append(extra)
    if dup:
        ids.append(dup)
    items: list[dict[str, object]] = [
        {"id": i, "found": False, "evidence": [], "observation": "Not seen in the class."}
        for i in ids
    ]
    return json.dumps({"items": items, "flagged_segments": []})


def test_valid_output_has_every_item() -> None:
    items, flags = evaluate.validate(llm_output())

    assert [i.id for i in items] == [i.id for i in checklist.load()]
    assert flags == []


def test_missing_id_is_rejected() -> None:
    with pytest.raises(AppError, match="missing.*gd02") as exc:
        evaluate.validate(llm_output(drop="gd02"))
    assert exc.value.code == "LLM_BAD_OUTPUT"


def test_unknown_id_is_rejected() -> None:
    with pytest.raises(AppError, match="unknown.*zz99"):
        evaluate.validate(llm_output(extra="zz99"))


def test_duplicate_id_is_rejected() -> None:
    with pytest.raises(AppError, match="duplicate.*exe01"):
        evaluate.validate(llm_output(dup="exe01"))


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(AppError) as exc:
        evaluate.validate("not json")
    assert exc.value.code == "LLM_BAD_OUTPUT"


def test_times_are_converted_from_mm_ss() -> None:
    data = json.loads(llm_output())
    data["items"][0] = {
        "id": data["items"][0]["id"],
        "found": True,
        "evidence": [{"time": "30:30", "quote": "ok, do it yourselves"}],
        "observation": "…",
    }
    data["flagged_segments"] = [{"start": "105:02", "end": "105:10", "reason": "odd word"}]

    items, flags = evaluate.validate(json.dumps(data))

    assert items[0].evidence[0].start_s == 1830
    assert (flags[0].start_s, flags[0].end_s) == (6302, 6310)


def test_bad_time_format_is_rejected() -> None:
    data = json.loads(llm_output())
    data["flagged_segments"] = [{"start": "1830", "end": "31:00", "reason": "…"}]

    with pytest.raises(AppError) as exc:
        evaluate.validate(json.dumps(data))
    assert exc.value.code == "LLM_BAD_OUTPUT"


def test_video_only_items_are_forced_to_not_found() -> None:
    data = json.loads(llm_output())
    for item in data["items"]:
        if item["id"] == "exe02":
            item.update(found=True, evidence=[{"time": "01:00", "quote": "hi"}])

    items, _ = evaluate.validate(json.dumps(data))

    exe02 = next(i for i in items if i.id == "exe02")
    assert not exe02.found
    assert exe02.evidence == []
    assert exe02.observation == evaluate.NEEDS_VIDEO_CHECK


def test_format_transcript_lines() -> None:
    segments = [
        Segment(start_s=12.4, end_s=18.9, speaker="SPEAKER_00", text="Good morning."),
        Segment(start_s=6302, end_s=6310, speaker=None, text="Bye."),
    ]

    assert evaluate.format_transcript(segments) == (
        "[00:12] SPEAKER_00: Good morning.\n[105:02] UNKNOWN: Bye."
    )


def test_format_facts_lists_gaps() -> None:
    facts = Facts(
        duration_s=3600,
        first_speech_s=65,
        dead_air_gaps=[Gap(start_s=1830, end_s=2080)],
        utilization_pct=93.25,
    )

    text = evaluate.format_facts(facts)

    assert "60:00" in text
    assert "01:05" in text
    assert "30:30–34:40" in text
    assert "93.2%" in text


class _CaptureHandler(BaseHTTPRequestHandler):
    bodies: list[dict[str, object]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _CaptureHandler.bodies.append(json.loads(self.rfile.read(length)))
        self.send_response(400)
        self.end_headers()
        self.wfile.write(b'{"error": {"message": "bad request"}}')

    def log_message(self, format: str, *args: object) -> None:
        pass


def test_request_uses_medium_thinking_and_schema_and_maps_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(
        evaluate,
        "_client",
        lambda: genai.Client(api_key="test", http_options=types.HttpOptions(base_url=base_url)),
    )
    monkeypatch.setattr(
        evaluate,
        "get_settings",
        lambda: Settings("a", "k", "s", "b", "sarvam", "test", "gemini-3.5-flash-lite"),
    )
    _CaptureHandler.bodies.clear()

    with pytest.raises(AppError) as exc:
        evaluate.evaluate(
            [], Facts(duration_s=60, first_speech_s=None, dead_air_gaps=[], utilization_pct=0)
        )
    server.shutdown()

    assert exc.value.code == "LLM_FAILED"
    body = _CaptureHandler.bodies[0]
    assert body["model"] == "gemini-3.5-flash-lite"
    assert body["generation_config"] == {"thinking_level": "medium"}
    response_format = json.dumps(body["response_format"])
    assert '"mime_type": "application/json"' in response_format
    assert '"flagged_segments"' in response_format
