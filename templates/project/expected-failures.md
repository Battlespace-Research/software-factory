# Expected failures

The baseline for this project's full verify command (`verify.full` in `sw-factory.yaml`).

**The contract.** The full verify command compares its own results with this list. It exits 0 when only the failures listed here occur, and exits non-zero when anything else fails. The factory treats that exit code as the verdict. It never judges a run by reading "green" or by counting by hand, and test totals come only from real runs.

Every expected failure traces to the base branch: it fails there too, at the commit named. Remove a row when the failure is fixed.

| Test or check | Fails on the base branch since | Why it fails |
|---------------|--------------------------------|--------------|
