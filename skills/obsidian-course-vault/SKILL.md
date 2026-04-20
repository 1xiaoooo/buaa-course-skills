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

Use `final` only as a deterministic maintenance path. Use `draft` only for placeholders or backlog notes.

In semantic modes, scripts must write only:

- `semantic_rebuild/semantic_rebuild_input.json`
- `semantic_rebuild/semantic_rebuild_prompt.md`

Do not write a seed lesson note into the vault by default. The agent must read that packet and produce the first user-visible lesson note only after semantic rebuild completes.

## Mandatory Semantic Workflow

For semantic modes:

1. Build the semantic packet from replay artifacts plus recent course context.
2. Run a course-alignment check before accepting the note.
3. Rewrite the note semantically.
4. Mark the lesson as finished only after semantic rebuild completes.
5. Only allow a formal lesson page when transcript coverage and transcript-based summary coverage both pass.

If semantic rebuild is still pending, do not count the lesson as finished in course trackers.

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

On Windows, prefer a UTF-8 shell when validating generated files. If needed, set `[Console]::InputEncoding` and `[Console]::OutputEncoding` to UTF-8 before manual `Get-Content` or other console inspection.
