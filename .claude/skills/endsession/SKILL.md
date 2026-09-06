---
name: endsession
description: Wrap up and hand off the current Noetra work session. Use when the user types /endsession, or says "end session", "wrap up", "close out for today", or "let's stop here". Writes a concise handoff into docs/SESSION_LOG.md so the next session has context, adds a docs/LEARNING_LOG.md entry if a milestone finished, records any problem hit and solved (or design decision made) this session in docs/CONCEPTS.md, then tells the user it's safe to run /clear.
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

Get the current local date and time with `date "+%F %H:%M"`. **Append** a new entry at
the end (most recent last), in this format. Only the core — enough for a fresh session
to pick up cold:

```
## Session — YYYY-MM-DD HH:MM

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

## 4. Update `docs/CONCEPTS.md` — for problems hit and decisions made this session

This is the user's interview file, not a glossary: **no entries for concepts that were
merely explained.** Only two kinds of entry exist, and each goes at the end of its section
with the next sequential number:

- **Section A — a problem we actually hit and solved** (a bug, a wrong assumption, a
  measurement that surprised us). Format, four labelled lines:
  **Problem:** what broke or what the number said · **Why:** the real cause ·
  **Name:** what this failure is called in general, so it can be looked up ·
  **How we solved it:** the fix, including what was measured to confirm it.
- **Section B — a design decision with a real alternative** (a library, an architecture
  shape, a constant set by measurement). Format, three labelled parts:
  **Chose:** · **Alternative:** · **Why:** why ours wins *here*, and what would change
  the answer.

Keep each entry to roughly 8–15 lines, plain language, numbers where we have them. If
nothing in the session qualifies, skip this step — do not pad the file.

## 5. Confirm, then hand off

Print a 3–4 line summary: what you wrote and to which files. Then tell the user:

> Handoff saved. Run `/clear` to start the next session fresh — I can't clear my own
> context. The next session will read the latest `docs/SESSION_LOG.md` entry first.

## Notes

- Never delete or rewrite existing entries in these files — only append/insert.
- If a doc file is missing, create it (for LEARNING_LOG.md and CONCEPTS.md, add the same
  header/format block the originals use).
- Keep SESSION_LOG.md entries short. LEARNING_LOG.md can be fuller; CONCEPTS.md entries
  stay tight — they're meant to be scanned before an interview.
