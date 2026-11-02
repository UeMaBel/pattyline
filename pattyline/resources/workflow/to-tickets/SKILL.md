---
name: to-tickets
description: Propose narrow end-to-end implementation slices from an approved specification.
---

Read the authoritative specification and handoff in a fresh context. Propose
observable vertical slices small enough for one fresh implementation session.
Each slice names its outcome, acceptance evidence, genuine blocking edges,
scope, local/cloud route, check names and whether each check is an existing
baseline or introduced by that slice. Existing failing checks block execution;
new checks are not yet applicable on the base and must pass after implementation.

Show the dependency graph and proposed tickets. Ask the human explicitly whether
these tickets match their intent before publication. Resolve requested changes
through the specification and proposal, then ask again. Only after the human
confirms the proposal should the tickets be written to disk. Produce
ticket-proposal.md using the supplied template. Ticket publication does not
require a separate manual approval command; generated tickets remain drafts for
execution readiness and approval policy.

Every implemented ticket gets a separate worktree and branch from fetched base,
Pattyline-owned scoped commits/checks, fresh read-only cloud review and serialized
normal GitHub merge. Dependents wait for verified integration. Preserve external
evidence requirements and approved escalation/repair budgets.
