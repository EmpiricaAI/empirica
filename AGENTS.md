## Work Tracking: Empirica Goals and Tasks

Work in this project is tracked with **Empirica goals and tasks**. Do not keep markdown TODOs or task lists.

```bash
empirica goals-list                                    # What is open
empirica goals-ready                                   # What is unblocked and ready
empirica goals-create --objective "<title>" --description "<markdown: why, success criteria, links>"
empirica goals-add-task --goal-id <ID> --description "<one unit of work>"
empirica goals-complete-task --task-id <ID> --evidence "<commit SHA, test result, path>"
empirica goals-complete --goal-id <ID> --reason "<evidence>"
```

- Decompose into tasks when the work starts, not after it is done.
- Commit after each completed task, and cite the commit as the task's evidence.
- `empirica goals-claim --goal-id <ID> --create-branch` creates and checks out a working branch for the goal (without the flag it leaves git alone).

### Optional: bd (beads) integration

`goals-create --use-beads` also creates a linked `bd` issue, for teams that track work in bd. It is optional; goals are the record.
