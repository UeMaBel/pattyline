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
from .runtime import record_inventory
from .state import State
from .tickets import adopt, discover, legacy_preview, order, source_candidates


def parser():
    result = argparse.ArgumentParser(prog="pattyline", description="Prepare and execute explicitly approved local/cloud tickets")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--project", type=Path, default=Path.cwd())
    result.add_argument("--json", action="store_true", help="Emit machine-readable JSON (also used for detailed evidence)")
    commands = result.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Inventory tools; preview consented installations/model downloads")
    setup.add_argument("--local", action="store_true")
    setup.add_argument("--install", choices=sorted(onboarding.SOURCES))
    setup.add_argument("--tool-version")
    setup.add_argument("--download-model")
    setup.add_argument("--yes", action="store_true", help="Explicit installation/download consent after reading the plan")
    setup.add_argument("--probe", action="store_true", help="Record read-only runtime inventory; no inference or capability-pass claim")
    setup.add_argument("--probe-worker", choices=["cloud:implementation", "cloud:review", "cloud:preparation", "local:implementation"], help="Opt-in bounded real model/container capability probe; requires --yes")
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
    cleaning = commands.add_parser("clean", help="Preview safe inactive worktree cleanup")
    cleaning.add_argument("--apply", action="store_true")
    cleaning.add_argument("--yes", action="store_true")
    return result


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


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


def execute(args):
    command = args.command
    if command == "setup":
        settings_path = config.global_path()
        root = settings_path.parent
        created = not settings_path.exists()
        values = config.initialize_global()
        result = onboarding.inventory(values)
        result["global_settings"] = {"path": str(settings_path), "status": "initialized" if created else "preserved"}
        if args.install:
            install_plan = onboarding.installation_plan(args.install, values, args.tool_version)
            emit(install_plan)
            if args.yes:
                human()
                result["installation"] = onboarding.install(root, install_plan, True)
        if args.download_model:
            emit(onboarding.model_plan(values, args.download_model))
            if args.yes:
                human()
                result["model_download"] = onboarding.download_model(root, values, args.download_model, True)
        if args.local:
            result["local"] = onboarding.local_inventory(values)
        if args.probe:
            result["runtime_inventory"] = record_inventory(root, values)
        if args.probe_worker:
            emit({"live_probe": args.probe_worker, "maximum_seconds": 60, "token_limit_per_request": values["runtime"]["max_output_tokens"], "cost": "unknown; not a guaranteed monetary cap"})
            confirm("Run real bounded model inference and container denial exercises?", args.yes)
            from .runtime import adapter
            runtime = adapter(root, values)
            if not hasattr(runtime, "probe"):
                raise Blocked("Selected runtime has no verified live-probe implementation")
            route, stage = args.probe_worker.split(":")
            result["live_probe"] = runtime.probe(route, stage)
        return result

    root = primary(args.project)
    if command == "init":
        choice = args.defaults
        if not (root / "pattyline.toml").exists() and config.global_path().exists() and choice is None:
            emit({"global_defaults": tomllib.loads(config.global_path().read_text(encoding="utf-8")), "choices": ["global", "independent"], "customize": "--set KEY=JSON"})
            if not sys.stdin.isatty():
                raise Blocked("Choose --defaults global or --defaults independent; global settings are snapshotted only with consent")
            choice = input("Use global defaults or independent setup? [global/independent] ").strip()
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
        return {"configuration": str(root / "pattyline.toml"), "status": "initialized; execution readiness not yet established",
                "discovered_sources": source_candidates(root), "adoption": "Preview selected files with init --adopt PATH ...; additional project-relative paths are accepted",
                "next": "pattyline setup; pattyline prepare FEATURE (new idea or refinement)"}
    values = config.load(root)
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
            emit(preview)
            confirm("Remove only clean inactive verified-integrated worktrees?", args.yes)
            clean(root, values, True)
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
        emit(execute(args))
        return 0
    except (Blocked, tomllib.TOMLDecodeError, ValueError, OSError) as error:
        emit({"error": str(error), "status": "blocked"})
        return 2
    except KeyboardInterrupt:
        emit({"error": "Interrupted; work/state preserved. Use status then resume to reconcile.", "status": "interrupted"})
        return 130
