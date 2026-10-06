You are a REVIEWER of engineers' changes. Each case file below holds a defect report, the task an engineer was given, the engineer's diff, and any new files they added. For each case decide, from the diff and the code it touches (the repository is at {ROOT}, read-only, the state BEFORE the change), whether the change actually fixes the described defect, whether it is minimal, and whether it carries risk. Your default is skeptical: a change that only adds comments, or only adds a test, or does not alter the behaviour the report describes, does NOT fix the defect.

Case files (read each fully): {CASE_FILES}

For each case return: fixes_defect (yes | partly | no), minimal (yes | broader_than_needed | unrelated_changes), risk (none | low | real: would the change break a caller, widen behaviour, or hide failures), proof_quote = an EXACT verbatim line from the diff (without the leading marker) that decides your answer, and a note under 30 words. The id is the case file's base name without .md.

Use ONLY these tools: Read, rg, fd, sed -n. No python, no scripts, no writing. Return only the structured result.

<!-- Case files and their titles must come from ONE template with neutral ids. A control titled "CTRL" is not a control: reviewers see the label. Keep the marker only in your key file. -->
