"""The text of the cockpit bar's help popup: the bar's legend, the commands, and a first guide for new users.

Kept as a Python constant so it ships with the package whatever the packaging rules say about data files. The guide topics are
David's list (2026-11-06): what he takes for granted and a new person needs. The topic texts here are a first draft for him to edit;
topic 11 is to be replaced by the pages mesh-support drafts from the provisioning runbooks.
"""

HELP_TEXT = """\
  EMPIRICA COCKPIT  ·  wheel, arrows or PgUp/PgDn scroll  ·  g goes to the top  ·  q closes
  ────────────────────────────────────────────────────────────────────────────────────

  THE BOTTOM BAR
    First line   your practices (click one, or Ctrl+b then its number), health on the right.
                 A red ✗ on a practice means a pane in it has stopped.
    Last line    commands. The colour says whether you need to act:

      dim      nothing to do
      orange   recommended: something is waiting (mail, an update, a login about to expire)
      red      needed now: something is broken (a stopped pane, a listener that is down)

    When there are too many practices to fit, health moves to a line of its own.

  COMMANDS   click a chip. From a keyboard: Ctrl+b, then e, then the letter
    R  restart this pane    same practice, same identity, conversation resumed, then woken
    A  refresh all          restart every pane whose program stopped; running ones are left alone
    M  listener             wake this practice so it reads its mail and re-arms its listener
    I  inbox                show this practice's unanswered mail
    U  update               update Empirica and its plugins on this machine (asks first)
    C  claude               update the AI client (asks first)
    S  sessions             restart running panes onto new settings (asks first, it ends them)
    L  login                renew the Cortex login
    ?  this help

    R, A and M never touch a pane that is running. U, C and S always ask before they act.

  THE GUIDE   to jump to a topic type  /§3  and Enter (any number 1 to 11); g returns here

     1  What a practice is, and how the cockpit holds them
     2  Talking with a practitioner
     3  Helping it introspect
     4  Using its predictions
     5  Plans, goals and tasks
     6  Skills
     7  The epistemic loop, and why it pauses to read
     8  The mesh, and what needs your approval
     9  Gardening
    10  Reading the status line and the bar
    11  Onboarding a person, setting up a cockpit, adding a practice


  §1  WHAT A PRACTICE IS, AND HOW THE COCKPIT HOLDS THEM
  ────────────────────────────────────────────────────────────────────────────────────
    A practice is one area of work with its own memory: a directory with a .empirica folder,
    its own goals, findings, decisions and mistakes, and its own skills. A practitioner is
    the AI working in it (Claude Code, ecodex). The memory belongs to the practice, so a new
    session, or a different model, picks up where the last one stopped.

    The cockpit is one tmux session with a tab per practice. Each pane starts in its
    practice's directory with a fixed identity, so a pane that is restarted is the same
    practitioner, not a stranger. Nothing is lost when you close the terminal: the practices
    keep running, and `empirica cockpit launch` brings you back.

  §2  TALKING WITH A PRACTITIONER
  ────────────────────────────────────────────────────────────────────────────────────
    Work with it the way you would with a capable colleague who has read the whole project.
    Say what you are trying to achieve and why, not only the next step. Ask what it thinks
    before you decide. When it disagrees and gives a reason, weigh the reason: it is asked to
    hold a position that the evidence supports and to change it when you bring evidence.

    Short commands get literal work. Context gets judgment.

  §3  HELPING IT INTROSPECT
  ────────────────────────────────────────────────────────────────────────────────────
    You can ask a practitioner about its own state, and the answer is useful:

      What do you know here, and what are you assuming?
      How sure are you, and what would change your mind?
      What have you not checked?

    Share your own thinking the same way, including doubts. It records what it learns as
    findings, what it does not know as unknowns, and what it takes for granted as
    assumptions, so the next session starts from them.

  §4  USING ITS PREDICTIONS
  ────────────────────────────────────────────────────────────────────────────────────
    When something needs your decision, ask for its predicted answer and the one reason
    behind it. Then confirm with a word or override. This is faster than an open question
    and it shows you whether it understood the situation: a prediction it cannot make means
    it has more to read first.

  §5  PLANS, GOALS AND TASKS
  ────────────────────────────────────────────────────────────────────────────────────
    Ask for a plan when the work is more than one step. A goal says what is to be achieved
    and why; tasks are the units of work under it. They are stored in the practice, so you
    can ask at any time: what is open, what is done, what is waiting on me. A task is closed
    with evidence (a commit, a test result), which is what keeps "done" honest.

  §6  SKILLS
  ────────────────────────────────────────────────────────────────────────────────────
    A skill is a written procedure the practitioner loads when a situation calls for it:
    preparing a release, planning a larger piece of work, reviewing a document. It loads
    them on its own when it recognises the moment, and you can ask for one by name with a
    slash, such as /forecast to see what should come next.

  §7  THE EPISTEMIC LOOP, AND WHY IT PAUSES TO READ
  ────────────────────────────────────────────────────────────────────────────────────
    Before it changes anything of substance the practitioner states what it knows and how
    it knows it (PREFLIGHT, and CHECK after investigating). When the work is done it says
    what held and what did not (POSTFLIGHT). In between, reading and searching are always
    free; writing files and running things that change state wait for that statement.

    So when it stops to read before acting, that is the system working. The record lets it,
    and you, see later where its confidence matched reality and where it did not.

  §8  THE MESH, AND WHAT NEEDS YOUR APPROVAL
  ────────────────────────────────────────────────────────────────────────────────────
    Practices can message each other. Questions and information go straight through. A
    request for another practice to do something is a proposal, and it waits for a person
    to accept, change or decline it, unless you have turned automatic acceptance on.

    The I chip shows mail a practice has not answered. The M chip wakes a practice whose
    listener has stopped, so it reads its mail again.

  §9  GARDENING
  ────────────────────────────────────────────────────────────────────────────────────
    A practice's memory is only useful if it is current. From time to time, and before a
    release, ask the practitioner to garden: close goals that are done, resolve questions
    that were answered, and retract findings that turned out wrong. A good moment is when
    a new session starts by telling you things that are no longer true.

  §10  READING THE STATUS LINE AND THE BAR
  ────────────────────────────────────────────────────────────────────────────────────
    Inside each practice, the line under the prompt shows the open transaction, the phase
    (investigating or acting), and how much of the context is used. Context filling up is
    normal: what was learned is saved as it goes.

    The bar at the bottom of the cockpit is about the machine and the practices as a whole.
    Dim means leave it alone. Orange means look when you have a moment. Red means act.

  §11  ONBOARDING A PERSON, SETTING UP A COCKPIT, ADDING A PRACTICE
  ────────────────────────────────────────────────────────────────────────────────────
    Add a practice and put it in a cockpit:

      empirica provision-practice NAME --type TYPE --description "what it is for" \\
          --cockpit-profile PROFILE

    Check a cockpit profile, then bring it up:

      empirica cockpit status --profile PROFILE
      empirica cockpit launch --profile PROFILE

    The full guide is docs/guides/COCKPIT.md. `empirica cockpit --help` is the short form.
"""
