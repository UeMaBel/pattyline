"""Enforced verification boundary. Unsupported hosts fail closed."""
import os
from pathlib import Path
import shutil
import tempfile
import json
import subprocess
import uuid

from .common import Blocked, within
from .process import run, sanitized_env


class Bubblewrap:
    """Linux checks get system tools and one writable worktree, no home/state/network.

    A real denial/process probe is still required before release. Runtime worker
    isolation is a separate capability, not implied by this verification adapter.
    """
    platform = "linux"

    def readiness(self):
        if os.name == "nt":
            return ["Windows setup/check sandbox not implemented: restricted-token filesystem, credential and descendant denial evidence required"]
        if not shutil.which("bwrap"):
            return ["Install bubblewrap and enable unprivileged user namespaces for isolated setup/checks"]
        return []

    def execute(self, command, worktree, primary, protected=(), **options):
        blockers = self.readiness()
        if blockers:
            raise Blocked("; ".join(blockers))
        worktree, primary = Path(worktree).resolve(), Path(primary).resolve()
        cwd = within(worktree, command.get("cwd", "."))
        with tempfile.TemporaryDirectory(prefix="pattyline-env-") as home:
            argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
            # Deliberately omit the user's home, primary checkout and arbitrary host mounts.
            for directory in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
                if Path(directory).exists():
                    argv += ["--ro-bind", directory, directory]
            argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
                     "--dir", "/home/worker", "--bind", str(worktree), str(worktree)]
            for path in protected:
                target = within(worktree, path)
                if target.exists():
                    argv += ["--ro-bind", str(target), str(target)]
            metadata = worktree / ".git"
            if metadata.exists():
                argv += ["--ro-bind", str(metadata), str(metadata)]
            argv += ["--chdir", str(cwd), "--setenv", "PATH", "/usr/bin:/bin",
                     "--setenv", "HOME", "/home/worker", "--setenv", "PATTYLINE_WORKER", "1",
                     "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null", "--setenv", "PYTHONNOUSERSITE", "1",
                     "--", *command["argv"]]
            return run(argv, worktree, command["timeout"], env=sanitized_env(home), **options)


def protected_paths(root, ticket, config):
    from .approval import instruction_paths
    root = Path(root).resolve()
    result = {"pattyline.toml", ".pattyline", *instruction_paths(root, ticket, config)}
    result.add(ticket.path.relative_to(root).as_posix())
    result.add(within(root, ticket.path.parent / ticket.data["specification"]).relative_to(root).as_posix())
    # Protect every requirement and approval, including tickets outside this selection.
    for pattern in config["tickets"]:
        result.update(path.relative_to(root).as_posix() for path in root.glob(pattern))
    return sorted(result)


class Container:
    """One short-lived, immutable-identity container per tool/check invocation.

    Model HTTP calls stay in the orchestrator; containers receive no provider or
    GitHub credentials. Only this worktree is mounted, with authoritative files
    remounted read-only. No Docker socket or primary checkout is exposed.
    """
    def __init__(self, config):
        self.config = config

    def docker(self, *argv, check=True):
        response = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=30)
        if check and response.returncode:
            raise Blocked(f"Container {argv[0]} failed; verify Docker service, pinned image and mount permissions")
        return response

    def readiness(self):
        if not shutil.which("docker"):
            return ["Install Docker with Linux-container support and start its service"]
        image = self.config["runtime"].get("container_image", "")
        if self.config["runtime"].get("execution_platform") != "linux":
            return ["This broker enforces Linux-container execution only; required native Windows commands need a separately verified Windows adapter and cannot be replaced silently"]
        if not image.startswith("sha256:") or len(image) != 71:
            return ["Configure runtime.container_image as an approved local sha256 image ID; no images are pulled automatically"]
        if self.config["permissions"].get("command_policy") != "container-unrestricted" or self.config["permissions"].get("network") is not False:
            return ["Only unrestricted commands inside the isolated network-disabled container are supported; other permission profiles require another enforced adapter"]
        try:
            response = self.docker("image", "inspect", image, "--format", "{{.Id}}")
            if response.stdout.strip() != image:
                return ["Configured immutable image is not present locally"]
            operating_system = self.docker("image", "inspect", image, "--format", "{{.Os}}")
            if operating_system.stdout.strip() != "linux":
                return ["Approved image OS differs from explicit Linux execution platform"]
        except (Blocked, OSError, subprocess.TimeoutExpired):
            return ["Docker service/image unavailable; approve/install a pinned tool image explicitly"]
        return []

    def execute(self, command, worktree, primary, protected=(), readonly=False, **options):
        blockers = self.readiness()
        if blockers:
            raise Blocked("; ".join(blockers))
        worktree, primary = Path(worktree).resolve(), Path(primary).resolve()
        if worktree == primary or worktree.is_relative_to(primary):
            raise Blocked("Container execution requires an isolated directory outside primary checkout")
        # --mount syntax uses comma as delimiter; never let a path alter mount options.
        if "," in str(worktree):
            raise Blocked("Container mount paths containing commas are unsupported")
        name = "pattyline-" + uuid.uuid4().hex
        record = {"mechanism": "docker", "name": name, "id": None, "phase": "creating"}
        on_start = options.pop("on_start", lambda p: None)
        on_stop = options.pop("on_stop", lambda p: None)
        arguments = ["create", "--name", name, "--pull", "never", "--network", "none", "--read-only",
                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                     "--memory", "1g", "--cpus", "1", "--user", "1000:1000", "--init",
                     "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=128m", "--env", "HOME=/tmp/home",
                     "--env", "PATTYLINE_WORKER=1", "--env", "GIT_CONFIG_GLOBAL=/dev/null",
                     "--env", "GIT_TERMINAL_PROMPT=0", "--env", "PYTHONNOUSERSITE=1",
                     "--mount", f"type=bind,source={worktree},target=/workspace" + (",readonly" if readonly else ""),
                     "--workdir", "/workspace/" + Path(command.get("cwd", ".")).as_posix()]
        within(worktree, command.get("cwd", "."))
        for relative in sorted({*protected, ".git", ".pattyline"}):
            target = within(worktree, relative)
            if target.exists():
                if "," in str(target):
                    raise Blocked("Unsupported comma in protected mount path")
                arguments += ["--mount", f"type=bind,source={target},target=/workspace/{Path(relative).as_posix()},readonly"]
        argv = command["argv"]
        arguments += ["--entrypoint", argv[0], self.config["runtime"]["container_image"], *argv[1:]]
        on_start(record)
        created = False
        try:
            response = self.docker(*arguments)
            record.update({"id": response.stdout.strip(), "phase": "created"})
            created = True
            on_start(record)
            result = run(["docker", "start", "--attach", name], worktree, command["timeout"],
                         env=sanitized_env(tempfile.gettempdir()), **options)
            status = json.loads(self.docker("inspect", name).stdout)[0]
            if not status["State"]["Running"]:
                result["exit_code"] = status["State"]["ExitCode"]
            result["argv"] = argv
            result["cwd"] = command.get("cwd", ".")
            result["container"] = {"id": record["id"], "image": self.config["runtime"]["container_image"]}
            return result
        finally:
            # docker kill targets immutable identity, not a reused host PID.
            found = self.docker("inspect", name, check=False)
            if found.returncode == 0:
                status = json.loads(found.stdout)[0]
                if record["id"] and status["Id"] != record["id"]:
                    raise Blocked("Container identity mismatch; preserve ownership")
                if status["State"]["Running"]:
                    self.docker("kill", status["Id"])
                stopped = json.loads(self.docker("inspect", status["Id"]).stdout)[0]
                if stopped["State"]["Running"]:
                    raise Blocked("Container descendants not established stopped; preserve ownership")
                on_stop({**record, "phase": "stopped"})
                self.docker("rm", status["Id"])
            elif not created:
                on_stop({**record, "phase": "never-created"})
            else:
                raise Blocked("Container identity missing after dispatch; descendant termination uncertain")
