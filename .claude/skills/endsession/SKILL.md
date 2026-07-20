---
name: endsession
description: Wrap up and hand off the current Noetra work session. Use when the user types /endsession, or says "end session", "wrap up", "close out for today", or "let's stop here". Writes a concise handoff into docs/SESSION_LOG.md so the next session has context, adds a docs/LEARNING_LOG.md entry if a milestone finished, records anything new-to-the-user in docs/CONCEPTS.md, then tells the user it's safe to run /clear.
allowed-tools: Read, Edit, Write, Bash
---

# End Session — Noetra handoff

Run the steps below in order. Keep everything **concise** — this is a handoff for a
future session, not an essay. Draft from the conversation yourself; don't interrogate
the user with a pile of questions. Show a short summary at the end and let them correct.

## 1. Gather what happened this session

From the conversation, note: files created/changed, what now works, what's half-done,
decisions made (and the one-line why), any blockers, and the obvious next step.

## 2. Update `docs/SESSION_LOG.md` (create it if missing)

Get today's date with `date +%F`. **Append** a new entry at the end (most recent last),
in this format. Only the core — enough for a fresh session to pick up cold:

```
## Session — YYYY-MM-DD

**Worked on:** milestone / feature.
**Done:** what now works (bullets).
**In progress:** what's started but unfinished, and where it stands.
**Key decisions:** decision → one-line why.
**Next step:** the very next thing to do.
**Watch out for:** any gotcha/blocker the next session should know.
```

## 3. Update `docs/LEARNING_LOG.md` — only if a milestone completed this session

This file is interview/recruiter-facing. Follow the format already defined at the top of
that file (Built / Core concept(s) / Recruiter-ready explanation / Tricky part). Insert
the new entry **above** the `<!-- Add new entries above this line -->` marker.
**If no milestone finished, skip this step — do not invent one.**

## 4. Update `docs/CONCEPTS.md` — for anything that was new to the user this session

This is the user's personal glossary. For each concept that was new to them (the
auth / infra / retrieval / distributed-systems kind of thing that CLAUDE.md says to
explain), add an entry in that file's existing format:
**What it is / Why we need it here / How it's used in Noetra / Is this standard? /
Docs (official link).** Plain language, with a concrete example. If nothing new came
up, skip this step.

## 5. Confirm, then hand off

Print a 3–4 line summary: what you wrote and to which files. Then tell the user:

> Handoff saved. Run `/clear` to start the next session fresh — I can't clear my own
> context. The next session will read the latest `docs/SESSION_LOG.md` entry first.

## Notes

- Never delete or rewrite existing entries in these files — only append/insert.
- If a doc file is missing, create it (for LEARNING_LOG.md and CONCEPTS.md, add the same
  header/format block the originals use).
- Keep SESSION_LOG.md entries short. LEARNING_LOG.md and CONCEPTS.md can be fuller, but
  still plain-language.
