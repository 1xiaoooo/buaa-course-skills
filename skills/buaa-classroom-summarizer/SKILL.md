---
name: buaa-classroom-summarizer
description: Extract BUAA classroom replay artifacts from `livingroom` or `coursedetail` URLs by reusing a local Chromium login session. Use when Codex needs replay metadata, course-transcript files, optional PPT auxiliary artifacts, replay-ready lesson lists, or a standalone semantic rebuild packet / final lesson note.
---

# BUAA Classroom Summarizer

Use this skill for:

- one `classroom.msa.buaa.edu.cn/livingroom` replay URL
- one `classroom.msa.buaa.edu.cn/coursedetail` course page

Assume commands run from this skill root. Otherwise use the absolute path to `scripts/`.

## Core Boundary

- Let scripts handle authentication, extraction, replay diagnosis, caching, and artifact writes.
- Let the agent handle course alignment, concept confirmation, terminology correction, and final prose reconstruction.
- Treat deterministic note output as a seed unless semantic rebuild is explicitly completed.

## Main Commands

Single replay extraction:

```powershell
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>"
```

Whole-course replay enumeration or extraction:

```powershell
python scripts\collect_buaa_course_replays.py "<coursedetail-url>" --output-dir "<output-dir>"
python scripts\collect_buaa_course_replays.py "<coursedetail-url>" --output-dir "<output-dir>" --extract-existing --skip-existing
```

Whole-course commands are extraction and inventory commands, not permission to generate final notes for every available lesson. For a `coursedetail` URL, enumerate or extract artifacts first, then ask the user which lesson to semantically rebuild next unless the user explicitly requests a batch finalization workflow.

Runtime browser auth when local cookie reuse is unreliable:

```powershell
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>" --browser-runtime-auth --browser-channel "auto"
```

## Required Replay Diagnosis

Before building any note, the script must produce one `replay_diagnosis` and route the replay into exactly one of:

- `waiting_transcript`
- `partial_transcript`
- `transcript_only`

Downstream note logic must consume this diagnosis instead of recomputing route decisions elsewhere.

## Standalone Markdown Note Workflow

Prepare a semantic rebuild packet:

```powershell
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>" --export-markdown-note
```

Preferred user-facing modes:

```powershell
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>" --export-markdown-note --markdown-note-mode "final-lite"
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>" --export-markdown-note --markdown-note-mode "final-explained"
```

These modes must write only:

- `semantic_rebuild/semantic_rebuild_input.json`
- `semantic_rebuild/semantic_rebuild_prompt.md`

All final-oriented modes, including legacy `final`, must write only the semantic packet. Do not emit `lesson_note.md` by default. Treat the packet as the only intermediate artifact, then let the agent produce the final note.

Before accepting an agent-written note as final, run:

```powershell
python scripts\validate_final_note.py "<final-note.md>"
```

If the validator fails, keep the extraction artifacts and semantic packet only. Do not rename or present the failed note as a final note.

Then create a reviewer packet:

```powershell
python scripts\review_final_note.py --note "<final-note.md>" --semantic-input "<semantic_rebuild_input.json>" --output-dir "<review-dir>"
```

Use `final_note_review/final_note_review_prompt.md` with an independent reviewer agent when the environment supports subagents. In environments without subagents, run a separate reviewer pass with the same prompt. The reviewer must not edit the note during review.

When the agent writes the final standalone Markdown note:

- use a readable lesson filename, preferably the lesson title such as `2026-04-13 贝叶斯统计 第7周星期1第3,4,5节.md`
- do not name the final note `lesson_note.md`
- place final lesson Markdown notes for the same course in one course folder named by course title, for example `贝叶斯统计/2026-04-13 贝叶斯统计 第7周星期1第3,4,5节.md`
- if the chosen output directory is already named exactly as the course title, write final lesson Markdown notes directly in that directory; do not create `课程名/课程名/...`
- keep extraction artifacts such as `metadata.json`, `transcript.json`, and semantic rebuild packets in their original replay output directories; only user-facing final Markdown notes need the course-folder layout
- start directly with the lesson title and content
- do not show production metadata such as `状态`, `来源`, transcript coverage, replay diagnosis, or PPT extraction status in the user-facing note
- pass `scripts\validate_final_note.py` before the note is called final
- pass the reviewer gate for the current file hash before the note is called final

## Batch Finalization Rule

Do not turn all extracted lessons into final Markdown notes in one unattended pass. A whole-course run may produce:

- replay inventory
- extraction artifacts
- semantic rebuild packets
- a course-level todo list

A whole-course run must not produce many "formal notes" unless each lesson has received a real semantic rebuild and a quality pass. If the user asks to process all lessons so far, state that reliable finalization should be staged lesson-by-lesson or in small batches, and make intermediate outputs clearly non-final.

## Final Note Quality Gate

A user-facing final note must be a semantic reconstruction, not a decorated transcript segment list. Before writing or accepting a final note, reject it if it contains any of these patterns:

- raw ASR/OCR snippets presented as "representative expressions" or "代表性表达"
- headings such as `课堂讲解与主题推进 1`
- boilerplate like `整理时建议不要把这一段只当作...`
- repeated generic advice across sections instead of course-specific mathematical content
- transcript noise such as misrecognized symbols copied into the note without correction
- a course overview that marks low-quality diagnostics as "正式笔记"

If a note fails this gate, keep only the extraction artifacts and semantic packet, then mark the lesson as needing semantic rebuild. Do not call it final.

The semantic packet must not contain user-facing seed prose such as `seed_bullets`, raw `sample_lines`, or `transcript_excerpt`. It may contain time windows and paths to the transcript; the agent must read the transcript itself and reconstruct the note semantically.

## Reviewer Gate

Finalization requires both gates on the current Markdown bytes:

- `scripts\validate_final_note.py` passes.
- The independent reviewer returns `decision=pass`, `finalization_allowed=true`, and `reviewed_note_sha256` equal to `final_note_review_input.json` `note.sha256`.

If the note changes after either gate, both gate results are invalid and must be rerun.

Reviewer decisions:

- `pass`: the note faithfully covers the transcript, handles course-domain substance, preserves supported affairs/emphasis, and is safe to present as final.
- `needs_revision`: the transcript can support a final note, but the current note misses supported content, is too generic, or needs correction. Revise, rerun hard gate, then rerun reviewer.
- `reject`: the current source material or note is not fit for finalization. Keep extraction artifacts and semantic packet; do not present a final note.

Absence is not failure. Missing homework, exam, grading, or deadline information is only a problem when the transcript contains evidence for it and the note omits, distorts, or invents it. If the transcript shows early dismissal, in-class exercise, student presentation, discussion, or a logistics-only class, the note may be short but must faithfully describe what happened.

## Semantic Rebuild Rules

- Perform a course-alignment check before accepting the rewrite as final.
- Correct obvious ASR/OCR term errors when the course context makes the intended term clear.
- Keep the lesson time axis visible. Each final section should keep a packet time range or a coarse `MM:SS-MM:SS` marker.
- Keep math as `$...$` or `$$...$$` only. Do not wrap formulas in backticks.
- Treat the course transcript as the only primary source for section boundaries, lesson mainline, and completion checks.
- Only mark a lesson final when course-transcript coverage and summary coverage both pass.
- Reconstruct course-specific substance. Do not substitute generic learning advice for missing semantic understanding.

## Authoring Contract

When writing the final student-facing Markdown note from a semantic packet:

- You are writing the finished note, not a seed note, diagnostic note, or instruction to a future organizer.
- Read the full `transcript.txt` before writing. Use `semantic_rebuild_input.json` only as metadata, time anchors, and artifact index.
- Do not expose evidence snippets, candidate phrases, OCR fragments, raw ASR lines, or internal workflow notes.
- Every major time block should explain what teaching move happened: definition, model, argument, proof, example, comparison, case discussion, policy explanation, teacher comment, assignment, exam arrangement, or class logistics.
- Capture high-value classroom signals: exams, homework, deadlines, submission format, grading weight, reading requirements, teacher-emphasized key points, repeatedly stressed phrases, formulas, theorems, definitions, examples, and common mistakes.
- If the teacher explicitly says something is important, likely to be tested, easy to confuse, often wrong, or needs review after class, preserve it in the note.
- If transcript evidence is weak, write the item under `待核对` instead of turning it into a confident conclusion.
- The final note must face the student reader directly. Avoid phrases such as “整理时应...”, “后续重写...”, “这一段主要在...”, or other process commentary.

Course-domain reconstruction guidance:

- Math and statistics: reconstruct objects, definitions, assumptions, equations, theorems, proof ideas, examples, counterexamples, symbol meanings, and links between results.
- Engineering and computer science: reconstruct system components, algorithms, design constraints, implementation steps, experiment setup, failure cases, trade-offs, and how formulas or code relate to the design.
- Humanities and social sciences: reconstruct concepts, arguments, historical or institutional background, author positions, evidence, comparisons, cases, and the teacher's evaluative emphasis.
- Ideological and political courses: reconstruct policy concepts, theoretical claims, historical context, named documents or events, value judgments, exam-oriented formulations, and examples used to explain abstract claims.
- Language, writing, and communication courses: reconstruct vocabulary, rhetorical patterns, text structure, examples, correction points, practice requirements, and teacher feedback.
- Lab, design, or project courses: reconstruct task goals, deliverables, tools, operation steps, data requirements, safety or format constraints, grading criteria, and troubleshooting advice.

## Transcript-Only Rule

When `replay_diagnosis=transcript_only`:

- do not emit fake content templates such as “课程定位 / 基础概念 / 方法流程”
- let scripts provide only time segments from the course transcript, representative transcript lines, and `transcript_overview`
- let the agent infer the real lesson structure from the course transcript plus course context
- do not ask scripts to pre-confirm concepts from transcript-only material

## PPT Rule

- Prefer teacher stream by default.
- Treat PPT as auxiliary only, even when a PPT stream exists.
- PPT may help with term spelling, page or book titles, formula symbols, and logistics screenshots.
- PPT must not decide section boundaries, lesson mainline, concept generation, or completion state.

## Logistics-Only Teacher Review

If the user only wants follow-up on assignments, exams, notices, or arrangements:

```powershell
python scripts\extract_buaa_classroom.py "<livingroom-url>" --output-dir "<output-dir>" --export-markdown-note --lightweight-teacher-review
```

This mode prepares short teacher-stream review clips and `teacher_review.json`. It should not silently rewrite conclusions into the note until a later confirmation step marks them as confirmed.

## Failure Rules

- If the course transcript is missing, keep extraction artifacts but do not invent a formal lesson note.
- If course-transcript coverage is clearly partial, keep only a diagnostic draft rather than a final note.
- If the course transcript exists but the current summary only covers an early slice of the lesson or leaves large uncovered gaps, mark the note `needs_review` instead of final.
- If session reuse fails, rerun with `--browser-runtime-auth`.

On Windows, prefer a UTF-8 shell when validating generated files. If needed, set `[Console]::InputEncoding` and `[Console]::OutputEncoding` to UTF-8 before manual `Get-Content` or other console inspection.
