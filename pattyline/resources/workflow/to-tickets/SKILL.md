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

Show the dependency graph and ask the human about granularity, edges and
merges/splits before publication. Ask explicitly whether independent preparation
review is wanted. Optional preparation review never disables mandatory independent
cloud implementation review. Produce ticket-proposal.md using the supplied
template; publication and exact ticket approval are human-controlled operations.

Every implemented ticket gets a separate worktree and branch from fetched base,
Pattyline-owned scoped commits/checks, fresh read-only cloud review and serialized
normal GitHub merge. Dependents wait for verified integration. Preserve external
evidence requirements and approved escalation/repair budgets.
