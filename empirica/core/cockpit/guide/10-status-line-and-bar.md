# 10. Reading the status line and the cockpit's commands

Three places show how things are going: the **tabs** along the top of the cockpit, the line of
**commands** under each box, and the **status line** inside each practitioner's box.

## The top of the cockpit

Your practices, as tabs. A red **✗** on a tab means a practitioner in that practice has stopped.
On the right, small green or red dots show whether Empirica's online services are reachable,
and how busy and how full this computer is. With many practices, the dots move to a second
line.

## The commands under each box

Each box has a line of commands under it. The colour tells you whether you need to do anything:

| Colour | Means |
|---|---|
| grey | nothing to do |
| **orange** | recommended: something is waiting for you |
| **red** | needed now: something has stopped working |

**Click anywhere on that line** to open the cockpit's menu, then click what you want. Every menu
has a **Close** item at the bottom; clicking outside it or pressing Esc also closes it. From the
keyboard: `Ctrl+b e` then the letter (page 0), or `Ctrl+b e` then Space for the menu.

| Command | What it does | When it lights up | Asks first? |
|---|---|---|---|
| **R** restart | restarts the practitioner in this box if it has stopped | red: it has stopped | no |
| **A** refresh | restarts every stopped practitioner in the cockpit | red: one or more have stopped | no |
| **M** listener | reconnects this practice's message delivery | red: messages are not arriving; orange: more than one listener runs for it, so messages arrive twice | no |
| **I** inbox | shows messages for this practice that still need an answer | orange: some are waiting | no |
| **C** claude | updates Claude Code (`claude update`), or what this machine's update check names for it | orange: a newer version exists | yes |
| **S** sessions | restarts the running practitioners so they pick up an update | orange: an update is waiting for them | yes, it interrupts them, so use it at a quiet moment |
| **L** login | signs this computer in to the Empirica server again, in a window of its own | orange or red when this machine's update check says the sign-in needs renewing | no |
| **?** help | opens this guide | none | no |

**Updating Empirica itself** is in the menu too, near the end: *update Empirica on this machine*.
It has no letter and no button, so it is not started by accident, and it asks first.

The help and the inbox open in a box with a **Close** button at the top. Updates and login open
in a window of their own, as a new tab, so you can read what they say; it closes by itself
when it is done.

## The status line (inside each box)

Under the conversation, each practitioner shows one line about itself. You do not need to
watch it; it is there when you want to know what the practitioner is doing.

| Part | Tells you |
|---|---|
| practice name | which practice it is working in |
| stage and sureness | which of the three moments it is in (page 7), and how sure it is right now [check: the symbols used] |
| counts | how many goals are open, questions unanswered, assumptions in use, and findings recorded |
| learning mark | whether its understanding changed during this piece of work |
| memory in use | how much of its working memory for this conversation is used |
| looking or doing | whether it is still finding things out, or ready to make changes |
| model | which AI model is working |

When its working memory fills up, the conversation is summarised and work carries on. Nothing
important is lost: it is already written in the practice's notebook.
