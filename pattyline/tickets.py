"""Native Markdown contract and conservative import of legacy website tickets."""
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from .common import Blocked, atomic_write, digest, now, within
from .config import toml_text

BLOCK = re.compile(r"```toml\s*\n(.*?)\n```", re.S)
OPERATIONAL = re.compile(r"^## (?:Comments|Execution evidence|Answer|Execution status)\s*$", re.M | re.I)


@dataclass
class Ticket:
    path: Path
    data: dict
    text: str

    @property
    def id(self):
        return self.data["id"]

    @property
    def dependencies(self):
        return self.data["dependencies"]

    def requirements(self):
        body = OPERATIONAL.split(self.text, maxsplit=1)[0]
        body = BLOCK.sub("", body)
        body = re.sub(r"^\*\*(?:Status|Approval):\*\*.*$", "", body, flags=re.M)
        body = re.sub(r"\[[ xX]\]", "[ ]", body)
        # Only operational fields are excluded; unknown metadata conservatively affects approval.
        fields = {k: v for k, v in self.data.items() if k not in {"approval", "status"}}
        return {"metadata": fields, "body": "\n".join(line.rstrip() for line in body.splitlines()).strip()}

    def write(self):
        replacement = "```toml\n" + toml_text(self.data).rstrip() + "\n```"
        self.text = BLOCK.sub(lambda _: replacement, self.text, count=1)
        operational = OPERATIONAL.search(self.text)
        boundary = operational.start() if operational else len(self.text)
        self.text = re.sub(r"(^\*\*Status:\*\*).*", lambda m: m[1] + " " + self.data["status"], self.text[:boundary], flags=re.M) + self.text[boundary:]
        atomic_write(self.path, self.text)


def parse(path, root, config):
    path = Path(path).resolve()
    text = path.read_text(encoding="utf-8-sig")
    blocks = list(BLOCK.finditer(text))
    if len(blocks) != 1:
        raise Blocked(f"{path}: expected exactly one native TOML metadata block")
    try:
        data = tomllib.loads(blocks[0][1])
    except tomllib.TOMLDecodeError as error:
        raise Blocked(f"{path}: invalid ticket TOML: {error}") from error
    def require(condition, field, message):
        if not condition:
            raise Blocked(f"{path}: {field}: {message}")
    require(data.get("schema_version") == 1, "schema_version", "expected 1")
    require(isinstance(data.get("id"), str) and re.fullmatch(r"[a-zA-Z0-9_-]+/[0-9]+", data["id"]), "id", "expected feature/number")
    require(data["id"].split("/")[0] == path.parent.parent.name, "id", "feature move requires identity reconciliation")
    require(data.get("type") == "task", "type", "only implementation tasks are executable")
    require(isinstance(data.get("status"), str) and data["status"] in {"draft", "needs-triage", "ready-for-agent"}, "status", "unknown triage status")
    require(isinstance(data.get("llm_target"), str) and data["llm_target"] in {"local", "cloud"}, "llm_target", "explicit route required")
    require(isinstance(data.get("dependencies"), list) and all(isinstance(d, str) for d in data["dependencies"]), "dependencies", "expected ID array")
    require(len(set(data["dependencies"])) == len(data["dependencies"]), "dependencies", "duplicates")
    require(isinstance(data.get("specification"), str), "specification", "source path required")
    spec = within(root, (path.parent / data["specification"]).resolve())
    require(spec.is_file(), "specification", f"missing {spec}")
    semantic_body = OPERATIONAL.split(text, maxsplit=1)[0]
    require(re.search(r"^## Acceptance criteria\s*\n\s*- \[[ xX]\] .+", semantic_body, re.M), "acceptance", "nonempty Acceptance criteria checkbox list required")
    verification = data.get("verification")
    require(isinstance(verification, list) and bool(verification), "verification", "nonempty check list required")
    names = []
    for check in verification:
        require(isinstance(check, dict), "verification", "each check needs name and phase (baseline or introduced)")
        require(isinstance(check.get("phase"), str) and check["phase"] in {"baseline", "introduced"}, "verification.phase", "explicit baseline or introduced required")
        require(isinstance(check.get("name"), str) and check["name"] in config["checks"], "verification.name", f"unknown check {check.get('name')}")
        names.append(check["name"])
    require(len(set(names)) == len(names), "verification", "duplicate checks")
    external = data.get("external_verification", [])
    require(isinstance(external, list), "external_verification", "expected array")
    external_names = []
    for item in external:
        require(isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"] and isinstance(item.get("kind"), str) and item["kind"] in {"human", "external"} and isinstance(item.get("criterion"), str) and bool(item["criterion"].strip()), "external_verification", "each requirement needs unique name, kind human/external and criterion")
        external_names.append(item["name"])
    require(len(set(external_names)) == len(external_names), "external_verification", "duplicate requirements")
    require(type(data.get("cloud_escalation_allowed")) is bool, "cloud_escalation_allowed", "explicit boolean required")
    scope = data.get("scope")
    require(isinstance(scope, list) and bool(scope) and all(isinstance(p, str) and p and not Path(p).is_absolute() and ".." not in Path(p).parts for p in scope), "scope", "nonempty project-relative file patterns required")
    for label, key in (("Type", "type"), ("llm_target", "llm_target"), ("Status", "status")):
        field = re.search(rf"^\*\*{label}:\*\*\s*(.+)$", semantic_body, re.M)
        require(not field or field[1].strip() == data[key], label, "readable field contradicts metadata")
    readable_spec = re.search(r"^\*\*Source specification:\*\*\s*(.+)$", semantic_body, re.M)
    if readable_spec:
        links = re.findall(r"\]\(([^)]+)\)", readable_spec[1])
        require(len(links) == 1 and within(root, path.parent / links[0]) == spec, "Source specification", "readable field contradicts metadata or is ambiguous")
    return Ticket(path, data, text)


def discover(root, config):
    root = Path(root).resolve()
    tickets = {}
    for pattern in config["tickets"]:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise Blocked(f"Ticket glob must be project-relative: {pattern}")
        for path in sorted(root.glob(pattern)):
            within(root, path)
            ticket = parse(path, root, config)
            if ticket.id in tickets:
                raise Blocked(f"Duplicate ticket identity {ticket.id}: {path}, {tickets[ticket.id].path}")
            tickets[ticket.id] = ticket
    paths = {ticket.path: ticket.id for ticket in tickets.values()}
    def resolve(ticket, reference):
        if reference in tickets:
            return reference
        if re.fullmatch(r"[a-zA-Z0-9_-]+/[0-9]+", reference):
            raise Blocked(f"{ticket.path}: dependencies: unknown identity {reference}")
        path = within(root, ticket.path.parent / reference)
        if path not in paths:
            raise Blocked(f"{ticket.path}: dependencies: unknown relative ticket {reference}")
        return paths[path]
    for ticket in tickets.values():
        normalized = [resolve(ticket, reference) for reference in ticket.dependencies]
        if len(set(normalized)) != len(normalized):
            raise Blocked(f"{ticket.path}: dependencies: duplicate normalized identities")
        ticket.data["dependencies"] = normalized
        body = OPERATIONAL.split(ticket.text, maxsplit=1)[0]
        readable = re.search(r"^\*\*Blocked by:\*\*\s*(.+)$", body, re.M)
        if readable:
            if readable[1].lower().startswith("none"):
                declared = []
            else:
                references = re.findall(r"\]\(([^)]+)\)", readable[1]) or re.findall(r"[a-zA-Z0-9_-]+/[0-9]+", readable[1])
                if not references:
                    raise Blocked(f"{ticket.path}: Blocked by: ambiguous readable dependencies")
                declared = [resolve(ticket, reference) for reference in references]
            if sorted(declared) != sorted(normalized):
                raise Blocked(f"{ticket.path}: Blocked by: readable field contradicts metadata")
    order(tickets)
    return tickets


def order(tickets, selected=None):
    visiting, visited = set(), set()
    def walk(identity):
        if identity not in tickets:
            raise Blocked(f"Unknown dependency/selection {identity}")
        if identity in visiting:
            raise Blocked(f"{tickets[identity].path}: dependencies: cycle through {identity}")
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in sorted(tickets[identity].dependencies):
            walk(dependency)
        visiting.remove(identity)
        visited.add(identity)
    def key(identity):
        feature, number = identity.rsplit("/", 1)
        return feature, int(number)
    for identity in sorted(tickets if selected is None else selected, key=key):
        walk(identity)
    result = []
    remaining = set(visited)
    while remaining:
        eligible = sorted((i for i in remaining if not set(tickets[i].dependencies) & remaining), key=key)
        identity = eligible[0]
        remaining.remove(identity)
        result.append(identity)
    return result


def legacy_preview(root, paths):
    """Read only: historical status does not establish execution or approval."""
    root = Path(root).resolve()
    result = []
    for relative in paths:
        path = within(root, relative)
        text = path.read_text(encoding="utf-8-sig")
        number = re.match(r"(\d+)-", path.name)
        if not number:
            raise Blocked(f"Cannot infer immutable legacy number: {path}")
        feature = path.parent.parent.name
        def field(name):
            match = re.search(rf"^\*\*{name}:\*\*\s*(.+)$", text, re.M)
            return match[1].strip() if match else ""
        blockers = field("Blocked by")
        dependencies = []
        for link in re.findall(r"\]\(([^)]+)\)", blockers):
            target = within(root, path.parent / link)
            if not target.exists():
                raise Blocked(f"{path}: missing blocker {link}")
            dep_number = re.match(r"(\d+)-", target.name)
            if not dep_number:
                raise Blocked(f"{path}: ambiguous blocker {link}")
            dependencies.append(f"{feature}/{dep_number[1]}")
        if blockers and not dependencies and not blockers.lower().startswith("none"):
            raise Blocked(f"{path}: ambiguous blockers; refine manually")
        result.append({"path": path.relative_to(root).as_posix(), "id": f"{feature}/{number[1]}",
                       "dependencies": dependencies, "historical_status": field("Status"),
                       "route": field("llm_target"), "source_hash": digest(text),
                       "needs": ["specification", "scope", "structured verification", "approval reconciliation"],
                       "execution_claim": "unknown; historical status is not execution evidence"})
    return result


def source_candidates(root):
    """Read-only discovery in conventional tracker locations; never imports a queue."""
    root = Path(root)
    patterns = [".scratch/*/spec.md", ".scratch/*/issues/*.md", "docs/pattyline/*/spec.md", "docs/pattyline/*/issues/*.md", "docs/spec.md", "spec.md"]
    result = set()
    for pattern in patterns:
        result.update(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())
    return sorted(result)


def adopt(root, previews, specification, verification, scope, reconcile=False):
    """Append native draft metadata, retaining all legacy text/history."""
    if not within(root, specification).is_file():
        raise Blocked("Source specification missing; reconstruct an explicitly labeled draft through prepare before adoption")
    results = []
    for preview in previews:
        path = within(root, preview["path"])
        text = path.read_text(encoding="utf-8-sig")
        if BLOCK.search(text):
            results.append({"id": preview["id"], "result": "already native; no history modified"})
            continue
        if digest(text) != preview["source_hash"]:
            raise Blocked(f"{path}: changed after adoption preview")
        if preview["historical_status"] in {"claimed", "resolved"} and not reconcile:
            raise Blocked(f"{path}: historical {preview['historical_status']} requires explicit reconciliation")
        data = {"schema_version": 1, "id": preview["id"], "type": "task", "status": "needs-triage",
                "llm_target": preview["route"], "specification": Path(__import__('os').path.relpath(within(root, specification), path.parent)).as_posix(),
                "dependencies": preview["dependencies"], "verification": verification, "scope": scope,
                "cloud_escalation_allowed": False}
        # Keep historical fields intact under an operational section; native fields become authoritative.
        body = f"# Adopted {preview['id']}\n\n```toml\n{toml_text(data)}```\n\n"
        acceptance = re.search(r"## Acceptance criteria\s*\n(.*?)(?=\n## |\Z)", text, re.S)
        if not acceptance:
            raise Blocked(f"{path}: acceptance criteria need refinement before adoption")
        body += "## Acceptance criteria\n" + acceptance[1] + "\n## Comments\n\nOriginal source preserved below; historical completion is unverified.\n\n" + text
        atomic_write(path, body)
        results.append({"id": preview["id"], "result": "adopted draft; human approval required"})
    return results
