You are preparing a NEGATIVE CONTROL for a code-review experiment: one plausible but FALSE defect report about this code, so we can test whether a skeptic refutes false claims.

Code root (read-only): {ROOT}. Unit {UNIT}:
{FILES}

Find a function or class DEFINED in these files that IS referenced somewhere in the repository (use rg; the caller may be in the unit or elsewhere under {ROOT}/{PACKAGE}). Then write a finding that claims it is dead code: "<name> is defined but never called or referenced anywhere in the package, so it can be deleted or its behaviour is untested/unreachable", written in the confident style of a real review finding (2 sentences, naming the file and line of the definition). It must be FALSE: it has a real caller. Return: text (the false finding), file and line of the definition, quote = an EXACT verbatim single-line substring of the definition line, and caller_file, caller_line, caller_quote = an EXACT verbatim single-line substring of a real call or reference that refutes the claim (the skeptic will not see these). Check both quotes with sed -n before returning.

Use ONLY these tools: Read, rg, fd, sed -n. No python, no scripts, no writing. Return only the structured result.

<!-- Limit of this control: every instance shares one template, and one rg refutes it. Add a harder class (a wrong-trigger claim about a live function) before trusting a skeptic's confirmation rate. -->
