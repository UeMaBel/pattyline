# Pattyline: project and CLI ideas

Conversation handoff, 2026-11-01. This captures the proposed direction; it is not an implementation specification or a ticket queue.

The design interview is complete. See the [draft v1 specification](docs/pattyline/v1/spec.md), [implementation tickets](docs/pattyline/v1/tickets.md), and [runtime research](docs/pattyline/v1/runtime-research.md). These concrete artifacts await review and execution approval; local and cloud workers are both required in v1.

## Confirmed direction — 2026-11-01

- The intended user journey is `pattyline init` in a project folder, guided discussion of what to build, grilling and domain modeling, specification, ticket preparation, human approval, then `pattyline start` for unattended execution. Guided preparation is part of the first release's CLI experience, with fresh agent contexts between stages and durable document handoffs. Setup configuration and verification policy are also established during init. Skills guide preparation; the deterministic runtime still owns execution mechanics.
- The primary outcome is reduced human hands-on time per integrated ticket. Track cloud usage as a secondary metric; lower cloud usage alone does not establish success.
- Idea → specification → tickets remains human-led and supported by bundled skills. A valid ticket has complete, consistent required information; a Ready ticket has explicit human approval for execution. Validity alone does not authorize execution. See [the execution boundary decision](docs/adr/0001-human-approved-execution-boundary.md).
- The first usable release includes both local and cloud workers, checks, independent review, interruption recovery, and agent-operated integration, continuing with newly unblocked approved tickets unattended. Local execution and bounded cloud escalation are required in v1. Claims of savings require comparison of local and cloud execution.
- The website will be the first consuming project. A snapshot of its structure and conventions is available under `website_templates/`; references below to “this website” describe that consuming project.
- Approval interruptions persist a blocked execution state and require explicit resumption after human resolution. Workers must honor configured permissions and must not wait indefinitely or repeatedly retry the denied operation.
- Integration is agent-operated and verified by checking that the reviewed commit is an ancestor of the configured base branch. Use normal merges or fast-forwards initially; squash integration requires a separate verification policy. Dependencies remain blocked until integration is verified. This supersedes the earlier manual integration handoff decision.
- The first release supports one user on one machine. Concurrent CLI invocations share project state and exclusive claims; coordination across machines is deferred.
- Windows and Linux are supported from the first release. macOS remains unverified until tested. Platform-specific process handling must be isolated, and project checks may specify platform-appropriate commands.
- The website's `ready-for-agent` status authorizes execution only when explicitly assigned or approved by a human. Preparation skills may propose readiness but cannot grant execution approval.
- Markdown owns ticket content, dependencies, routing, and human approval. Pattyline's persisted execution state owns claims, attempts, check results, review results, and verified integration. Initially only implementation tickets are executable; research, prototype, and grilling tickets remain in human-led preparation.
- Onboarding requires human reconciliation of existing claims and completion evidence before dispatch. Existing Markdown statuses and historical queue entries alone do not prove live claims or verified integration.
- Ticket identity combines the feature directory and ticket number, such as `personal-website/67`, and remains stable across descriptive filename changes.
- Changes to scope, acceptance criteria, dependencies, routing, or the source specification require renewed human approval. Comments and appended execution evidence do not. During execution, preserve work and block further dispatch for the affected ticket until reconciled.
- Review approval binds to a specific commit and the ticket, instructions, and verification inputs used for review. Later code changes require fresh checks and review.
- The initial integration adapter uses GitHub PRs: push the reviewed branch, open a PR, wait for required checks, and merge without bypassing branch protection. Use normal merges to preserve reviewed commit identity.
- Each run captures a fixed selection of human-approved tickets and their approved dependencies at startup. Newly drafted or newly approved tickets wait for a later run; selected dependents proceed automatically after verified integration.
- Exhausted repair budgets, missing credentials or permissions, and unavailable external inputs block the affected ticket and its dependents while unrelated eligible work continues. Shared failures that prevent safe execution stop the run.
- Every acceptance criterion requires evidence for unattended completion. Explicit human judgment or unavailable external verification leaves the ticket blocked; an agent reviewer cannot substitute for a required human decision.
- Normal starts require explicit ticket selection or `--all-ready`, include approved dependencies, and show the resulting plan before dispatch. A bare `pattyline start` guides selection and confirmation. `pattyline start --yolo` selects all currently approved, ready tickets and their approved dependencies and dispatches without interactive plan confirmation. It retains fixed run selection, permissions, verification, independent review, branch protection, and budgets. `start` replaces the previously proposed `run` command name.
- Verification policy belongs in each ticket as structured check references such as `backend-tests` or `all-tests`. Project configuration maps names to commands and working directories, including platform-appropriate commands. Missing or unknown references fail validation before dispatch. Preparation skills translate verification prose into these references.
- Pattyline executes declared checks and captures actual results; verification does not depend on the local model's ability to run checks. Failed checks return to the implementer with captured output, within its repair budget. The independent cloud reviewer assesses whether the resulting evidence satisfies acceptance criteria.
- Weakening approved verification requirements requires human approval and blocks the affected ticket until reapproved. Workers may run additional checks but cannot remove or narrow required checks on their own.
- Default to one implementation attempt followed by at most two repair rounds per ticket. Each repair is followed by checks and fresh independent review. Consumed attempts persist across restarts; time and spending limits also apply, with values still to be decided.
- If the base branch advances before merge, update the ticket branch, rerun the ticket's required checks, and obtain fresh review before merging, even after a clean merge.
- Cancellation and a configured run deadline are first-release requirements: stop new dispatch, terminate active worker processes within a grace period, and preserve branches, logs, and state for explicit resumption.
- Store execution state in SQLite under Git-ignored `.pattyline/` in the primary checkout, shared by every invocation for the project. Keep logs and review artifacts there; support configurable worktree storage outside the checkout. Moving machines or operating systems initially requires finishing or explicitly abandoning active runs.
- Recovery must establish that a previous worker has stopped before replacing it. Uncertain ownership blocks for human reconciliation; a stale timestamp alone does not authorize replacement.
- Independent reviewers cannot edit implementation files. They return structured approval or required fixes; implementers repair code, and a separate integration step handles GitHub operations.
- Validate structured worker and reviewer results. Implementation results include commit, summary, and limitations; review results include verdict and concrete findings. Malformed or missing results are incomplete execution, never success. Pattyline supplies its own check evidence.
- Require a configured run deadline. Initial configurable timeout defaults are 30 minutes per implementation or repair attempt, 15 minutes per review, and 20 minutes waiting for PR checks. Stage expiry preserves progress and blocks the affected ticket; the run deadline stops the whole run. Spending limits and usage measurement remain open.
- V1 supports local and cloud routing. A local ticket is blocked when its configured local runtime or model is unavailable; any cloud fallback must follow the approved escalation policy rather than silently retagging work.
- Use Python for the CLI, scheduler, adapters, and persistence, distributed as an installable command-line package. Consuming projects remain independent of Pattyline's implementation language.
- Init establishes configured worktree setup steps, such as dependency installation and test-environment preparation, with separate timeouts and captured results. Run setup before implementation; unavailable credentials or services block work. Setup does not authorize production provisioning.
- Provide explicit approval that records approved ticket and specification fingerprints in project Markdown. Existing website approval statements may be imported during human onboarding reconciliation. YOLO never grants approval.
- Changes to check commands, worker permissions, or integration policy invalidate affected approval evidence and require reconciliation before further dispatch. Approval binds to effective requirements, not just check names.
- Init uses a cloud preparation agent with bundled skills and Pattyline-managed session handoffs. Alternative preparation workers are deferred.
- Fresh preparation sessions begin at stage boundaries: grilling, specification, ticket breakdown, and optional preparation review. Additional handoffs occur before context capacity is exhausted; preserve decisions, unresolved questions, and document references.
- Ask the human whether to run independent preparation review of the specification and ticket breakdown; simple projects may skip it. When selected, the reviewer checks missing requirements, contradictions, dependencies, and executable verification. Agents repair drafting errors; unresolved product decisions return to the human. This optional review does not replace or waive mandatory independent implementation review.
- Init inspects the folder first and offers adoption of existing specifications, tickets, instructions, and setup. Empty projects begin with the idea interview; the website enters through adoption and reconciliation.
- Init configures the project once and can launch its first preparation workflow. `pattyline prepare` starts or resumes preparation for subsequent features without repeating project setup.
- Preparation ends with an approval summary of scope, specification, tickets, dependencies, routing, and verification. The human may approve a whole feature or selected tickets; bind approval to the exact documents reviewed without requiring one confirmation per ticket.
- Unresolved product decisions discovered during execution are recorded as questions and block the affected ticket while unrelated eligible work continues. Agents may resolve routine implementation details within the approved specification, but cannot invent product requirements.
- Prepare detects unfinished preparation and offers to resume it or start a separate feature. Each feature retains independent documents, decisions, and session history.
- Bundle versioned preparation skills and invoke them directly without installation into personal agent skill directories. Projects pin their workflow version and explicitly adopt updates.
- Every execution run produces a durable report of integrated tickets, blockers and questions, verification and review outcomes, elapsed time, and available usage figures. Status summarizes the report and links to details.
- New projects store version-controlled preparation documents, tickets, and approval records under `docs/pattyline/<feature>/`. Git-ignored `.pattyline/` holds disposable execution state, logs, databases, and session transcripts. The website adapter retains its existing `.scratch/<feature>/` structure.
- Init establishes an execution permission profile for file access, commands, network access, and GitHub operations and validates that the selected worker can enforce it. Unsupported restrictions fail validation; YOLO never broadens permissions.
- Always enforce deadlines and attempt limits; enforce provider usage limits where supported. Distinguish reported estimates from enforceable limits and disclose when a hard monetary cap cannot be guaranteed. Unsupported hard-cap requirements fail validation before dispatch.
- Preparation asks questions until execution-relevant decisions are settled, then requests confirmation of shared understanding before generating the final specification and tickets. Capture notes and glossary terms throughout; unresolved product assumptions cannot silently enter the approved handoff.
- Init discovers existing tickets and brings them into Pattyline's workflow through validation, approval reconciliation, and dependency planning. Adoption must preserve existing work rather than assume every project begins with newly generated tickets.
- Init previews discovered specs, tickets, inferred relationships, and missing fields and asks which sets to adopt. Search known locations first and allow additional paths; do not silently import arbitrary Markdown.
- Imported tickets missing verification, dependencies, routing, or sufficient acceptance criteria enter preparation for refinement. Complete tickets proceed to approval reconciliation. Preserve existing text and history; propose additions instead of wholesale replacement.
- If imported tickets lack a source specification, offer to reconstruct a draft from them and resolve gaps with the human before approval. Clearly label inferred requirements.
- Repeated initialization or import recognizes adopted tickets and updates mappings without duplication, resetting attempts, or losing review and integration history. Conflicting identities and ambiguous moves require reconciliation before execution.
- Required checks run inside the ticket's isolated worktree, with setup supplying isolated test environments where needed. Checks must not modify production data or interfere with other running project environments. Missing isolation requirements block affected checks.
- Before implementation, run the ticket's required checks against its configured base revision. Existing failures block the ticket and are reported rather than silently assigned for unrelated repair or accepted as unexplained failures.
- If GitHub is temporarily unavailable after successful implementation and review, resume integration without repeating implementation when approved inputs and the reviewed commit remain unchanged. If the base advanced, update, rerun checks, and obtain fresh review.
- The first specification fully defines both local and cloud execution, including local runtime setup, model availability, and bounded escalation. Parallel scheduling remains a later milestone. This supersedes the earlier cloud-only v1 decision.
- Release acceptance requires an unattended chain of at least two dependent tickets, interruption recovery, and a blocked ticket that does not stop unrelated eligible work. Verify Windows and Linux behavior and the imported website ticket format.
- When a folder lacks Git or a GitHub remote, init offers to create them after showing the destination and repository visibility and obtaining approval. Preparation may proceed without a remote; unattended execution requires validated Git, base branch, and GitHub integration.
- Init checks worker availability, authentication, Git, GitHub permissions, and configured setup commands and gives actionable guidance for missing prerequisites. Configuration stores credential references rather than secrets. Repeat relevant readiness checks before execution.
- Preserve failed or interrupted worktrees and branches. Automatically remove clean worktrees after verified integration; retain reports and evidence until explicit cleanup. Never automatically delete uncommitted changes, and cleanup must respect active runs.
- Status identifies each blocker and its remedy. Resume revalidates affected work before continuing; product questions return to prepare. Status edits alone cannot bypass approval, verification, or recovery requirements.
- Concrete model identifiers, authentication mode, machine paths, and check commands are configuration choices rather than product constants. Init previews applicable global defaults and offers to use, customize, or ignore them. Global defaults may include models, authentication references, budgets, and permission-profile templates; ticket paths, checks, repository identity, and setup steps remain project-specific.
- Accepting global defaults snapshots their values into project configuration. Later global edits do not silently affect initialized projects. Explicit refresh previews changes and triggers required approval reconciliation.
- Effective configuration precedence is built-in defaults, then project configuration, then explicit invocation options. Global defaults apply during init or explicit refresh. Invocation options may select work or tighten limits but cannot silently broaden approved permissions or weaken verification.
- Init changes remain project-local unless the human explicitly selects save as global defaults. Preview the exact settings saved globally; neither configuration file stores secrets.
- All skills used by Pattyline must be present in the repository and shipped with the installed package, including required supporting resources; do not depend on personal skill installations. Redistribution rights and transitive skill dependencies must be verified.
- Installation provides guided dependency setup: detect existing Codex, Ollama, and the selected coding-agent runtime, preview missing components, and install them after one approval. Reuse compatible installations. Init repeats detection and offers installation for anything missing. Explain and request administrator access only for the specific step requiring it.
- Local setup detects available Ollama models and hardware, recommends a suitable model, and asks before downloading it with download size and storage requirements shown. Ollama installation alone does not establish local worker readiness.
- Prefer a single coding-agent runtime capable of cloud and Ollama execution if research verifies its permission, recovery, and structured-result capabilities. Use separate runtimes if necessary; Ollama supplies inference rather than the coding tool loop.
- Preparation shows whether local tickets may escalate to cloud and the applicable failure and budget limits. Human ticket approval includes that escalation policy. Overnight escalation within approved limits needs no additional confirmation; without approval, exhausted local execution blocks the ticket.

## Intent and name

Build a reusable, public project for taking well-specified tickets through agent implementation, verification, independent code review, and integration.

The CLI includes guided idea clarification, specification, and ticket preparation before execution.

The motivation is to stop rebuilding or adjusting the same agent workflow for every new project. Success means onboarding a second project through configuration and validation without editing the orchestrator itself.

Possible tagline: **Local agents. Cloud power. One workflow.**

## End-to-end workflow

```text
Skills: idea -> specification -> reviewed, tagged tickets
                                      |
CLI: validate -> schedule -> claim -> implement -> check -> review -> integrate
                                        ^                    |
                                        +---- bounded fixes--+
```

- Use skills to clarify an idea, create a specification, break it into tickets, and triage those tickets.
- Tickets are already tagged with their intended execution target. Initial routing is deterministic and does not need an LLM classifier.
- Easy, narrow, well-specified tasks run through a local coding agent using Ollama.
- More demanding tasks run through a cloud coding agent using a configured GPT-6.1 model.
- Run independent code review after implementation, initially using a fresh GPT-6.1 session for both local and cloud implementations.
- Route by risk and ambiguity as well as estimated effort. A small authentication change may require stronger reasoning than a large mechanical edit.
- Escalate local work that stalls or repeatedly fails checks or review, preserving its branch and evidence for the cloud worker.

## Architectural boundary

**Pattyline owns execution mechanics; each consuming project owns its conventions.**

Keep repository paths, check commands, instructions, models, concurrency limits, and integration policy in project configuration. The scheduler should not know whether a project uses Django, React, or another stack.

Skills and the execution runtime remain separate but share a validated ticket contract. Skills help humans prepare work; the CLI requires sufficiently complete inputs before execution.

Start with a deterministic execution core, guided cloud preparation, local and cloud execution adapters, and one Markdown tracker adapter. Avoid a general plugin framework or dashboard until real projects justify them.

Proposed source layout:

```text
pattyline/
  cli/          # Commands and presentation
  core/         # Scheduling, lifecycle, claiming, recovery
  workers/      # Local coding-agent and Codex adapters
  trackers/     # Markdown ticket adapter initially
  skills/       # Specification, ticket breakdown, triage workflows
  templates/    # Starter project configuration
  tests/
```

Use Python, distributed as an installable CLI package. Select and verify the v1 local coding-agent runtime before implementation.

## Proposed CLI

These commands describe the intended interface, not an existing executable.

| Command | Purpose |
| --- | --- |
| `pattyline init` | Ask what to build; guide grilling, domain modeling, specification, tickets, project setup, and human approval using fresh contexts between preparation stages |
| `pattyline prepare` | Start or resume guided preparation for a feature in an initialized project |
| `pattyline validate` | Validate configuration, tickets, dependencies, and prerequisites |
| `pattyline plan` | Show eligible tickets, selected workers, and reasons work is blocked; do not dispatch |
| `pattyline start` | Execute approved work through implementation, verification, independent review, and integration within configured limits |
| `pattyline status` | Show active runs, blocked work, review outcomes, and integration state |
| `pattyline resume` | Recover an interrupted run using persisted state |

Explicit ticket selection and cancellation are first-release requirements; exact selection flags and the cancellation command shape remain specification details. Manual escalation and review-only execution are deferred.

## Example project configuration

Illustrative only: filename, schema, adapter identifiers, and model identifiers are not finalized.

```yaml
tracker:
  type: markdown
  tickets: .scratch/**/issues/*.md

instructions:
  - AGENTS.md

workers:
  local:
    adapter: local-agent
    provider: ollama
    model: your-local-model
  cloud:
    adapter: codex
    model: your-gpt-6.1-model-id

review:
  worker: cloud

checks:
  frontend-tests:
    command: [npm, test]
    cwd: frontend

execution:
  concurrency: 1
  integration: agent
```

This example illustrates the two-worker configuration required in v1. Tickets reference named checks. Commands must come from the consuming project; the example is not a requirement for this website.

## Ticket contract and compatibility

Validate at least:

- Stable ticket identifier.
- Readiness/status.
- Dependencies.
- Target worker, such as the existing `llm_target: local` field.
- Acceptance criteria.
- Links or context needed to find the source specification.

Missing or inconsistent information should produce actionable validation errors.

This website already stores specs and numbered tickets under `.scratch/<feature>/`, with `Blocked by:`, `Status:`, `Type:`, and `llm_target:` fields. Its tickets also contain implementation and review handoff instructions. Use this as the first real tracker input rather than inventing a replacement format immediately.

Read project instructions before applying lifecycle transitions. This project's triage states and execution states are not a complete common lifecycle; define their mapping explicitly instead of silently treating them as interchangeable.

## Execution and recovery

- Select ready, unclaimed tickets in dependency order. Dependencies become satisfied only after integration into the base branch.
- Claim work atomically in one authoritative queue before dispatch. Worktree-local ticket copies are not shared locks.
- Create an isolated Git worktree and fresh branch for each ticket, based on an updated base branch.
- Give workers the ticket, specification, project instructions, and relevant dependency context.
- Ollama provides inference; a local coding-agent runtime must supply file access, editing, command execution, and context management.
- Use an existing agent runtime where practical. Codex non-interactive execution is a candidate for the cloud adapter.
- Workers return a branch/commit, implementation summary, verification evidence, and limitations. They do not decide their own independent review outcome.
- The orchestrator runs relevant checks and records actual exit statuses and output. A worker's claim that it passed is insufficient.
- Persist ticket identity, run identity, branch, worktree, base revision, session references, attempts, and review verdicts.
- Use local SQLite under `.pattyline/` for execution state, with readable logs and review artifacts. Markdown remains the human-facing ticket source.
- On restart, reconcile existing runs before dispatching more work. Do not rerun or duplicate a ticket merely because the CLI process stopped.
- Keep integration serialized. If the base branch advances, update the branch, repeat the ticket's required checks, and obtain fresh review, including after a clean merge.

Project instructions and configured permissions must remain effective for every worker. Unattended runs need explicit handling for operations that require approval rather than hanging indefinitely.

## Review and escalation

Use a fresh reviewer session with the source ticket, acceptance criteria, project instructions, implementation diff, and verification results.

Review correctness, scope, contract compatibility, and whether the acceptance criteria were actually demonstrated. Return required fixes with concrete evidence.

Bound local implementation time, tool iterations, and repair attempts. Escalate to the cloud worker when those budgets are exhausted or the work is discovered to exceed local capability. Hand off the existing branch, attempted approaches, failed checks, and unresolved findings.

Cloud fixes and reviewer loops also need limits. Infrastructure failures, missing credentials, and unavailable services should be recorded as blockers rather than consuming repeated reasoning attempts.

Integration is agent-operated: push, PR creation, required-check waiting, and merge follow project policy without a routine human handoff. Completion must distinguish implemented, reviewed, and integrated work. After verified integration, continue with newly unblocked approved tickets.

## Concurrency

Start with one implementer followed by one reviewer. Prove claiming, verification, and recovery before adding parallel work.

This website's existing handoff allows at most one frontend implementer and one backend implementer plus a separate reviewer, with explicit ownership of shared configuration and serialized merges. A later Pattyline configuration should be able to express those constraints without hardcoding frontend/backend concepts into the scheduler.

## Suggested implementation stages

1. Extract and document this project's current ticket contract and execution policy.
2. Implement guided `init` and `prepare`, approval and adoption, `validate`, and `plan`, including dependency and routing explanations.
3. Complete the cloud-worker path: claim, worktree, implementation, checks, independent review, agent-operated integration, persisted state, and recovery. Demonstrate unattended continuation through dependent tickets.
4. Complete the Ollama-backed coding worker and bounded escalation to the cloud worker as a first-release requirement.
5. Onboard a second real project. Use the differences to refine configuration and identify necessary extension points.
6. Add controlled concurrency after the sequential workflow, including automated integration, is reliable.

Use meaningful integration tests for scheduler dependencies, exclusive claims, interrupted-run recovery, review transitions, and escalation. Test against fake worker adapters before involving paid or nondeterministic models.

## Evaluation

Track total time and cloud usage per accepted ticket, including failed attempts, review, repair, and escalation. Also track local completion rate, review rejection rate, and manual intervention.

The purpose is to determine whether local routing saves enough cloud usage and human effort to justify its complexity. Hardware and model choice must be evaluated with actual project tickets.

## Specification details and follow-up research

- Official documentation identifies Codex CLI as a candidate shared coding runtime: noninteractive execution supports the Ollama provider as well as cloud execution. See [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode), and [Ollama Codex integration](https://docs.ollama.com/integrations/codex). Ollama recommends at least 64k context and tool-capable models. Verify actual local inference, exact-version structured results, editing, permissions, and cancellation in a smoke test; documented provider support alone does not prove the complete worker contract.
- Official installers exist for [Codex](https://learn.chatgpt.com/docs/codex/cli), [Codex on Windows](https://learn.chatgpt.com/docs/enterprise/windows-deployment), [Ollama on Windows](https://docs.ollama.com/windows), and [Ollama on Linux](https://docs.ollama.com/linux). Installation, account authentication, model downloads, GPU prerequisites, and permission setup are separate readiness steps. Do not assume unattended installer flags or universal administrator requirements.
- Verify the selected cloud runtime's actual support for interactive preparation, fresh sessions, structured results, permissions, cancellation, authentication, and usage reporting on Windows and Linux. Surface any capability gaps before implementation rather than assume support.
- Define configuration filenames and schemas, structured ticket verification and approval records, worker results, reviewer verdicts, lifecycle transitions, and CLI flags consistent with the confirmed decisions above.
- Specify timeout and cancellation mechanics, budget enforcement capabilities, configuration refresh, and import reconciliation. Actual model identifiers, credentials references, paths, check commands, and run deadlines are selected during project setup.
- Select and validate the v1 local coding-agent runtime and Ollama model using available hardware and actual tickets. Verify dependency installation on Windows and Linux and bundled skill resources and redistribution rights. Parallel scheduling remains a later milestone.

## Naming checks and reference

Pattyline is the chosen working GitHub repository name. A repository can live under the owner's namespace; this discussion did not reserve a GitHub name or create a repository.

At the time of the conversation, direct queries for exact `pattyline` npm and PyPI packages returned 404. `pattyline.com` was registered, and the existing developer service [Pattylinenet](https://pattylinenet.com/) may cause naming confusion. These observations are not trademark clearance or permanent availability guarantees.

Cloud adapter starting point: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode). Verify current commands and model availability during implementation.

## Setup design update — 2026-11-02

The setup boundary was re-grilled and confirmed. This decision supersedes earlier notes that place global dependency setup and provider authentication inside `init`:

- `pattyline setup` is per-user, works from any directory, and does not create project files or state.
- Setup checks Git, GitHub CLI, and Codex. It asks separately before installing or upgrading each missing/outdated component, uses official sources, and gives manual instructions when automation is unavailable.
- Codex is the only initially supported coding agent; OpenAI is the initial model provider. Setup guides and verifies Codex's native ChatGPT sign-in. Codex owns its authentication and configuration; Pattyline does not store login secrets or edit Codex settings.
- Pattyline creates per-user global preferences for provider/model defaults and execution limits, initialized with sensible defaults. Use Codex's current default model; do not add a setup questionnaire.
- GitHub authentication, local inference, container tooling, and repository-specific configuration are deferred until project initialization.
- The design is not implemented yet. The current broker execution path still requires `OPENAI_API_KEY`, separate from Codex ChatGPT sign-in.
