"""Durable human decision frontier with fresh sessions at stage boundaries."""
from importlib.resources import files
from pathlib import Path
import re
import unicodedata
import uuid

from .common import Blocked, atomic_write, digest, load_json, now, within, write_json
from .config import toml_text
from .runtime import adapter

STAGES = ("interview", "specification", "breakdown", "preparation-review", "approval")
SKILLS = {"interview": ["grilling", "domain-modeling"], "specification": ["to-spec", "handoff"],
          "breakdown": ["to-tickets", "triage"], "preparation-review": ["preparation-review"]}


def bundle(stage):
    base = files("pattyline") / "resources/workflow"
    result = {}
    for skill in SKILLS.get(stage, []):
        path = base / skill / "SKILL.md"
        result[skill] = path.read_text(encoding="utf-8")
    result["templates"] = (base / "templates.md").read_text(encoding="utf-8")
    return result


class Preparation:
    @staticmethod
    def slug(idea):
        value = unicodedata.normalize("NFKD", idea).encode("ascii", "ignore").decode("ascii").lower()
        value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
        return (value[:48].rstrip("-") or "new-idea")

    def __init__(self, root, config, feature, runtime=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", feature):
            raise Blocked("Feature must be a lowercase slug")
        self.root, self.feature = Path(root), feature
        self.directory = within(root, f"docs/pattyline/{feature}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "preparation.json"
        self.config = config
        self.runtime = runtime if runtime is not None else adapter(root, config)
        self.value = load_json(self.path, {"schema": 1, "feature": feature, "stage": "interview",
                 "decisions": [], "terms": {}, "open_questions": [], "messages": [], "sessions": [],
                 "confirmed": False, "review_choice": "undecided", "breakdown_approved": False, "progress": []})

    def save(self):
        self.value["updated"] = now()
        write_json(self.path, self.value)
        handoff = {key: self.value[key] for key in ("feature", "stage", "decisions", "terms", "open_questions", "confirmed", "review_choice", "progress")}
        handoff["authoritative_paths"] = [str(self.path.relative_to(self.root)), str((self.directory / "spec.md").relative_to(self.root))]
        handoff["evidence"] = self.value["sessions"]
        atomic_write(self.directory / "handoff.md", "# Durable preparation handoff\n\n```json\n" + __import__('json').dumps(handoff, indent=2) + "\n```\n")
        atomic_write(self.directory / "index.md", f"# {self.feature}\n\nStage: {self.value['stage']}\n\n[Handoff](handoff.md) | [State](preparation.json)\n")
        return self.value

    def answer(self, message):
        self.value["messages"].append({"actor": "human", "time": now(), "text": message})
        self.value["confirmed"] = False
        return self.save()

    def choose_review(self, choice):
        if choice not in {"yes", "no"}:
            raise Blocked("Choose independent preparation review: yes or no")
        self.value["review_choice"] = choice
        return self.save()

    def show_proposal(self):
        path = self.directory / "ticket-proposal.md"
        return {"feature": self.feature, "stage": self.value["stage"],
                "proposal": path.read_text(encoding="utf-8") if path.exists() else "",
                "tickets": self.value.get("proposed_tickets", []),
                "open_questions": self.value["open_questions"]}

    def confirm_proposal(self):
        if self.value["stage"] != "breakdown" or not self.value.get("proposed_tickets") or self.value["open_questions"]:
            raise Blocked("A complete ticket proposal without open questions is required")
        self.value["breakdown_confirmed"] = True
        self.value["breakdown_approved"] = True
        self.value["stage"] = "approval"
        return self.save()

    def advance(self, confirm=False, approve_breakdown=False):
        stage = self.value["stage"]
        if confirm:
            if self.value["open_questions"]:
                raise Blocked("Resolve open product questions before confirming shared understanding")
            if not self.value["decisions"]:
                raise Blocked("Interview has no settled decisions to confirm")
            self.record_domain()
            self.value["confirmed"] = True
            if stage == "interview":
                self.value["stage"] = "specification"
            self.save()
            return self.value
        if approve_breakdown:
            if stage != "breakdown" or not (self.directory / "ticket-proposal.md").exists():
                raise Blocked("Generate and inspect the proposed slices before approving the breakdown")
            if self.value["open_questions"]:
                raise Blocked("Resolve breakdown questions before publication")
            if self.value["review_choice"] == "undecided":
                raise Blocked("Choose whether independent preparation review is wanted")
            self.value["breakdown_approved"] = True
            self.value["stage"] = "approval"
            return self.save()
        if stage == "approval":
            return self.save()
        blockers = self.runtime.readiness("cloud", "preparation")
        if blockers:
            self.save()
            raise Blocked("Preparation saved; cloud session unavailable: " + "; ".join(blockers))
        session = uuid.uuid4().hex
        self.save()  # Durable handoff exists before invoking a new context.
        prompt = {"stage": stage, "session_request": session, "state": self.value,
                  "handoff": (self.directory / "handoff.md").read_text(encoding="utf-8"), "skills": bundle(stage),
                  "source_documents": {p.name: p.read_text(encoding="utf-8") for p in self.directory.glob("*.md")},
                  "project_instructions": {p: within(self.root, p).read_text(encoding="utf-8") for p in sorted({*self.config["instructions"], "AGENTS.md", "GLOSSARY.md"}) if within(self.root, p).is_file()}}
        response = self.runtime.execute("preparation", "cloud", self.directory, prompt, timeout=1800)
        returned = response.get("session")
        if not returned or any(s["session"] == returned for s in self.value["sessions"]):
            raise Blocked("Fresh preparation session identity required at every handoff")
        for key in ("decisions", "open_questions"):
            if not isinstance(response.get(key), list):
                raise Blocked(f"Preparation result requires {key}")
        self.value["sessions"].append({"session": returned, "stage": stage, "time": now()})
        self.value["decisions"] = response["decisions"]
        self.value["open_questions"] = response["open_questions"]
        self.value["terms"].update(response.get("terms", {}))
        self.value["progress"].append({"stage": stage, "summary": response.get("summary", ""), "time": now()})
        artifacts = response.get("artifacts", {})
        allowed = {"interview": {"interview.md", "glossary-draft.md", "adr-draft.md"},
                   "specification": {"spec.md"}, "breakdown": {"ticket-proposal.md"},
                   "preparation-review": {"preparation-review.md"}}[stage]
        if not set(artifacts) <= allowed:
            raise Blocked("Preparation agent proposed unauthorized artifact paths")
        for name, content in artifacts.items():
            if not isinstance(content, str) or not content.strip():
                raise Blocked(f"Preparation artifact {name} must be nonempty")
            atomic_write(self.directory / name, content)
        if stage == "specification" and not self.value["open_questions"]:
            if not (self.directory / "spec.md").exists():
                raise Blocked("Specification session did not produce a specification")
            self.value["stage"] = "breakdown"
        if stage == "preparation-review" and not self.value["open_questions"]:
            if response.get("verdict") != "approved":
                self.value["stage"] = "breakdown"
                self.value["breakdown_approved"] = False
            else:
                self.value["stage"] = "approval"
                self.value["review_verdict"] = "approved"
        if stage == "breakdown" and "tickets" in response:
            if not isinstance(response["tickets"], list) or not response["tickets"]:
                raise Blocked("Breakdown tickets must be a nonempty array")
            self.value["proposed_tickets"] = response["tickets"]
        return self.save()

    def record_domain(self):
        glossary = self.root / "GLOSSARY.md"
        text = glossary.read_text(encoding="utf-8") if glossary.exists() else "# Project glossary\n"
        additions = []
        for term, definition in self.value["terms"].items():
            if not isinstance(term, str) or not isinstance(definition, str) or not term.strip() or "\n" in term:
                raise Blocked("Settled terms require one-line names and concrete definitions")
            match = re.search(r"\*\*" + re.escape(term) + r"\*\*:\s*(.+?)(?=\n\s*\n|\Z)", text, re.S)
            if match:
                if " ".join(match[1].split()) != " ".join(definition.split()):
                    raise Blocked(f"Glossary term {term} conflicts with settled project vocabulary; reconcile it before confirmation")
            else:
                additions.append(f"**{term}**: {definition}")
        if additions:
            atomic_write(glossary, text.rstrip() + "\n\n" + "\n\n".join(additions) + "\n")
        draft = self.directory / "adr-draft.md"
        if draft.exists():
            target = self.root / "docs/adr" / f"{self.feature}-preparation.md"
            proposed = draft.read_text(encoding="utf-8")
            if target.exists() and target.read_text(encoding="utf-8") != proposed:
                raise Blocked("Existing ADR differs from confirmed draft; reconcile instead of overwriting history")
            atomic_write(target, proposed)

    def publish(self):
        from .tickets import parse, order, discover
        if self.value["stage"] == "complete":
            return {"published": self.value.get("published", []),
                    "status": "init complete; drafts require validation and human approval"}
        if self.value["stage"] not in {"approval", "breakdown"} or self.value["open_questions"]:
            raise Blocked("Ticket publication requires a settled proposal with no unresolved questions")
        if self.value["stage"] == "breakdown" and not self.value.get("breakdown_confirmed"):
            raise Blocked("Show the proposed tickets and confirm they match your intent before publication")
        proposed = self.value.get("proposed_tickets")
        if not proposed:
            raise Blocked("No structured ticket drafts; return to breakdown refinement")
        directory = self.directory / "issues"
        directory.mkdir(exist_ok=True)
        staged = []
        temporary_paths = []
        try:
            for item in proposed:
                number = item.get("number")
                if not isinstance(number, str) or not re.fullmatch(r"[0-9]+", number):
                    raise Blocked("Each proposed ticket needs an immutable numeric string number")
                data = {"schema_version": 1, "id": f"{self.feature}/{number}", "type": "task", "status": "draft",
                        "llm_target": item.get("route"), "specification": "../spec.md", "dependencies": item.get("dependencies"),
                        "verification": item.get("verification"), "scope": item.get("scope"),
                        "cloud_escalation_allowed": item.get("cloud_escalation_allowed")}
                criteria = item.get("acceptance")
                if not isinstance(criteria, list) or not criteria or any(not isinstance(c, str) or not c.strip() or "\n" in c for c in criteria):
                    raise Blocked("Each ticket requires nonempty one-line acceptance criteria")
                target = directory / f"{number}-task.md"
                temporary = directory / f"{number}-pending.tmp"
                temporary_paths.append(temporary)
                text = f"# {number}: {item.get('title', 'Implementation')}\n\n```toml\n{toml_text(data)}```\n\n## Scope\n\n{item.get('outcome', '')}\n\n## Acceptance criteria\n\n"
                text += "\n".join(f"- [ ] {criterion}" for criterion in criteria) + "\n"
                atomic_write(temporary, text)
                ticket = parse(temporary, self.root, self.config)
                if target.exists() and parse(target, self.root, self.config).requirements() != ticket.requirements():
                    raise Blocked(f"Published ticket {data['id']} differs from proposal; reconcile without overwriting history")
                staged.append((temporary, target, ticket))
            tickets = {ticket.id: ticket for _, _, ticket in staged}
            if len(tickets) != len(staged):
                raise Blocked("Duplicate proposed ticket identities")
            existing = discover(self.root, self.config)
            for _, target, ticket in staged:
                if ticket.id in existing and existing[ticket.id].path != target.resolve():
                    raise Blocked("Proposed identity exists at a different path; reconcile instead of duplicating")
            order({**existing, **tickets})
            for temporary, target, _ in staged:
                if not target.exists():
                    temporary.replace(target)
            self.value["published"] = [str(target.relative_to(self.root)) for _, target, _ in staged]
            self.value["stage"] = "complete"
            self.save()
            return {"published": self.value["published"], "status": "init complete; drafts require validation and human approval"}
        finally:
            for temporary in temporary_paths:
                if temporary.exists():
                    temporary.unlink()


def unfinished(root):
    return [p.parent.name for p in sorted(Path(root).glob("docs/pattyline/*/preparation.json"))
            if load_json(p).get("stage") != "complete"]
