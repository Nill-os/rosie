---
name: "job-fit-tailor"
description: "Use when given a job posting (and the user's resume) to score fit A–F, list gaps honestly, and produce a tailored, truthful resume version."
---

# Job fit and resume tailoring

The user brings the posting. Never apply for a job for them.

## Inputs
- The job posting (pasted text or a link to read).
- The current resume. If it isn't provided, look for the latest one in the user's notes or files; if none is found, ask.

## 1. Parse the posting
- Extract title, level, location/remote, pay if listed, must-have requirements, nice-to-haves, tools and certifications, and repeated keywords.

## 2. Score fit A–F
- Map each must-have to resume evidence: Strong (direct experience), Partial (adjacent or transferable), or Missing.
- Grade: A = all must-haves strong; B = most strong, rest partial; C = several partial; D = one or more must-haves missing; F = core requirement missing (e.g. required clearance or license).
- Show the mapping table so the grade is explainable.

## 3. Gaps and angles
- List the gaps honestly, plus a realistic way to close each (a cert, a homelab or portfolio project, coursework in progress).
- Point out transferable experience the user may be underselling, and the roles it maps to.

## 4. Tailored resume
- Reorder and reword existing bullets to lead with what this posting values, and mirror its keywords where they are true.
- Never invent jobs, titles, dates, metrics, certifications or tools. If a strong bullet needs a number the user hasn't given, leave a clearly marked placeholder and ask.
- Keep the user's existing resume format and length constraints.

## 5. Output
- Fit grade with the evidence table, the top 3 talking points for an interview, the gap list, and the tailored resume (or a diff of changed bullets).
- If asked for several postings, rank them by grade and say which to apply to first.