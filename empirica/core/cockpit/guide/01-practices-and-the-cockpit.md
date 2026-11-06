# 1. Practices and the cockpit

## What a practice is

A practice is one area of work, with a memory of its own. "Outreach", "website" and
"finance" can each be a practice. Everything a practitioner learns while working there,
what it found, what it decided, what went wrong, stays with the practice. The next time
any practitioner opens it, that knowledge is there.

Think of a practice as a desk with a notebook. Whoever sits at the desk reads the notebook
first and writes in it as they work.

## Who works there

A **practitioner** is the AI working at that desk. It can be **Claude** (made by Anthropic;
the program is called Claude Code) or **ChatGPT** (made by OpenAI; the program here is called
ecodex). Two practitioners can share one practice and its notebook; they sit side by side in
the same tab.

## The cockpit

The cockpit is the window that shows all your practices together: one **tab** per practice
along the top, one **box** per practitioner inside the tab, and under each box a line of
commands that opens the cockpit's menu (page 0 shows how to move around; page 10 explains
every command).

## When a practitioner stops

Sometimes a practitioner's box shows **"Pane is dead"**. It means that practitioner has
stopped; its box stays in place on purpose. Click the line under its box and choose **R restart this pane** (or press `Ctrl+b e R`) and it starts again in the same place, in the same practice, and picks up the
conversation where it left off. Practitioners that are still working are never touched.

## Leaving and coming back

You can close the window at any time; the cockpit keeps running in the background. To get
back to it, open a terminal (page 0) and run:

    empirica cockpit launch

It goes back to the running cockpit instead of starting a second one. If someone set up more
than one cockpit for you, they will give you its name, and you add it at the end:
`empirica cockpit launch --profile NAME`.
