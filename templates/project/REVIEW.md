---
# The lenses a review can use. Each one needs a "## Lens: <name>" section below, and its name is lower case.
# Add a project lens (for example ux or performance) here, with a section below.
lenses: [correctness, security, maintainability, spec]
# Which lenses run on each track.
tracks:
  fix: [correctness, security]
  feature: [correctness, security, maintainability]
  program: [correctness, security, maintainability, spec]
# The most nit-level findings one review may report.
nit_cap: 5
# Glob patterns of files reviewers leave alone, such as generated code.
ignore: []
# This project's own rules. A reviewer flags every change that breaks one,
# for example "never change XML serialization".
always_flag: []
---
# Review checklist

Each reviewer reads the section for its own lens, and only that one. The format a review must follow (the verdict line, the findings table, the severities) is defined by the factory's review prompts, not here.

## Lens: correctness

- Logic: the code does what the spec and the task say, on the unhappy paths as well as the happy one.
- Edge cases: empty, missing, zero, maximum, duplicate and malformed input; boundaries; text encoding, line endings, time and paths where they apply.
- Error handling: failures are caught where they can be dealt with, reported with a useful message and never swallowed; a failure leaves no half-done state behind.
- Tests prove the behaviour: each new behaviour has a test that fails without the change, and the tests check results rather than only that code ran.
- Concurrency: shared state, ordering, races, locking and cancellation, wherever more than one thread, process or request can touch the same data.

## Lens: security

- Input validation: everything that comes from outside (users, files, the network, the environment, the command line) is validated at the boundary before it is used.
- Authentication and authorization: who may do this is checked on every path that reaches it, on the server side, and fails closed.
- Secrets: no credentials, tokens or keys in code, configuration, logs, tests or error messages.
- Injection: queries, shell commands, file paths, templates and markup built from input are parameterized or escaped, never joined by hand.
- Unsafe deserialization: untrusted data is never loaded with a format or loader that can run code or build arbitrary objects.
- Risky dependencies: a new dependency is needed, maintained, licensed for this use and pinned; known vulnerabilities in what is added or upgraded are checked.

## Lens: maintainability

Maintainability findings never block a merge. They become follow-up tickets.

- Duplication: logic copied rather than shared, and near-copies that will drift apart.
- Module boundaries: each module has one job, depends only on what it needs, and does not reach into another's internals.
- Naming: names say what a thing is or does, and match the words the project already uses.
- Dead code: unused code, unreachable branches, commented-out code, stale flags and settings.
- Documentation of the public surface: every public interface, command, option and file format a person or another module relies on is documented and the documentation matches the code.

## Lens: spec

- Every requirement and acceptance criterion in the spec is addressed, each one by something a reviewer can point to in the change.
- Nothing outside the scope is added: no extra features, options or refactors the spec did not ask for.
- The plan task is fulfilled: the change does what the task in the plan says, in the files and with the interfaces the plan names.
