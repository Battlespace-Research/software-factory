## Software factory

This project's work runs through the BSRI software factory. The project's own rules in this file still come first; where one conflicts with a factory default, `factory.yaml` records how the owner settled it.

- **Where work lives.** Under `factory/work/`, one folder per piece of work (`FIX-<n>-<slug>`, `FEA-<n>-<slug>`, `PRG-<n>-<slug>`), each holding its spec, plan, `progress.md`, reviews and approvals. Settings are in `factory.yaml` and the review checklist is in `REVIEW.md`.
- **How to resume.** Run `factory work status <ID>`, or read the Status line at the top of that folder's `progress.md`. It names the stage, the state and what the next session does. Keep the Status line current; state lives in files, never in memory.
- **Verify.** Quick, at every task: {{verify_quick}}. Full, for the tester, milestone demos and the merge gate: {{verify_full}}. A task is done only with the real output of the command in hand, and the full command's exit code is the verdict: it compares its results with `factory/baseline/expected-failures.md`. When `factory.yaml` says `builds: ask`, ask the owner before each full run.
- **Reviewers and the tester never edit product code.** They report findings; the implementer fixes them.
- **Lessons.** Lessons from finished work are added to this file, to `REVIEW.md` or to a skill only with the owner's approval, and are indexed in `factory/lessons.md`.
