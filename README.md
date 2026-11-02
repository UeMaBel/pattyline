# Pattyline

Pattyline is a Python CLI for turning ideas or existing specifications into human-approved tickets, then orchestrating sequential local/cloud implementation, verification, independent review and GitHub integration.

**This is an installable development build, not a release-ready automation tool.** Offline configuration, ticket validation, planning and approval are implemented. Live preparation needs a configured cloud model and capability probe. End-to-end execution currently remains blocked by an unfinished GitHub freshness capability gate, even with valid credentials and runtime probes. Native Windows setup/check isolation is also unavailable.

## Installation

You need **Python 3.11+**, Git, and pip for the wheel installation below. Pattyline has no third-party Python runtime dependencies. Workflow instructions are bundled; no personal agent skill installation is needed.

```text
git clone https://github.com/UeMaBel/pattyline.git
cd pattyline
```

### Windows (PowerShell)

These examples use Python 3.12; select an installed version that is at least 3.11.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip wheel --no-build-isolation --no-deps --wheel-dir dist .
.\.venv\Scripts\python.exe -m pip install --no-index .\dist\pattyline-0.1.0-py3-none-any.whl
.\.venv\Scripts\pattyline.exe --help
```

Optionally activate with `.\.venv\Scripts\Activate.ps1` to use `pattyline` directly. If activation is blocked by PowerShell, use the full executable path; activation is optional.

### Linux

Install your distribution's Python venv/pip packages first if they are missing.

```bash
python3 -m venv .venv
.venv/bin/python -m pip wheel --no-build-isolation --no-deps --wheel-dir dist .
.venv/bin/python -m pip install --no-index dist/pattyline-0.1.0-py3-none-any.whl
source .venv/bin/activate
pattyline --help
```

Building/installing the wheel needs no package downloads once Python, venv and pip are available. After source changes, rebuild and reinstall the wheel with `--force-reinstall`.

For source development, run `python -m pattyline --help` from this checkout. Here and below, `python` means your chosen Python 3.11+ interpreter. With uv, use `uv run --no-project python -m pattyline --help`. The custom backend supports wheels and source distributions, **not editable installs**: `pip install -e .` and default `uv run`/`uv sync` project installation are unsupported.

## Per-user setup

Run `pattyline setup` from any directory. Setup is per-user: it initializes Pattyline's global settings and does not read or change project files. On Windows, settings live under `%APPDATA%/pattyline/config.toml`; on Linux, they live under `$XDG_CONFIG_HOME/pattyline/config.toml` or `~/.config/pattyline/config.toml`.

Setup checks Git, GitHub CLI (`gh`), and Codex. It reports installed versions and, when publisher version information is available, flags outdated tools. For each missing or outdated tool, review the displayed source and version, then answer `y` to install or upgrade that item. Each item gets its own prompt. If Pattyline cannot install a tool on your platform, follow the official link shown and rerun setup.

### Debug Pattyline from a checkout

To run the current source without building a wheel, use `uv run python -m pattyline` from the repository root. For example:

```powershell
uv run python -m pattyline setup
```

To make the `pattyline` command use the checkout in your active environment, install it once in editable mode:

```powershell
uv pip install -e .
```

The editable install points at the source tree, so code changes are picked up without rebuilding a wheel. If editable installation fails, run the module directly with `uv run python -m pattyline` instead.

After checking tools, setup checks Codex's native ChatGPT sign-in. If Codex is installed but not signed in with ChatGPT, setup asks whether to start Codex's login flow. Answer `y` and complete the browser or terminal steps Codex presents. Codex stores and manages its own credentials; Pattyline does not save the secret or edit Codex's configuration. If you decline or setup is noninteractive, run `codex login` yourself and then rerun `pattyline setup`.

Setup is ready when Git, `gh`, and Codex are installed and Codex reports an authenticated ChatGPT session. Updates are optional: declining one leaves the installed version in place, and setup can still be ready. GitHub CLI sign-in is not part of this step. Setup also does not install local model runtimes or containers; those depend on the project and are handled later.

For one specific installation, use `--install` to review that tool's plan and prompt for consent:

```text
pattyline setup --install git
```

You can request a particular tool version with `--tool-version VERSION`. `--yes` applies only to the single `--install` item; it cannot approve all tools in one run.

This setup readiness is separate from execution readiness. The current worker broker still requires `OPENAI_API_KEY`, and project execution may also need GitHub authentication, Docker, an approved tool image, and runtime capability evidence.

## Initialize your project

After per-user setup is complete, run Pattyline in the repository it should work on. Keep the installed executable on PATH, or use its absolute path after changing directories.

```text
cd /path/to/your/project
pattyline init --defaults independent
pattyline validate --scope config
```

`init` shows the important defaults and lets you choose whether to snapshot your global defaults or use independent local defaults. It creates project-specific `pattyline.toml` and adds `.pattyline/` to `.gitignore`; repeated initialization preserves settings. To change this project's parameters later, edit `pattyline.toml` and run `pattyline validate --scope config`. Global defaults are only copied at initialization, so later global changes do not overwrite project settings. Project setup will determine whether GitHub authentication, local inference, or container tools are needed. For a new repository, initialize Git and create a first commit before execution; integration also needs a matching GitHub remote and base branch.

Global options go **before** the subcommand:

```text
pattyline --project /path/to/your/project --json plan
```

Commands emit JSON. `--json` also requests detailed evidence from `status`. Exit codes are `0` for success, `2` for blocked/invalid operations, and `130` for interruption.

## Configure dependencies and runtime

| Task | Requirements |
| --- | --- |
| Offline configuration, ticket validation and planning | Python, Git, project configuration and source documents |
| Live feature preparation | Explicit cloud model, API credential and real `cloud:preparation` probe |
| Cloud implementation/review | Cloud model/credential, Docker running Linux containers, approved local tool image and matching worker probes |
| Local implementation | Docker/tool image, running loopback Ollama, installed local model and `local:implementation` probe; cloud review is still mandatory |
| GitHub integration | Authenticated GitHub CLI, repository permissions, matching remote, merge commits and strict required checks; currently blocked by the unfinished freshness gate |

Edit the generated `pattyline.toml` for your project:

- `repository`: GitHub `owner/name`, matching `remote` (default `origin`).
- `base`: integration branch (default `main`).
- `tickets`: globs for the native tickets you want discovered; default `docs/pattyline/*/issues/*.md`.
- `instructions`: project-relative instruction files included in approvals and protected during execution.
- `checks` and optional `setup`: structured commands with an `argv` array, project-relative `cwd`, and timeout in seconds.
- `runtime.cloud_model`: explicit cloud model; set `runtime.local_model` too if using local implementation.
- `runtime.container_image`: full local image ID (`sha256:` plus 64 hexadecimal characters). The image must contain Python and the tools/dependencies your commands need. Images are never pulled automatically.
- `runtime.container_python`: Python executable inside that image (default `python3`).

Keep `runtime.adapter = "broker"` and `runtime.execution_platform = "linux"` for the implemented container route. Tools/checks run in Linux containers on either host OS; native Windows-only checks require another adapter and currently block this route.

For a Python unittest project, a check can look like this:

```toml
[checks.unit.linux]
argv = ["python3", "-m", "unittest", "discover", "-s", "tests"]
cwd = "."
timeout = 120
```

Adapt commands to your project. If `checks` is already an inline table, edit that table or convert it to sections first; do not declare the same TOML key twice. Tool/setup/check execution has no network, so dependencies must already be available in the approved environment.

For the current broker worker, supply the cloud API credential in the environment variable named by `runtime.api_key_env` (default `OPENAI_API_KEY`). Keep credentials outside TOML and committed files. Codex ChatGPT sign-in is used by setup but is not an API key for this broker route. GitHub authentication is a separate project step:

```text
gh auth login
gh auth status
```

The shared setup command installs only Git, GitHub CLI, and Codex. For a selected version, replace `VERSION` with the version you chose:

```text
pattyline setup --install git --tool-version VERSION
```

Existing shared tools are reused; upgrades require your consent. Local runtime installation, model downloads, container setup, and worker probes are not part of `pattyline setup` in this workflow. Configure those only when a project needs them, using that project's runtime instructions and readiness checks.

## Workflow: idea to approved tickets

Use a lowercase feature slug such as `search`. Run preparation and human approval commands from your own terminal. Worker sessions, including those marked with `CODEX_THREAD_ID`, cannot perform these human actions.

### 1. Interview and specification

```text
pattyline prepare search --answer "Add search over published articles."
pattyline prepare search --show
```

Read the returned decisions/questions and files under `docs/pattyline/search/`. Continue with `prepare search --answer "..."` until decisions are settled and open questions resolved. Then confirm and generate the specification:

```text
pattyline prepare search --confirm
pattyline prepare search
```

Confirmation records settled glossary/ADR content and advances to specification. The next call generates `spec.md`; inspect it before continuing. `preparation.json`, `handoff.md` and `index.md` preserve the frontier so you can return to the feature later. `--show` displays/saves state without model dispatch.

### 2. Breakdown, optional preparation review and publication

```text
pattyline prepare search
pattyline prepare search --preparation-review yes --show
```

The breakdown call produces `ticket-proposal.md` and structured tickets. Inspect scope, dependencies, routes, checks and acceptance criteria. Resolve questions with `--answer` and rerun preparation as needed. Choose `--preparation-review yes` or `no` explicitly, then approve the proposal:

```text
pattyline prepare search --approve-breakdown
```

If you chose review, run `pattyline prepare search` again to perform it. A changes-required result returns to breakdown refinement and requires fresh breakdown approval. Once the stage is `approval`, publish:

```text
pattyline prepare search --publish
pattyline validate --scope tickets
```

Publication writes **unapproved native drafts** under `docs/pattyline/search/issues/`. Ensure `tickets` includes their paths. Preparation-review choice never disables the mandatory independent cloud implementation review.

### 3. Execution approval

```text
pattyline approve search
pattyline plan search
```

`approve` displays exact effective requirements and asks for confirmation. Select `search/01` for one ticket; `--yes` records explicit human consent without prompting. Breakdown approval and execution approval are separate. Changed requirements, instructions or relevant configuration invalidate affected approvals.

Commit the configuration, specification, ticket files (including approval changes) and instructions yourself before starting. Pattyline does not stage primary-checkout files for you.

## Workflow: existing specifications and tickets

For native tickets, configure their paths and named checks, then validate, refine and approve. A native ticket contains exactly one TOML metadata block. For example, `docs/pattyline/search/issues/01-task.md`:

````markdown
# 01: Search published articles

```toml
schema_version = 1
id = "search/01"
type = "task"
status = "draft"
llm_target = "cloud"
specification = "../spec.md"
dependencies = []
verification = [{name = "unit", phase = "baseline"}]
scope = ["src/search.py", "tests/test_search.py"]
cloud_escalation_allowed = false
```

## Acceptance criteria

- [ ] Search includes matching published articles and excludes drafts.
````

The specification must exist, check names must be configured, IDs must match the enclosing feature, and dependencies must resolve without cycles. Baseline checks must pass on the base; introduced checks become applicable after implementation. Scope patterns are project-relative.

For legacy Markdown, preview selected files first:

```text
pattyline init --adopt docs/pattyline/search/issues/01-task.md
```

After inspecting the preview, supply the existing specification, configured checks and scope:

```text
pattyline init --adopt docs/pattyline/search/issues/01-task.md --specification docs/pattyline/search/spec.md --check baseline:unit --scope src/search.py --scope tests/test_search.py --apply --yes
```

Adoption preserves historical text and adds unapproved metadata requiring triage. Previously `claimed`/`resolved` tickets also need `--reconcile-history`; historical completion never establishes execution evidence. Reconstruct a missing specification through preparation first. Include adopted paths in `tickets`, then validate and approve.

## Execution, reports and recovery

These commands describe the execution interface; **the current GitHub capability gate prevents production dispatch**. Offline checks and runtime probes cannot remove it.

```text
pattyline validate
pattyline plan search/01
pattyline start search/01
```

`validate --scope config` checks configuration only. `--scope tickets` checks ticket contracts and reports approval reasons. Bare `validate` checks execution prerequisites without worker dispatch and may inspect Docker, Ollama or GitHub. Planning includes blocking prerequisites, not reverse dependents.

When execution becomes available, `start` freezes approved selection and runs sequential implementation, checks, independent cloud review and normal merge integration in isolated branches/worktrees. Bare `start` selects approved tickets. `start --yolo` skips only the dispatch prompt; it cannot approve drafts or bypass gates. `--run-seconds N` tightens the approved deadline. Default budgets allow one initial implementation and at most two repairs, with optional explicitly approved cloud escalation.

```text
pattyline status
pattyline --json status RUN_ID
pattyline cancel RUN_ID
pattyline resume RUN_ID
```

Replace `RUN_ID` with the returned run ID; omitting it selects the latest run. Status points to Markdown/JSON reports under `.pattyline/reports/`. Cancellation persists a stop request. Resume reconciles prior work/external effects against unchanged frozen requirements; changed requirements require an approved successor. Uncertain process ownership blocks automatic recovery.

If a ticket declares human/external acceptance, collect its project-local evidence and attest the exact implementation commit:

```text
pattyline approve search/01 --attest ui --evidence evidence/search-ui.md
pattyline resume RUN_ID
```

`ui` must match an `external_verification` requirement declared in the ticket. Changed evidence or implementation invalidates the attestation. See the [operator guide](docs/pattyline/v1/operator-guide.md) for its metadata format.

```text
pattyline clean
pattyline clean --apply --yes
```

`clean` previews Pattyline-generated project data (`.pattyline/`, `pattyline.toml`, and only the `.pattyline/` entry in `.gitignore`) plus eligible worktrees. Apply requires confirmation; `--yes` supplies it explicitly. The command preserves unfinished or uncertain work and refuses to remove project state while such work or an active run exists. Cleaning removes saved reports, evidence, and configuration, so review the preview before applying.

## Share configuration across projects

Global defaults are opt-in snapshots:

```text
pattyline config save-global runtime limits --yes
pattyline init --defaults global
pattyline config refresh
pattyline config refresh runtime limits --yes
```

Run `init --defaults global` in the new project. Only `runtime`, `permissions`, `limits` and `escalation` can be shared; repository identity, tickets and checks stay local. Refresh previews differences; applying selected changes can invalidate approvals. Global configuration lives at `%APPDATA%/pattyline/config.toml` on Windows or `$XDG_CONFIG_HOME/pattyline/config.toml` (default `~/.config/pattyline/config.toml`) on Linux.

## Development and verification

From this checkout, using your chosen interpreter:

```text
python scripts/check.py all
python scripts/check.py static
python scripts/check.py release-live
```

With uv, prefix with `uv run --no-project`, for example `uv run --no-project python scripts/check.py all`. On Windows, `py -3.12` is another option if installed.

`all` runs deterministic unittest coverage, including offline wheel installation, bundled resources and entry points. `static` compiles sources and checks whitespace. Package tests need venv/pip support. `release-live` reports missing real evidence and currently exits **2**; deterministic tests cannot establish release readiness.

## Current limits and troubleshooting

- GitHub integration always reports missing strict-rule/base-race capability proof. No configuration switch bypasses it.
- The Codex production adapter remains blocked. The broker requires trusted real evidence for each used profile.
- Native Windows setup/check sandboxing is unimplemented. Windows-host Linux-container probes do not establish native Linux or native Windows release coverage.
- Full platform, dependent local/cloud execution, escalation, crash/cancellation and actual consumer scenarios still need live evidence.
- Preparation uses supplied source text and a read-only environment inventory tool. Arbitrary project research commands are unsupported in that adapter.
- Tool execution has no network or provider/GitHub credentials. Hard monetary caps and deployment are unsupported.

For missing tools, rerun `setup` after installation. For local model errors, check the configured model and loopback Ollama service. For capability errors, resolve the reported prerequisites and rerun the matching real probe. Missing GitHub freshness proof and Windows isolation require implementation/evidence work; reinstalling tools will not fix them.

## Further reading

- [V1 specification](docs/pattyline/v1/spec.md)
- [Implementation tickets and verification catalog](docs/pattyline/v1/tickets.md)
- [Independent review findings](docs/pattyline/v1/review.md)
- [Proposed replacement ticket breakdown](docs/pattyline/v1/proposed-breakdown.md)
- [Runtime and skill-bundling research](docs/pattyline/v1/runtime-research.md)
- [Design interview decisions](pattyline-ideas.md)
- [Domain glossary](GLOSSARY.md)
- [Architectural decisions](docs/adr/)
- [Operator guide and implemented limits](docs/pattyline/v1/operator-guide.md)
- [Durable implementation progress](docs/pattyline/v1/implementation-progress.md)
- [Approved 22-slice implementation queue](docs/pattyline/implementation-v1/tickets.md)

The revised 22-slice breakdown and baseline/introduced-check policy were approved for this development session on 2026-11-01. Original 21 tickets are superseded drafts. Product implementation review remains mandatory. No personal skill installation is needed; bundled instructions/resources are Pattyline-owned and MIT-licensed.

`website_templates/` is the first consumer's reference snapshot. It is not an instruction to execute those tickets or deploy the website.

License: [MIT](LICENSE).
