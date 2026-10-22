"""Gradio page: file upload (browser → R2 presigned PUT) or Drive link, then a pushed job status
and, once the job is COMPLETE, the full report."""

import html
from collections.abc import Iterator

import gradio as gr

from app.errors import AppError
from assessment import checklist
from assessment.evaluate import Evidence, mm_ss
from storage import jobs
from storage.jobs import Job, JobStatus, Report

FILE_INPUT_HTML = """
<input type="file" id="video-file" accept=".mp4,.mkv,.webm,.mov">
<progress id="upload-bar" value="0" max="100" style="width:100%;display:none"></progress>
<div id="upload-progress"></div>
"""

# Runs in the browser so the video goes straight to R2 and never through this server.
UPLOAD_JS = """
async () => {
  const say = (msg) => { document.getElementById('upload-progress').textContent = msg; };
  const errorText = async (res) => {
    try { const b = await res.json(); return `${b.error.code}: ${b.error.message}`; }
    catch { return `HTTP ${res.status}`; }
  };
  const bar = document.getElementById('upload-bar');
  bar.style.display = 'none';
  const file = document.getElementById('video-file').files[0];
  if (!file) { say('Choose a video file first.'); return ''; }

  const res = await fetch('/api/upload-url', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename: file.name, size: file.size}),
  });
  if (!res.ok) { say(await errorText(res)); return ''; }
  const {job_id, upload_url, content_type} = await res.json();

  const putOk = await new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', upload_url);
    xhr.setRequestHeader('Content-Type', content_type);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round(100 * e.loaded / e.total);
      bar.style.display = 'block';
      bar.value = pct;
      say(`Uploading… ${pct}%`);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { resolve(true); }
      else { say(`Upload to storage failed: HTTP ${xhr.status}`); resolve(false); }
    };
    xhr.onerror = () => { say('Upload to storage failed: network error or CORS'); resolve(false); };
    xhr.send(file);
  });
  if (!putOk) return job_id;

  const done = await fetch(`/api/jobs/${job_id}/uploaded`, {method: 'POST'});
  say(done.ok ? 'Upload complete.' : await errorText(done));
  return job_id;
}
"""

DRIVE_JS = """
async (url) => {
  const res = await fetch('/api/drive', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url}),
  });
  const body = await res.json();
  if (!res.ok) { alert(`${body.error.code}: ${body.error.message}`); return ''; }
  return body.job_id;
}
"""


# Pipeline step (shown as a progress bar) and label for every status except FAILED.
STEPS: dict[JobStatus, tuple[int, str]] = {
    "UPLOADING": (0, "Uploading"),
    "EXTRACTING": (1, "Extracting audio"),
    "TRANSCRIBING": (2, "Transcribing"),
    "EVALUATING": (3, "Evaluating with Gemini"),
    "SCORE_APPLYING": (4, "Applying the score"),
    "COMPLETE": (5, "Done"),
}
LAST_STEP = max(step for step, _ in STEPS.values())


def render_status(job: Job) -> str:
    if job.error is not None or job.status == "FAILED":
        err = job.error["error"] if job.error is not None else None
        detail = f"{err['code']}: {err['message']}" if err is not None else "unknown error"
        return f"<p>Failed: <b>{html.escape(detail)}</b></p>"
    step, label = STEPS[job.status]
    bar = (
        f'<progress value="{step}" max="{LAST_STEP}" style="width:100%"></progress>'
        f"<p>Step {step} of {LAST_STEP}: {label}</p>"
    )
    if job.score is None:
        return bar
    grade = job.score.tier.capitalize()
    if job.score.provisional:  # under 5 minutes: an excerpt or demo
        grade = f"On Track for {grade}"
    return f"{bar}<p>Grade: <b>{grade}</b> ({html.escape(job.score.reason)})</p>"


def _quote(e: Evidence) -> str:
    return f"[{mm_ss(e.start_s)}] “{html.escape(e.quote)}”"


def render_report(report: Report) -> str:
    """Every checklist item by tier (✓ found / ✗ not), then class facts and flagged lines."""
    findings = {f.id: f for f in report.items}
    parts = ["<h2>Checklist</h2>"]
    for tier in checklist.TIERS:
        parts.append(f"<h3>{tier.capitalize()}</h3><ul>")
        for item in (i for i in checklist.load() if i.tier == tier):
            f = findings[item.id]
            quotes = "".join(f"<li>{_quote(e)}</li>" for e in f.evidence)
            parts.append(
                f"<li>{'✓' if f.found else '✗'} <code>{item.id}</code> "
                f"<b>{html.escape(item.title)}</b><br>{html.escape(f.observation)}"
                + (f"<ul>{quotes}</ul>" if quotes else "")
                + "</li>"
            )
        parts.append("</ul>")

    facts = report.facts
    gaps = ", ".join(
        f"{mm_ss(g.start_s)}–{mm_ss(g.end_s)} ({g.duration_s / 60:.1f} min)"
        for g in facts.dead_air_gaps
    )
    first = mm_ss(facts.first_speech_s) if facts.first_speech_s is not None else "no speech"
    parts.append(
        "<h2>Class facts</h2><ul>"
        f"<li>Duration: {mm_ss(facts.duration_s)}</li>"
        f"<li>First speech: {first}</li>"
        f"<li>Time utilization: {facts.utilization_pct:.1f}%</li>"
        f"<li>Dead air (≥ 3 min): {gaps or 'none'}</li></ul>"
    )

    flags = "".join(
        f"<li>{mm_ss(s.start_s)}–{mm_ss(s.end_s)}: {html.escape(s.reason)}</li>"
        for s in report.flagged_segments
    )
    parts.append(
        "<h2>Flagged lines (possible mis-hearings)</h2>"
        + (f"<ul>{flags}</ul>" if flags else "<p>No lines flagged.</p>")
    )
    return "".join(parts)


def watch_status(job_id: str) -> Iterator[str]:
    """Stream the status: one render per status change pushed by the pipeline, no polling."""
    if not job_id:
        yield ""
        return
    try:
        for job in jobs.watch(job_id):
            # COMPLETE is the last status watch yields, so the report is read from R2 once.
            report = render_report(jobs.load_report(job)) if job.status == "COMPLETE" else ""
            yield render_status(job) + report
    except AppError as e:
        yield f"<p><b>{e.code}</b>: {html.escape(e.message)}</p>"


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Class Recording Quality Assessor") as demo:
        gr.Markdown("# Class Recording Quality Assessor")
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Upload a recording (≤ 500 MB)")
                gr.HTML(FILE_INPUT_HTML, js_on_load=None)
                upload_btn = gr.Button("Upload")
            with gr.Column():
                gr.Markdown("### Or a public Google Drive link")
                drive_url = gr.Textbox(
                    label="Drive link", placeholder="https://drive.google.com/file/d/…"
                )
                drive_btn = gr.Button("Submit link")

        job_id = gr.Textbox(label="Job ID", interactive=False)
        status = gr.HTML(js_on_load=None)

        upload_btn.click(fn=None, js=UPLOAD_JS, outputs=job_id)
        drive_btn.click(fn=None, js=DRIVE_JS, inputs=drive_url, outputs=job_id)
        # Each watcher waits on the notifier, so it must not take Gradio's single default slot.
        job_id.change(fn=watch_status, inputs=job_id, outputs=status, concurrency_limit=None)
    return demo
