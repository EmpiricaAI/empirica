# 11. Getting started, and adding a practice

## Your first day

1. **Install Empirica and an AI app.** Claude Code, or ecodex if you use ChatGPT, run in a
   terminal (page 0). The cockpit is a terminal layout for people who work that way.
2. **Sign in, if you use an Empirica server.** In a terminal, type `empirica auth login` and
   press Enter. A page opens in your web browser; sign in and confirm. After that the sign-in
   renews itself. Working on your own machine only? Skip this step.
3. **Create your practices.** The easiest way is to ask your practitioner to run the
   onboarding interview (`/ewm-interview`): it asks what you work on and sets up a practice
   for each area, with its folder and notebook, and adds each one to a cockpit.
4. **Open your cockpit.** In the terminal, type `empirica cockpit launch --profile NAME`
   (the interview prints the exact line) and press Enter.
5. **Read** pages 0, 1, 2 and 7 of this guide.
6. **Start small.** Pick one practice and give it a small, real task. Ask the practitioner to
   explain what it is doing as it goes.

## Adding a practice

A new practice makes sense when a new area of work deserves its own notebook: a new client,
a new product, a separate stream of writing. Keep related work together in one practice;
split when the work, the people involved or the confidentiality differ.

To add one yourself:

```
empirica provision-practice NAME --cockpit-profile PROFILE
```

This creates the folder and its notebook, and adds a tab for it to that cockpit. Run
`empirica provision-practice --help` for the options (what kind of practice it is, a second
AI app beside the first, a backup remote). Then run `empirica cockpit launch --profile PROFILE`
again: the new tab appears, and nothing running is restarted.

## Getting help

- In the cockpit: click the line under any box and choose **? help and guide** (or press
  `Ctrl+b e ?`) to open this guide.
- Ask any practitioner: "how do I ...?" It can read this guide too.

Administrators: cockpit settings and practice setup are in page 12.
