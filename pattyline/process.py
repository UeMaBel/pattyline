"""Bounded subprocesses with retained ownership evidence and conservative recovery."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from .common import Blocked, now

SAFE_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TERM"}


def sanitized_env(home):
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    home = str(home)
    env.update({"HOME": home, "USERPROFILE": home, "APPDATA": home,
                "LOCALAPPDATA": home, "TMPDIR": home, "TMP": home, "TEMP": home,
                "PATTYLINE_WORKER": "1", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "PYTHONNOUSERSITE": "1"})
    return env


def process_identity(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            code = ctypes.get_last_error()
            if code == 87:
                return None
            raise Blocked(f"Cannot establish process identity {pid}: Windows error {code}")
        creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        try:
            if not kernel.GetProcessTimes(wintypes.HANDLE(handle), ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                raise Blocked(f"Cannot read creation identity for PID {pid}")
            return f"{creation.dwHighDateTime}:{creation.dwLowDateTime}"
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    try:
        content = Path(f"/proc/{pid}/stat").read_text()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return boot + ":" + content[content.rfind(")") + 2:].split()[19]
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Blocked(f"Cannot establish process identity {pid}: {error}") from error


def reconcile_process(record):
    """Parent death cannot establish descendant death; retain uncertain claims."""
    if not record:
        return "stopped"
    if record.get("mechanism") == "docker":
        response = subprocess.run(["docker", "inspect", record.get("id") or record["name"]], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if response.returncode:
            return "uncertain: container state unavailable; preserve ownership"
        container = __import__('json').loads(response.stdout)[0]
        if record.get("id") and container["Id"] != record["id"]:
            return "uncertain: container identity differs"
        return "alive" if container["State"]["Running"] else "stopped"
    current = process_identity(record["pid"])
    if current == record["identity"]:
        return "alive"
    return "uncertain: original parent absent or PID reused; descendant termination not proven"


def run(argv, cwd, timeout, env=None, cancelled=lambda: False, grace=10, on_start=lambda p: None,
        on_stop=lambda p: None, input_text=None):
    """Capture bounded output to a spool; POSIX process groups terminate as a unit.

    Windows plain subprocess execution is only for trusted probes/installers. Production
    stages require an externally verified tree-owning sandbox and are gated separately.
    """
    import tempfile
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        with tempfile.TemporaryFile() as stdin:
            stdin.write((input_text or "").encode())
            stdin.seek(0)
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin, stdout=output, stderr=errors,
                                     start_new_session=os.name != "nt",
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            record = {"pid": child.pid, "identity": process_identity(child.pid), "started": now(), "nonce": uuid.uuid4().hex,
                      "tree_mechanism": "process-group" if os.name != "nt" else "unverified-windows"}
            if not record["identity"]:
                child.wait()
                raise Blocked("Process exited before ownership identity could be established")
            try:
                on_start(record)
                expires = time.monotonic() + timeout
                stopped = None
                while child.poll() is None:
                    if cancelled() or time.monotonic() >= expires:
                        stopped = "cancelled" if cancelled() else "timeout"
                        terminate(child, grace)
                        break
                    time.sleep(0.05)
                child.wait()
                # Kill leftover descendants even after a successful parent exit.
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                record["stopped"] = now()
                on_stop(record)
            except BaseException:
                terminate(child, grace)
                raise
        output.seek(0)
        errors.seek(0)
        return {"argv": argv, "cwd": str(cwd), "exit_code": child.returncode, "outcome": stopped or "exited",
                "stdout": output.read(128 * 1024).decode(errors="replace"),
                "stderr": errors.read(128 * 1024).decode(errors="replace"), "process": record}


def terminate(child, grace):
    if os.name == "nt":
        # Trusted probe/install cancellation only; no assertion of durable descendant ownership.
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, timeout=max(1, grace))
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=max(1, grace))
    except subprocess.TimeoutExpired as error:
        raise Blocked("Process termination uncertain; retain ownership and worktree") from error
