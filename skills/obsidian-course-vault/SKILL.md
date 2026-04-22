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

Use `final_note_review/final_note_review_prompt.md` with an independent reviewer agent when the environment supports subagents. In environments without subagents, run a separate reviewer pass with the same prompt. The reviewer must not edit the note during review.

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

- Keep concept links visible in the note body, not only in frontmatter.
- Keep visible time references in final lesson sections.
- Keep math as `$...$` or `$$...$$` only.
- Keep graph-growth rules internal to the semantic packet. Do not expose helper rule notes as vault content.
- Keep course pages concept-centric. Lesson pages support the graph; they should not become the graph itself.
- Grow concept pages from transcript-stable concepts only. Do not let PPT or OCR noise create concept pages.
- Do not create concept pages from low-quality transcript snippets, representative expressions, or generic section labels.
- Do not add a lesson to course trackers or graph growth if `validate_final_note.py` rejects it.

On Windows, prefer a UTF-8 shell when validating generated files. If needed, set `[Console]::InputEncoding` and `[Console]::OutputEncoding` to UTF-8 before manual `Get-Content` or other console inspection.
