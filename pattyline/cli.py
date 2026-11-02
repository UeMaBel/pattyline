"""Public command contract; required human choices never default silently."""
import argparse
import json
import os
from pathlib import Path
import sys
import tomllib

from . import __version__, approval, config, onboarding
from .cleanup import clean
from .common import Blocked, canonical
from .engine import Engine, plan
from .git import primary
from .preparation import Preparation, unfinished
from .state import State
from .terminal import Spinner
from .tickets import adopt, discover, legacy_preview, order, source_candidates


def parser():
    result = argparse.ArgumentParser(prog="pattyline", description="Prepare and execute explicitly approved local/cloud tickets")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--project", type=Path, default=Path.cwd())
    result.add_argument("--json", action="store_true", help="Emit machine-readable JSON (also used for detailed evidence)")
    commands = result.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Check shared tools and initialize per-user setup")
    setup.add_argument("--install", choices=onboarding.SHARED_COMPONENTS)
    setup.add_argument("--tool-version")
    setup.add_argument("--yes", action="store_true", help="Consent to this one --install action after reviewing its plan")
    init = commands.add_parser("init", help="Create a project-local configuration snapshot or adopt selected tickets")
    init.add_argument("--defaults", choices=["global", "independent"])
    init.add_argument("--set", action="append", default=[], metavar="KEY=JSON")
    init.add_argument("--adopt", nargs="+")
    init.add_argument("--specification")
    init.add_argument("--check", action="append", default=[], metavar="baseline:NAME|introduced:NAME")
    init.add_argument("--scope", action="append", default=[])
    init.add_argument("--reconcile-history", action="store_true")
    init.add_argument("--apply", action="store_true")
    init.add_argument("--yes", action="store_true")
    prepare = commands.add_parser("prepare", help="Start/resume preparation with durable handoffs")
    prepare.add_argument("feature", nargs="?")
    prepare.add_argument("--answer")
    prepare.add_argument("--confirm", action="store_true")
    prepare.add_argument("--preparation-review", choices=["yes", "no"])
    prepare.add_argument("--approve-breakdown", action="store_true")
    prepare.add_argument("--show", action="store_true")
    prepare.add_argument("--publish", action="store_true", help="Publish approved breakdown as unapproved native execution drafts")
    approve = commands.add_parser("approve", help="Show requirements and record exact human approval")
    approve.add_argument("selection", nargs="+")
    approve.add_argument("--yes", action="store_true")
    approve.add_argument("--attest", metavar="REQUIREMENT", help="Attest declared human/external acceptance for the exact implementation commit")
    approve.add_argument("--evidence", type=str)
    cfg = commands.add_parser("config", help="Preview/apply explicitly selected global defaults")
    cfg.add_argument("action", choices=["refresh", "save-global"])
    cfg.add_argument("keys", nargs="*")
    cfg.add_argument("--yes", action="store_true")
    validate = commands.add_parser("validate", help="Check contracts and readiness without worker dispatch")
    validate.add_argument("--scope", choices=["config", "tickets", "cloud", "local", "execution"], default="execution")
    planning = commands.add_parser("plan", help="Explain selection and prerequisite closure without dispatch")
    planning.add_argument("selection", nargs="*")
    start = commands.add_parser("start", help="Freeze an approved selection and execute sequentially")
    start.add_argument("selection", nargs="*")
    start.add_argument("--all-ready", action="store_true")
    start.add_argument("--yolo", action="store_true")
    start.add_argument("--run-seconds", type=int)
    for name in ("status", "resume", "cancel"):
        sub = commands.add_parser(name)
        sub.add_argument("run", nargs="?")
    cleaning = commands.add_parser("clean", help="Preview and remove Pattyline project data and safe inactive worktrees")
    cleaning.add_argument("--apply", action="store_true")
    cleaning.add_argument("--yes", action="store_true")
    return result


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def print_setup_report(result):
    """Render setup status for people; --json retains the structured report."""
    print("\nPattyline setup")
    print("==============")
    print("\nTools")
    for name, tool in result["tools"].items():
        version = tool.get("version") or "not found"
        state = tool["presence"]
        if state == "installed" and tool.get("update") == "outdated":
            state = "update available"
        elif state == "installed":
            state = "up to date" if tool.get("update") == "current" else "installed"
        print(f"  {name:<7} {version} ({state})")
    authentication = result["authentication"]["codex_chatgpt"]
    auth_text = "signed in with ChatGPT" if authentication == "authenticated" else authentication.replace("-", " ")
    print(f"\nCodex: {auth_text}")
    if result["readiness"]["ready"]:
        print("\nStatus: READY - Pattyline setup is complete. You can continue.")
        declined = [action["component"] for action in result["actions"] if action["status"] == "declined"]
        if declined:
            print("Skipped optional updates: " + ", ".join(declined) + ". Installed versions remain available.")
        print("Next: run `pattyline init` in the project you want Pattyline to work on.")
    else:
        print("\nStatus: INCOMPLETE - a required setup step needs attention.")
        print("Next: " + result["readiness"].get("next_step", "install missing tools, then rerun pattyline setup"))


def print_clean_report(result):
    if result.get("removed"):
        print("Cleaned! Pattyline project files and eligible worktrees have been removed.")
        return
    print("Pattyline clean preview")
    for artifact in result.get("artifacts", []):
        print(f"  {artifact['action']}: {artifact['path']}")
    for worktree in result.get("worktrees", []):
        if worktree["action"] == "remove worktree":
            print(f"  Remove worktree {worktree['ticket']}: {worktree['path']}")
        else:
            print(f"  Keep worktree {worktree['ticket']}: {worktree['reason']}")
    if result.get("blocked"):
        print("Cannot remove project state: " + "; ".join(result["blocked"]))
    else:
        print("\nRun `pattyline clean --apply --yes` to remove the listed Pattyline files and eligible worktrees.")


def print_init_options(values):
    runtime = values["runtime"]
    limits = values["limits"]
    print("\nPattyline project defaults")
    print("  Cloud model:       " + (runtime.get("cloud_model") or "not selected"))
    print("  Local model:       " + (runtime.get("local_model") or "not configured"))
    print("  Execution:         " + runtime.get("adapter", "broker") + " on " + runtime.get("execution_platform", "linux"))
    print("  Repair attempts:   " + str(limits.get("repairs", "not set")))
    print("  Run time limit:    " + str(limits.get("run_seconds", "not set")) + " seconds")
    print("  Network access:    " + ("enabled" if values["permissions"].get("network") else "disabled in worker tools"))


def print_init_report(result):
    if "configuration" not in result:
        emit(result)
        return
    print("\nProject initialized")
    print("  Local settings: " + result["configuration"])
    print("  This file is pattyline.toml in the project root; edit it to change this project's parameters.")
    print("  Project state:  .pattyline/ (ignored by Git)")
    print("Global defaults are only a starting snapshot; project settings remain local.")
    print("Run `pattyline validate --scope config` after editing to check the configuration.")
    print("\nNext: " + result["next"])


def human():
    if os.environ.get("PATTYLINE_WORKER") or os.environ.get("CODEX_THREAD_ID"):
        raise Blocked("Worker sessions cannot perform human approvals, installations or policy changes")


def confirm(message, yes=False):
    human()
    if yes:
        return
    if not sys.stdin.isatty():
        raise Blocked(message + "; explicit human action required from an interactive terminal")
    if input(message + " [y/N] ").lower().strip() not in {"y", "yes"}:
        raise Blocked("Human declined; no action performed")


def select(tickets, selection):
    identities = []
    for name in selection:
        matches = [i for i in tickets if i == name or i.split("/")[0] == name]
        if not matches:
            raise Blocked(f"Unknown ticket or feature {name}")
        identities.extend(matches)
    return list(dict.fromkeys(identities))


def print_burgerpatty():
    print(r"""              .-=========-.
             /  .  .  .  .  \
            /               \
           /=================\
           |^^^^^^^^^^^^^^^^^|
           \-----------------/
           |~~~~~~~~~~~~~~~~~|
           /=================\
           |#################|
            \_______________/""")


def setup(args):
    if not args.json:
        if getattr(args, "show_banner", True):
            print_burgerpatty()
        print("Welcome to Pattyline! Setup is checking Git, GitHub CLI, Codex, and your Codex sign-in.")
        print("It will show any missing or outdated tools and guide you through the next steps.")
    settings_path = config.global_path()
    root = settings_path.parent
    created = not settings_path.exists()
    values = config.initialize_global()
    if args.yes and not args.install:
        raise Blocked("--yes applies to one --install component only; setup asks separately for each tool")
    if args.tool_version and not args.install:
        raise Blocked("--tool-version applies to one --install component only")

    result = onboarding.tool_inventory(values)
    result["global_settings"] = {"path": str(settings_path), "status": "initialized" if created else "preserved"}
    tools = result["tools"]
    candidates = [args.install] if args.install else [name for name, tool in tools.items()
                                                      if tool["presence"] == "missing" or tool["update"] == "outdated"]
    result["actions"] = []
    for component in candidates:
        tool = tools[component]
        upgrade = tool["presence"] == "installed"
        if upgrade and tool["update"] != "outdated" and args.install:
            result["actions"].append({"component": component, "status": "reused", "version": tool["version"]})
            continue
        target_version = args.tool_version or tool.get("latest_version")
        install_plan = onboarding.installation_plan(component, values, target_version, upgrade=upgrade)
        if args.json:
            # Keep stdout as one valid JSON document; actions appear in the final report.
            pass
        else:
            print(f"\n{install_plan['action'].capitalize()} {component} {install_plan['version']}")
            print(f"  Source: {install_plan['source']}")
            if install_plan["argv"]:
                print("  Pattyline will run the official package manager after your approval.")
            else:
                print("  Install manually using the official source, then rerun setup.")
        consent = bool(args.yes)
        if not consent and sys.stdin.isatty() and install_plan["argv"]:
            human()
            answer = input(f"Proceed with {install_plan['action']} of {component} {install_plan['version']}? [y/N] ")
            consent = answer.strip().lower() in {"y", "yes"}
        if consent:
            human()
            with Spinner(f"Installing {component} {install_plan['version']} (this may take a few minutes)"):
                result["actions"].append(onboarding.install(root, install_plan, True))
        elif install_plan["argv"]:
            result["actions"].append({"component": component, "status": "declined" if sys.stdin.isatty() else "consent required",
                                      "next_step": f"rerun setup with --install {component} after reviewing the plan"})
        else:
            result["actions"].append({"component": component, "status": "manual installation required",
                                      "source": install_plan["source"],
                                      "next_step": "follow the official installation instructions, then rerun pattyline setup"})

    # Re-inventory after installations so readiness reflects verified availability.
    result.update(onboarding.tool_inventory(values))
    tools = result["tools"]
    auth = onboarding.codex_auth_status(values)
    login = {"status": "not attempted"}
    if auth["status"] in {"not-authenticated", "other-authentication"} and tools["codex"]["presence"] == "installed":
        login = {"status": "required", "next_step": "run pattyline setup again and choose yes when asked to start Codex's native ChatGPT sign-in"}
        if sys.stdin.isatty() and sys.stdout.isatty():
            human()
            if input("Codex is not signed in with ChatGPT. Start its native sign-in now? [y/N] ").strip().lower() in {"y", "yes"}:
                login = onboarding.codex_login(values, root)
                auth = onboarding.codex_auth_status(values)
                if login["status"] == "completed" and auth["status"] != "authenticated":
                    login = {"status": "verification-failed",
                             "next_step": "Codex sign-in did not verify as ChatGPT-authenticated; run codex login status and rerun pattyline setup"}
            else:
                login = {"status": "declined", "next_step": "run codex login, then rerun pattyline setup"}
    elif auth["status"] != "authenticated":
        login = {"status": "unavailable", "next_step": "install Codex, or run codex login status to diagnose authentication, then rerun pattyline setup"}

    result["authentication"] = {"codex_chatgpt": auth["status"], "login": login["status"]}
    # Updates are optional: an installed older version can still be used after
    # the user declines the offered upgrade. Missing tools still block readiness.
    required_tools = all(tools[name]["presence"] == "installed" for name in onboarding.SHARED_COMPONENTS)
    ready = required_tools and auth["status"] == "authenticated"
    result["readiness"] = {"ready": ready, "status": "ready" if ready else "incomplete"}
    if not ready:
        result["readiness"]["next_step"] = login.get("next_step", "install or update the listed tools, then rerun pattyline setup")
    return result


def execute(args):
    command = args.command
    if command == "setup":
        return setup(args)

    setup_result = None
    if command == "init" and not args.json:
        print_burgerpatty()
    if command == "init" and not config.global_path().exists():
        # A missing per-user settings file means first-run setup has not been
        # performed. Run the same inventory/login flow before project init.
        setup_args = argparse.Namespace(json=args.json, yes=False, install=None, tool_version=None, show_banner=False)
        setup_result = setup(setup_args)
        if not args.json:
            print_setup_report(setup_result)

    root = primary(args.project)
    if command == "init":
        choice = args.defaults
        if not (root / "pattyline.toml").exists() and config.global_path().exists() and choice is None:
            global_values = config.merge(config.DEFAULTS, {key: value for key, value in
                                            tomllib.loads(config.global_path().read_text(encoding="utf-8")).items()
                                            if key in config.GLOBAL_KEYS})
            if not args.json:
                print_init_options(global_values)
            if not sys.stdin.isatty():
                raise Blocked("Choose --defaults global or --defaults independent; global settings are snapshotted only with consent")
            choice = input("Use these global defaults for this project, or keep independent local defaults? [global/local] ").strip().lower()
            choice = "independent" if choice == "local" else choice
            if choice not in {"global", "independent"}:
                raise Blocked("Explicit defaults choice required")
        values = config.initialize(root, choice == "global")
        if args.set:
            human()
            for assignment in args.set:
                key, separator, value = assignment.partition("=")
                if not separator or key not in values:
                    raise Blocked("--set requires an existing top-level KEY=JSON")
                values = config.merge(values, {key: json.loads(value)})
            config.save(root, values)
        if args.adopt:
            previews = legacy_preview(root, args.adopt)
            emit({"adoption_preview": previews})
            if args.apply:
                if not args.specification or not args.check or not args.scope:
                    raise Blocked("Adoption needs --specification, --check phase:name and --scope; incomplete inputs need refinement")
                verification = []
                for declared in args.check:
                    phase, separator, name = declared.partition(":")
                    if not separator or phase not in {"baseline", "introduced"} or name not in values["checks"]:
                        raise Blocked("--check must be baseline:NAME or introduced:NAME referencing a configured check")
                    verification.append({"name": name, "phase": phase})
                confirm("Adopt selected sources as unapproved drafts, preserving historical text?", args.yes)
                result = adopt(root, previews, args.specification, verification, args.scope, args.reconcile_history)
                return {"adopted": result, "next": "Refine drafts, include their glob in config, validate and approve"}
            return {"result": "preview only; no ticket modified"}
        next_step = "pattyline prepare FEATURE (new idea or refinement)"
        if setup_result is not None and not setup_result["readiness"]["ready"]:
            next_step = "Complete the reported setup steps, then run pattyline setup before preparing a feature"
        result = {"configuration": str(root / "pattyline.toml"), "status": "initialized; execution readiness not yet established",
                  "discovered_sources": source_candidates(root), "adoption": "Preview selected files with init --adopt PATH ...; additional project-relative paths are accepted",
                  "next": next_step}
        if setup_result is not None:
            result["setup"] = {"readiness": setup_result["readiness"], "global_settings": setup_result["global_settings"]}
        if not args.json:
            print_init_options(values)
        return result
    values = config.load(root) if (root / "pattyline.toml").exists() else config.DEFAULTS
    if command == "config":
        if args.action == "save-global":
            if not args.keys or not set(args.keys) <= config.GLOBAL_KEYS:
                raise Blocked("Select global keys explicitly: " + ", ".join(sorted(config.GLOBAL_KEYS)))
            confirm("Save selected project settings as global defaults?", args.yes)
            existing = tomllib.loads(config.global_path().read_text(encoding="utf-8")) if config.global_path().exists() else {}
            from .common import atomic_write
            atomic_write(config.global_path(), config.toml_text(config.merge(existing, {k: values[k] for k in args.keys})))
            return {"saved": args.keys, "path": str(config.global_path())}
        preview = config.refresh(root)
        if args.keys:
            emit(preview)
            confirm("Apply selected global changes? Existing approvals will require reconciliation.", args.yes)
            config.refresh(root, args.keys)
        return {"changes": preview, "applied": args.keys}
    if command == "prepare":
        human()
        feature = args.feature
        if not feature:
            choices = unfinished(root)
            if len(choices) == 1:
                feature = choices[0]
            else:
                raise Blocked("Select a feature slug; unfinished features: " + ", ".join(choices))
        preparation = Preparation(root, values, feature)
        if args.answer:
            preparation.answer(args.answer)
        if args.preparation_review:
            preparation.choose_review(args.preparation_review)
        if args.show:
            return preparation.save()
        if args.publish:
            return preparation.publish()
        return preparation.advance(args.confirm, args.approve_breakdown)
    if command == "approve":
        tickets = discover(root, values)
        identities = select(tickets, args.selection)
        if args.attest:
            if len(identities) != 1 or not args.evidence:
                raise Blocked("Attestation needs one ticket, --attest REQUIREMENT and --evidence PATH")
            emit({"ticket": identities[0], "requirement": args.attest, "evidence": args.evidence})
            confirm("Attest this evidence for the exact current implementation commit?", args.yes)
            state = State(root)
            try:
                return approval.attest(root, tickets[identities[0]], values, state, args.attest, args.evidence)
            finally:
                state.close()
        emit({"approval_summary": {i: approval.effective(root, tickets[i], values) for i in identities},
              "implementation_review": "mandatory independent cloud review; never disabled by preparation-review choice"})
        confirm("Approve these exact requirements for execution?", args.yes)
        return {"approved": approval.approve(root, [tickets[i] for i in identities], values)}
    if command == "validate":
        if args.scope == "config":
            return {"valid": True, "scope": "configuration only", "execution_ready": False}
        tickets = discover(root, values) if args.scope in {"tickets", "execution"} else {}
        if args.scope == "tickets":
            reasons = {i: approval.reason(root, ticket, values) for i, ticket in tickets.items()}
            return {"valid": True, "tickets": reasons, "execution_ready": False}
        if args.scope in {"cloud", "local"}:
            from .runtime import adapter
            blockers = adapter(root, values).readiness(args.scope)
        else:
            engine = Engine(root, values)
            try:
                blockers = engine.readiness(order(tickets))
            finally:
                engine.state.close()
        if blockers:
            emit({"valid": False, "blockers": blockers, "execution_ready": False})
            raise Blocked("Execution readiness has unresolved prerequisites; see blockers")
        return {"valid": True, "execution_ready": True}
    if command == "plan":
        tickets = discover(root, values)
        selection = select(tickets, args.selection) if args.selection else None
        return plan(root, values, selection)
    if command in {"status", "cancel"}:
        state = State(root)
        try:
            run = state.run(args.run)
            if command == "cancel":
                state.cancel(run["id"])
                return {"run": run["id"], "stop_requested": True, "next": "Active executor observes stop; uncertain process termination preserves ownership"}
            report = state.report(run["id"])
            if args.json:
                return report
            return {"run": run["id"], "status": run["status"], "work": [{k: row[k] for k in ("ticket", "state", "stage", "reason", "attempts")} for row in report["work"] if row], "report": report["report"], "usage": report["usage"]}
        finally:
            state.close()
    if command == "clean":
        preview = clean(root, values)
        if args.apply:
            if preview.get("blocked"):
                raise Blocked("Cannot clean Pattyline data: " + "; ".join(preview["blocked"]))
            if not args.json:
                print_clean_report(preview)
            confirm("Remove the listed Pattyline project data and eligible worktrees?", args.yes)
            return clean(root, values, True)
        return preview
    if command in {"start", "resume"}:
        engine = Engine(root, values)
        try:
            if command == "resume":
                return engine.resume(args.run)
            tickets = discover(root, values)
            identities = select(tickets, args.selection) if args.selection else [i for i, t in tickets.items() if not approval.reason(root, t, values)]
            if not identities:
                raise Blocked("No approved tickets selected")
            emit(plan(root, values, identities))
            if not args.yolo:
                confirm("Dispatch this frozen approved selection?")
            return engine.start(identities, args.run_seconds)
        finally:
            engine.state.close()
    raise Blocked("Unknown command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        if args.command == "setup" and not args.json:
            print_setup_report(result)
        elif args.command == "clean" and not args.json:
            print_clean_report(result)
        elif args.command == "init" and not args.json:
            print_init_report(result)
        else:
            emit(result)
        return 0
    except (Blocked, tomllib.TOMLDecodeError, ValueError, OSError) as error:
        emit({"error": str(error), "status": "blocked"})
        return 2
    except KeyboardInterrupt:
        emit({"error": "Interrupted; work/state preserved. Use status then resume to reconcile.", "status": "interrupted"})
        return 130
