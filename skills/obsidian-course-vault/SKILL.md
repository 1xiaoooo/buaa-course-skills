---
name: obsidian-course-vault
description: Build and maintain a semester-long Obsidian course vault with course overviews, lesson notes, concept pages, graph hubs, replay-sync trackers, and replay-to-note workflows. Use when Codex needs to manage course knowledge in Obsidian, especially when BUAA replay artifacts should become structured lesson notes rather than raw transcripts.
---

# Obsidian Course Vault

Use this skill when the user wants long-term course notes in Obsidian.

Assume commands run from this skill root. Otherwise use the absolute path to `scripts/`.

## Core Boundary

- Let scripts handle vault structure, replay sync, replay diagnosis, cache refresh, tracker maintenance, and file writes.
- Let the agent handle course alignment, concept confirmation, terminology correction, graph interpretation, and final note prose.
- Do not let deterministic seed notes masquerade as finished course notes.

## Main Commands

Initialize the vault:

```powershell
python scripts\init_obsidian_course_vault.py --obsidian-dir "<obsidian-install-dir>" --vault-dir "<vault-dir>"
```

Add a course:

```powershell
python scripts\add_course.py --vault-dir "<vault-dir>" --course-name "<course-name>"
```

Maintain a course or sync BUAA replay state:

```powershell
python scripts\maintain_obsidian_course.py --vault-dir "<vault-dir>" --course-name "<course-name>" --course-page-url "<coursedetail-url>" --replay-output-dir "<replay-output-dir>"
```

## Course Identity And Placement

Before adding or maintaining a BUAA course in a vault:

- Resolve `course-name` from the extracted course title whenever possible. The normalized title is the course identity.
- If the resolved title already exists in the vault, reuse that course folder. Same title means same course, even if the new `coursedetail` URL has a different `course_id`, lecturer, schedule, classroom, or sub_id range.
- If the title is missing or unreliable, use the `course_id` only as a provisional course name such as `course-136278`, or ask the user for the intended title before writing formal notes. Do not infer the course from existing vault folders, teacher names, class times, or old extraction directories.
- Interpret "use the previous vault" as selecting the vault root, not automatically selecting an existing course folder inside it.
- Keep each source `course_id` and URL in sync metadata so multiple classroom sources can feed the same titled course without overwriting provenance.

Build or rebuild one replay note:

```powershell
python scripts\maintain_obsidian_course.py --vault-dir "<vault-dir>" --course-name "<course-name>" --draft-replay-sub-ids "<sub-id>" --replay-note-mode "final-explained"
```

## Required Replay Diagnosis

Before any replay note is built, scripts must compute one `replay_diagnosis` and route the lesson into exactly one of:

- `waiting_transcript`
- `partial_transcript`
- `transcript_only`

All downstream note generation should consume this diagnosis instead of recomputing route decisions independently.

## Recommended Replay Modes

Treat these as the normal user-facing modes:

- `final-lite`
- `final-explained`

Legacy `final` is treated as a semantic-packet preparation path, not permission to write final prose directly. Use `draft` only for placeholders or backlog notes.

In semantic modes, scripts must write only:

- `semantic_rebuild/semantic_rebuild_input.json`
- `semantic_rebuild/semantic_rebuild_prompt.md`

Do not write a seed lesson note into the vault by default. The agent must read that packet and produce the first user-visible lesson note only after semantic rebuild completes.

Before a rebuilt note is counted as a formal lesson page, run:

```powershell
python scripts\validate_final_note.py "<lesson-note.md>"
```

If the validator fails, mark the note as `needs_review` / quality rejected. Do not include it in course trackers, overview completion counts, or graph growth.

Then create a reviewer packet:

```powershell
python scripts\review_final_note.py --note "<lesson-note.md>" --semantic-input "<semantic_rebuild_input.json>" --output-dir "<review-dir>"
```

Use `final_note_review/final_note_review_prompt.md` with an independent reviewer agent only when the active system/developer instructions allow spawning one. If subagents are unavailable or not allowed, run a separate reviewer pass yourself with the same prompt, write the result as `final_note_review/final_note_review_result.json`, and do not edit the note during review.

## Efficient Batch Workflow

When the user explicitly asks to organize all pending BUAA replays for a course:

1. Reuse the existing replay extraction directory and semantic packets when present.
2. Build any missing semantic packets first; do not rerun browser extraction for lessons that already have current `transcript.txt` and `semantic_rebuild_input.json`.
3. Filter candidates before writing: skip finalized lessons, skip future lessons, and keep missing/empty/near-empty transcripts in waiting/backlog.
4. For each eligible lesson, read the full transcript, write the formal note, run validation, create the review packet, and record a passing review for the current note hash.
5. Run `maintain_obsidian_course.py` once after the batch to refresh overview, trackers, backlog, and sync notes. Run it earlier only when you need a checkpoint or need it to create missing directories/packets.
6. Do not regenerate concept pages from weak transcript-only hints during the batch; defer graph growth to transcript-stable concepts and the normal maintenance pass.

This is a batching optimization, not a relaxation of the semantic gates.

## Mandatory Semantic Workflow

For semantic modes:

1. Build the semantic packet from replay artifacts plus recent course context.
2. Run a course-alignment check before accepting the note.
3. Rewrite the note semantically.
4. Run `scripts\validate_final_note.py` on the rewritten Markdown.
5. Generate a reviewer packet with `scripts\review_final_note.py`.
6. Run an independent reviewer pass against the current note hash.
7. Mark the lesson as finished only after semantic rebuild completes, hard gate passes, and reviewer returns `pass`.
8. Only allow a formal lesson page when transcript coverage and transcript-based summary coverage both pass.

If semantic rebuild is still pending, do not count the lesson as finished in course trackers.

For a formal Obsidian replay note, the frontmatter must include at least:

- `type: lesson`
- `course`
- `title`
- `date`
- `replay_sub_id`
- `source: buaa-replay-semantic-rebuild`
- `replay_diagnosis`
- `has_semantic_rebuild_packet: true`
- `semantic_rebuild_completed: true`
- `semantic_rebuild_status: completed`
- `concepts`

Without these fields, maintenance may keep the lesson in pending semantic rebuild or exclude it from `已整理课次.md`.

## Course Affairs Maintenance

Treat course affairs as a first-class output, not incidental prose.

When a transcript contains supported logistics, write them in the lesson note under `## 课程事务` with these categories when applicable:

- `### 作业`
- `### 考试`
- `### 课程安排`
- `### 通知`

Existing notes may use `## 课堂事务`; maintenance treats it as the same rollup source. Prefer `## 课程事务` for newly written notes so the structure is explicit.

Only write transcript-supported affairs as confident bullets. Put uncertain due dates, weights, submission formats, exam scope, or policy details under `待核对` instead of promoting them to a firm affair.

During maintenance, scripts may refresh:

- course-internal `.course-internal/affairs-candidates.md` from finished lesson notes
- course-level `事务.md` only when it is still an unreviewed auto-generated placeholder
- vault-level `03-Admin/作业总表.md` and `03-Admin/考试与通知.md` only after an agent affairs review has condensed the candidates

Course-level affairs are a reviewed digest, not a keyword dump. Keep only items that change what a student should do, check, submit, read, attend, or expect in assessment. Compress repeated or vague mentions into one short entry per date. Exclude ordinary teaching content, general encouragement, study advice without a concrete deliverable, concept-review suggestions, and broad course narration. `课程安排` may remain inside lesson notes for local context, but do not roll it up to course-level `事务.md` unless it contains a concrete schedule/location/session change that belongs under `通知`.

Do not let keyword extraction write final affairs directly. Use this flow: generate `.course-internal/affairs-candidates.md`; run an agent affairs review in the main agent or an allowed independent reviewer; then write concise reviewed entries into `事务.md` and the Admin tables. Do not require human review, and do not overwrite an agent-reviewed `事务.md` during routine maintenance.

Do not expose affairs candidates in user-facing notes. Do not link `.course-internal/affairs-candidates.md` from `事务.md`, `00-课程总览.md`, trackers, or Admin pages.

Agent affairs review must explicitly reject or merge:

- ordinary teaching content or concept-review suggestions
- general encouragement, learning methods, or motivational remarks
- repeated mentions of the same assignment/exam/notice
- vague “maybe useful for homework” notes without a concrete deliverable
- exam-like keyword hits caused only by words such as `分数`, `分类`, or model scores

Do not roll up affairs from waiting transcript notes, partial transcript notes, quality rejected notes, or notes still pending semantic rebuild. Placeholder sentences such as “当前未从转写中识别出稳定...” are not affairs and must not appear in `事务.md` or the Admin tables.

## Final Note Quality Gate

Do not write or count a lesson as finished if the note is only a decorated transcript segment list. Reject the note and keep it as `needs_review` if it contains:

- raw ASR/OCR snippets presented as "代表性表达" or representative lines
- headings such as `课堂讲解与主题推进 1`
- repeated generic advice like `整理时建议不要把这一段只当作...`
- section bodies that could fit almost any course
- misrecognized mathematical symbols copied into final prose without correction
- a course tracker or overview marking diagnostics or weak drafts as formal notes

For math-heavy courses, the final note must reconstruct concrete mathematical objects, assumptions, equations, proof ideas, examples, and their relationships. If the agent cannot do that from the transcript, write a review-gated draft rather than a formal lesson page.

The semantic packet must not contain user-facing seed prose such as `seed_bullets`, raw `sample_lines`, or `transcript_excerpt`. It may contain time windows and paths to the transcript; the agent must read the transcript itself and reconstruct the note semantically.

## Reviewer Gate

Finalization requires both gates on the current Markdown bytes:

- `scripts\validate_final_note.py` passes.
- The independent reviewer returns `decision=pass`, `finalization_allowed=true`, and `reviewed_note_sha256` equal to `final_note_review_input.json` `note.sha256`.

If the note changes after either gate, both gate results are invalid and must be rerun. Do not update course overview, trackers, or graph growth from an outdated review.

Reviewer implementation detail:

- If subagents are permitted, use an independent reviewer agent.
- If subagents are not permitted by active instructions, run a separate reviewer pass in the main agent, write `final_note_review_result.json`, and ensure `reviewed_note_sha256` matches `final_note_review_input.json`.
- Do not rerun review for an unchanged note when an existing `final_note_review_result.json` already passes for the same hash.

Reviewer decisions:

- `pass`: the note faithfully covers the transcript, handles course-domain substance, preserves supported affairs/emphasis, and is safe to present as final.
- `needs_revision`: the transcript can support a final note, but the current note misses supported content, is too generic, or needs correction. Revise, rerun hard gate, then rerun reviewer.
- `reject`: the current source material or note is not fit for finalization. Keep extraction artifacts and semantic packet; do not present a final note or grow the graph.

Absence is not failure. Missing homework, exam, grading, or deadline information is only a problem when the transcript contains evidence for it and the note omits, distorts, or invents it. If the transcript shows early dismissal, in-class exercise, student presentation, discussion, or a logistics-only class, the note may be short but must faithfully describe what happened.

## Authoring Contract

When writing the formal Obsidian lesson note from a semantic packet:

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

## Course Alignment Rules

Before accepting a semantic rewrite, judge whether the replay interpretation is:

- `match`
- `weak_match`
- `mismatch`

Use at least:

- the declared course name
- recent lesson notes for the same course
- existing concept pages and chapter hubs
- the current replay transcript and semantic packet

Course alignment checks content fit, not administrative sameness. A different lecturer, weekday, section time, classroom, or `course_id` is not a mismatch when the confirmed course title is the same. When the title is unavailable, keep alignment provisional and avoid updating formal trackers until the course identity is confirmed.

If the result is `mismatch`, keep only seed artifacts or a draft and do not write a formal final lesson note.

## Transcript-Only Rule

When `replay_diagnosis=transcript_only`:

- do not produce fake generic headings
- let scripts provide only time segments, representative transcript lines, and `transcript_overview` from the course transcript
- let the agent infer the real teaching structure from the course transcript plus course context
- do not ask scripts to pre-confirm transcript-only concepts or create concept pages from weak transcript hints
- do not let seed notes count as finished notes by default

## PPT Rule

- Treat PPT as supplementary only.
- Do not require PPT to proceed with final rebuild.
- PPT may only help with term spelling, page or book titles, formula symbols, and logistics screenshots.
- PPT must not decide lesson structure, concept growth, or completion state.

## Waiting and Partial Transcript Rules

- `waiting_transcript`: create only a waiting placeholder. Do not invent a summary.
- Empty or near-empty `transcript.txt` counts as waiting material even if a tracker currently lists the replay under backlog instead of `waiting_transcript`.
- `partial_transcript`: create only a diagnostic draft. Do not treat it as a final lesson note.
- `needs_review`: create a review-gated note when the course transcript exists but the current transcript-based summary still leaves large uncovered ranges.

## Upgraded Source Review

If the platform later adds stronger replay materials such as PPT streams, `ppt_outline`, or fuller transcripts, surface that lesson in `回放同步.md` as a review candidate.

Semi-automatic rebuild:

```powershell
python scripts\maintain_obsidian_course.py --vault-dir "<vault-dir>" --course-name "<course-name>" --rebuild-upgraded-replays --replay-note-mode "final-explained"
```

Protect lessons already marked as semantic rebuild completions from silent overwrite.

## Output Rules

- Public outputs are only finished products: `00-课程总览.md`, `事务.md`, `章节完成度.md`, `已整理课次.md`, `待回看问题.md`, `回放同步.md`, `待整理回放.md`, `03-Admin/*.md`, formal lesson notes, and formal concept pages.
- Internal artifacts are only for workflow use: `.course-internal/*`, `semantic_rebuild/*`, `final_note_review/*`, draft packets, reviewer packets, and other diagnostic notes.
- Never link internal artifacts from public outputs. User-facing placeholder text must also avoid process language such as "agent review", "candidate", or "semantic rebuild".
- Ensure Obsidian ignores internal workflow paths such as `.course-internal`, `semantic_rebuild`, and `final_note_review` before writing candidate or review artifacts inside a vault.
- Keep concept links visible in the note body, not only in frontmatter.
- Keep visible time references in final lesson sections. Time references are a hard gate: write replay-locatable timestamp ranges such as `时间参考：约 \`03:29-18:58\`` or, after the first hour, `时间参考：约 \`01:05:31-01:23:21\``. Never write `01:20-01:39` to mean the 80th to 99th classroom minute; after one hour, use `HH:MM:SS`. Time ranges must be monotone in note order and should be long enough to represent a real major lesson section.
- Keep math as `$...$` or `$$...$$` only.
- Keep graph-growth rules internal to the semantic packet. Do not expose helper rule notes as vault content.
- Keep course pages concept-centric. Lesson pages support the graph; they should not become the graph itself.
- Grow concept pages from transcript-stable concepts only. Do not let PPT or OCR noise create concept pages.
- Do not create concept pages from low-quality transcript snippets, representative expressions, or generic section labels.
- Do not add a lesson to course trackers or graph growth if `validate_final_note.py` rejects it.

On Windows, prefer a UTF-8 shell when validating generated files. If needed, set `[Console]::InputEncoding` and `[Console]::OutputEncoding` to UTF-8 before manual `Get-Content` or other console inspection.

For inline Python in PowerShell, use:

```powershell
@'
print("hello")
'@ | python -
```

Do not use Bash heredoc syntax such as `python - <<'PY'` in PowerShell.
