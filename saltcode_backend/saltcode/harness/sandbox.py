"""Disposable sandboxes and the security container (task 3.1, REQ-SEC-001/005, REQ-STAT-001).

Two layers, and both are load-bearing:

* **The sandbox** — a disposable git worktree (temp copy when the target is not a
  repo). Builder diffs land here and nowhere else; the live tree is untouched
  until the Auditor returns `pass` (design §10).
* **The container** — every command runs inside OS-level containment. Pi ships
  no sandbox and documents the Gondolin pattern: real isolation comes from the
  OS boundary, so Saltcode brings its own (design §14). Backends are tried
  `bwrap` → Docker → firejail, and **if none is usable Phase 2 refuses to run**
  (REQ-SEC-005). There is no uncontained fallback, so this module exposes no way
  to run a command outside the container.

Three things here were settled by experiment rather than by reading, and each
would have shipped a container that looks right and isn't:

1. **Never `--ro-bind / /`.** Binding the whole root read-only leaves the unix
   sockets under `/run` reachable, and unix sockets cross network namespaces —
   `--unshare-net` notwithstanding, systemd-resolved answers DNS, and a Docker
   socket would hand over the host outright. Measured: whole-root resolved
   `example.com`; the explicit bind list below fails with `Network is
   unreachable` (REQ-SEC-001 AC2).
2. **`MemoryMax` without `MemorySwapMax=0` does not bind.** cgroup v2 pushes the
   overage to swap instead of killing, so a 64 MB cap happily allocated 300 MB.
   With swap pinned to zero the process is OOM-killed (REQ-SEC-001 AC3).
3. **A worktree's `.git` is a file pointing outside the sandbox**, so contained
   git needs its admin directory bound read-write and the parent repository's
   git directory read-only — otherwise `git apply` cannot resolve the worktree.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from saltcode.config import settings
from saltcode.harness.audit_log import append_entry
from saltcode.harness.command_allowlist import check_command

ContainmentBackend = Literal["bwrap", "docker", "firejail"]

BACKEND_PREFERENCE: tuple[ContainmentBackend, ...] = ("bwrap", "docker", "firejail")
"""REQ-SEC-005: bubblewrap first, Docker second, firejail last."""

CONTAINER_HOME = "/tmp/saltcode-home"
"""HOME points at the container's own tmpfs. `$HOME` is never bound in, so
`rm -rf ~/` destroys nothing that outlives the run (REQ-SEC-001)."""

CONTAINER_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": CONTAINER_HOME,
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "dumb",
    "PYTHONDONTWRITEBYTECODE": "1",
}
"""The environment is cleared and rebuilt: inheriting the host's carries tokens,
proxies and credentials into code we are running precisely because we do not
trust it."""


class SandboxError(Exception):
    """Base exception for sandbox operations."""


class DiffApplicationError(SandboxError):
    """Exception raised when a diff fails to apply to the sandbox."""


class NoContainmentBackendError(SandboxError):
    """No usable containment backend, so Phase 2 must not run (REQ-SEC-005 AC1).

    The message names what to install or configure. Refusing is the whole point:
    the alternative is executing model-written code straight onto the host.
    """


@dataclass(frozen=True)
class ContainerLimits:
    """Resource ceilings for one contained run (REQ-SEC-001 defaults)."""

    memory_mb: int = 2048
    cpus: float = 2.0
    timeout_seconds: float = 60.0

    @classmethod
    def from_settings(cls) -> ContainerLimits:
        return cls(
            memory_mb=settings.sandbox_memory_mb,
            cpus=settings.sandbox_cpus,
            timeout_seconds=settings.sandbox_timeout_seconds,
        )


@dataclass(frozen=True)
class ContainedResult:
    """Outcome of one contained command.

    ``refused`` separates "the allowlist said no" from "it ran and failed" —
    conflating them would let a blocked command read as a plain test failure.
    """

    exit_code: int
    stdout: str
    stderr: str
    backend: str
    container_id: str
    argv: tuple[str, ...] = ()
    timed_out: bool = False
    refused: bool = False
    reason: str = ""
    limits_enforced: bool = True

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.refused and not self.timed_out


# --------------------------------------------------------------------------- detection


_PROBE_CACHE: dict[str, bool] = {}


def _bwrap_usable() -> tuple[bool, str]:
    """Installed *and* able to create a namespace.

    Presence is not capability: Ubuntu 24.04 ships bubblewrap but restricts
    unprivileged user namespaces by default (an AppArmor profile plus
    `kernel.apparmor_restrict_unprivileged_userns`), so `bwrap` can exist and
    fail at the first exec. Detecting it anyway would report containment we do
    not have and defer the failure to the first gate. The probe runs once per
    process and is cached.
    """
    if shutil.which("bwrap") is None:
        return False, "bwrap: not installed"

    cached = _PROBE_CACHE.get("bwrap")
    if cached is None:
        # Probe with the *real* bind layout. A probe that binds less can succeed
        # where the actual container fails to even exec — which is how a broken
        # setup gets reported as contained.
        true_binary = shutil.which("true") or "/usr/bin/true"
        try:
            probe = subprocess.run(
                ["bwrap", "--unshare-all", *_root_bind_args(), "--", true_binary],
                capture_output=True,
                timeout=10,
                check=False,
            )
            cached = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cached = False
        _PROBE_CACHE["bwrap"] = cached

    if cached:
        return True, "bwrap: available"
    return False, "bwrap: installed but cannot create a namespace (unprivileged user namespaces may be restricted)"


def _docker_usable() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker: not installed"
    if not settings.sandbox_image:
        # No default is invented: the right image depends on the target project's
        # language and toolchain, and guessing one produces confusing failures.
        return False, "docker: installed, but [sandbox] image is not configured (SALTCODE_SANDBOX_IMAGE)"
    return True, "docker: available"


def _backend_availability() -> list[tuple[ContainmentBackend, bool, str]]:
    bwrap_ok, bwrap_detail = _bwrap_usable()
    docker_ok, docker_detail = _docker_usable()
    firejail_present = shutil.which("firejail") is not None
    return [
        ("bwrap", bwrap_ok, bwrap_detail),
        ("docker", docker_ok, docker_detail),
        ("firejail", firejail_present, "firejail: available" if firejail_present else "firejail: not installed"),
    ]


def detect_containment_backend(preferred: str | None = None) -> ContainmentBackend | None:
    """Return the backend to use, or ``None`` when none is usable.

    Args:
        preferred: Force a backend (REQ-SEC-005 AC2, `sandbox_backend` in config).
            An explicit choice is honoured or refused — never silently downgraded
            to a different backend than the operator asked for.
    """
    availability = _backend_availability()
    chosen = preferred if preferred is not None else settings.sandbox_backend

    if chosen:
        for name, usable, _ in availability:
            if name == chosen:
                return name if usable else None
        return None

    for name, usable, _ in availability:
        if usable:
            return name
    return None


def require_containment_backend(preferred: str | None = None) -> ContainmentBackend:
    """Return a usable backend or raise :class:`NoContainmentBackendError`."""
    backend = detect_containment_backend(preferred)
    if backend is not None:
        return backend

    details = "; ".join(detail for _, _, detail in _backend_availability())
    chosen = preferred if preferred is not None else settings.sandbox_backend
    scope = f"requested backend {chosen!r} is unusable" if chosen else "no containment backend is available"
    raise NoContainmentBackendError(
        f"Phase 2 refuses to run: {scope}. Saltcode never executes generated code uncontained. "
        f"Install bubblewrap (`bwrap`), Docker, or firejail. Status — {details}."
    )


# ------------------------------------------------------------------- container argv


def _root_bind_args() -> list[str]:
    """Read-only binds for the system image — an allowlist, never all of `/`.

    `/run`, `/home`, `/root`, `/var` and `/proc` are conspicuously absent: they
    hold the unix sockets, credentials and host state that containment exists to
    keep out of reach.
    """
    args: list[str] = []
    for directory in ("/usr", "/etc", "/opt"):
        if Path(directory).is_dir():
            args += ["--ro-bind", directory, directory]

    # On a merged-/usr system these are symlinks into /usr; elsewhere they are
    # real directories. Reproduce whichever this host has.
    for entry in ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32"):
        path = Path(entry)
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            args += ["--symlink", os.readlink(entry), entry]
        else:
            args += ["--ro-bind", entry, entry]
    return args


def git_bind_args(sandbox: Path) -> list[str]:
    """Binds a contained `git` needs when the sandbox is a worktree.

    A worktree's `.git` is a *file* holding `gitdir: <parent>/.git/worktrees/<name>`.
    Without that directory (read-write, for the index and HEAD) and the parent
    repository's git directory (read-only, for objects and refs), git inside the
    container cannot resolve the worktree at all.
    """
    git_marker = sandbox / ".git"
    if not git_marker.is_file():
        return []

    try:
        content = git_marker.read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not content.startswith("gitdir:"):
        return []

    admin_dir = Path(content.removeprefix("gitdir:").strip())
    if not admin_dir.is_absolute():
        admin_dir = (sandbox / admin_dir).resolve()
    if not admin_dir.is_dir():
        return []

    args = ["--bind", str(admin_dir), str(admin_dir)]

    # <parent>/.git/worktrees/<name> → <parent>/.git
    common_dir = admin_dir.parent.parent
    if common_dir.is_dir() and common_dir.name == ".git":
        args += ["--ro-bind", str(common_dir), str(common_dir)]
    return args


def _systemd_run_usable() -> bool:
    """Installed *and* able to open a scope — presence is not capability.

    This is `_bwrap_usable`'s lesson (G-C07) applied where it was still missing. On a host
    with no systemd bus — a container, most CI runners, this build container —
    `systemd-run` exists on `PATH` but fails at exec with *"Failed to connect to bus"*.
    Returning its prefix there does not merely fail to apply limits: it makes **every
    contained command die before its payload runs**, while detection still reports
    `contained`. Probing once and caching is the same shape as the bwrap probe.
    """
    if shutil.which("systemd-run") is None:
        return False

    cached = _PROBE_CACHE.get("systemd-run")
    if cached is None:
        true_binary = shutil.which("true") or "/usr/bin/true"
        try:
            probe = subprocess.run(
                ["systemd-run", "--user", "--scope", "--quiet", "--", true_binary],
                capture_output=True,
                timeout=10,
                check=False,
            )
            cached = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cached = False
        _PROBE_CACHE["systemd-run"] = cached

    return cached


def _limit_wrapper(container_id: str, limits: ContainerLimits) -> tuple[list[str], bool]:
    """Prefix that applies cgroup limits, plus whether they will actually bind.

    `systemd-run --user --scope` is the portable way to get cgroup v2 limits
    without privileges. `MemorySwapMax=0` is not optional: without it the cgroup
    swaps rather than kills and the memory ceiling is decorative.

    When the scope cannot be opened, this returns **no prefix and
    `limits_enforced=False`** rather than a prefix that cannot start (G-013). Containment
    still holds — the namespace, the bind layout and the network isolation are bwrap's,
    not systemd's — but the resource ceilings do not, and the caller is told so instead of
    being handed a command that dies at exec.
    """
    if not _systemd_run_usable():
        return [], False

    return (
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit=saltcode-{container_id}",
            f"--property=MemoryMax={limits.memory_mb}M",
            "--property=MemorySwapMax=0",
            f"--property=CPUQuota={int(limits.cpus * 100)}%",
            "--",
        ],
        True,
    )


def _bwrap_argv(
    argv: Sequence[str],
    sandbox: Path,
    env: Mapping[str, str],
    extra_ro_binds: Sequence[Path],
    extra_rw_binds: Sequence[Path],
) -> list[str]:
    args = [
        "bwrap",
        "--die-with-parent",  # the container cannot outlive us (REQ-SEC-001 cleanup)
        "--unshare-all",  # user, ipc, pid, net, uts, cgroup
        "--new-session",  # no TIOCSTI injection back into the operator's terminal
        "--clearenv",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", CONTAINER_HOME,
    ]
    args += _root_bind_args()

    for path in extra_ro_binds:
        args += ["--ro-bind-try", str(path), str(path)]

    # The sandbox is the only writable path that survives the run.
    args += ["--bind", str(sandbox), str(sandbox)]
    for path in extra_rw_binds:
        args += ["--bind-try", str(path), str(path)]

    for key, value in env.items():
        args += ["--setenv", key, value]

    args += ["--chdir", str(sandbox), "--"]
    return [*args, *argv]


def _docker_argv(
    argv: Sequence[str],
    sandbox: Path,
    env: Mapping[str, str],
    container_id: str,
    limits: ContainerLimits,
    extra_ro_binds: Sequence[Path],
    extra_rw_binds: Sequence[Path],
) -> list[str]:
    args = [
        "docker", "run", "--rm",
        f"--name=saltcode-{container_id}",
        "--network=none",
        f"--memory={limits.memory_mb}m",
        "--memory-swap=" + f"{limits.memory_mb}m",  # equal to --memory ⇒ no swap
        f"--cpus={limits.cpus}",
        "--pids-limit=512",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs=/tmp:rw,exec,nosuid",
        f"--volume={sandbox}:{sandbox}:rw",
        f"--workdir={sandbox}",
    ]
    for path in extra_ro_binds:
        args.append(f"--volume={path}:{path}:ro")
    for path in extra_rw_binds:
        args.append(f"--volume={path}:{path}:rw")
    for key, value in env.items():
        args += ["--env", f"{key}={value}"]

    image = settings.sandbox_image or ""
    return [*args, image, *argv]


def _firejail_argv(
    argv: Sequence[str],
    sandbox: Path,
    limits: ContainerLimits,
    extra_rw_binds: Sequence[Path],
) -> list[str]:
    """Last-resort backend. Constructed per firejail's documented flags but **not
    verified on this machine** (firejail is not installed here) — treat it as
    untested until someone runs the Task 3 suite on a host that has it."""
    args = [
        "firejail",
        "--quiet",
        "--noprofile",
        "--net=none",
        "--private-dev",
        "--private-tmp",
        "--caps.drop=all",
        "--nonewprivs",
        "--seccomp",
        "--read-only=/",
        f"--read-write={sandbox}",
        f"--rlimit-as={limits.memory_mb * 1024 * 1024}",
        f"--timeout=00:{int(limits.timeout_seconds) // 60:02d}:{int(limits.timeout_seconds) % 60:02d}",
    ]
    for path in extra_rw_binds:
        args.append(f"--read-write={path}")
    return [*args, "--", *argv]


# ------------------------------------------------------------------------ execution


def run_in_container(
    argv: Sequence[str] | str,
    *,
    sandbox: Path | str,
    workspace: Path | str | None = None,
    limits: ContainerLimits | None = None,
    allowlist: Sequence[Sequence[str]] | None = None,
    env: Mapping[str, str] | None = None,
    extra_ro_binds: Sequence[Path | str] = (),
    extra_rw_binds: Sequence[Path | str] = (),
    backend: str | None = None,
) -> ContainedResult:
    """Run one allowlisted command inside the security container.

    The only execution path this module offers. Order matters: the allowlist is
    checked *before* a container is built, so a refused command costs nothing and
    is logged as a refusal rather than as a failed run (REQ-SEC-002 AC1).

    Args:
        argv: The command, as argv or a single simple command string.
        sandbox: The disposable worktree — the only writable path.
        workspace: Target repo whose `.saltcode/audit_log.jsonl` receives the
            record. Defaults to ``sandbox``.
        limits: Memory/CPU/time ceilings; defaults from project config.
        allowlist: Overrides the default command allowlist (REQ-SEC-002 AC2).
        env: Replaces the container environment wholesale.
        extra_ro_binds: Extra read-only host paths (e.g. a toolchain).
        extra_rw_binds: Extra writable host paths (e.g. a worktree admin dir).
        backend: Force a containment backend.

    Returns:
        A :class:`ContainedResult`.

    Raises:
        NoContainmentBackendError: No usable backend — Phase 2 must not proceed.
    """
    sandbox_path = Path(sandbox).resolve()
    audit_root = Path(workspace).resolve() if workspace is not None else sandbox_path
    effective_limits = limits if limits is not None else ContainerLimits.from_settings()
    container_id = uuid.uuid4().hex[:12]

    decision = check_command(argv, allowlist)
    if not decision.allowed:
        append_entry(
            audit_root,
            command=argv,
            cwd=sandbox_path,
            exit_code=None,
            container_id=None,
            refused=True,
            reason=decision.reason,
        )
        return ContainedResult(
            exit_code=126,  # shell convention: found but not executable/permitted
            stdout="",
            stderr=decision.reason,
            backend="none",
            container_id="",
            refused=True,
            reason=decision.reason,
        )

    # Raised, not returned: a missing backend is not this command's failure, it
    # is a condition under which Phase 2 as a whole must stop (REQ-SEC-005).
    chosen = require_containment_backend(backend)

    container_env = dict(env) if env is not None else dict(CONTAINER_ENV)
    ro_binds = [Path(p).resolve() for p in extra_ro_binds]
    rw_binds = [Path(p).resolve() for p in extra_rw_binds]

    # A worktree sandbox needs its git admin directory reachable.
    git_binds = git_bind_args(sandbox_path)

    if chosen == "bwrap":
        inner = _bwrap_argv(decision.argv, sandbox_path, container_env, ro_binds, rw_binds)
        # git binds are raw bwrap flags; splice them before the terminating "--".
        if git_binds:
            terminator = inner.index("--chdir")
            inner = [*inner[:terminator], *git_binds, *inner[terminator:]]
        prefix, limits_enforced = _limit_wrapper(container_id, effective_limits)
        full_argv = [*prefix, *inner]
    elif chosen == "docker":
        docker_ro = [*ro_binds]
        docker_rw = [*rw_binds]
        for index, flag in enumerate(git_binds):
            if flag == "--bind":
                docker_rw.append(Path(git_binds[index + 1]))
            elif flag == "--ro-bind":
                docker_ro.append(Path(git_binds[index + 1]))
        full_argv = _docker_argv(
            decision.argv, sandbox_path, container_env, container_id, effective_limits, docker_ro, docker_rw
        )
        limits_enforced = True  # Docker applies its own cgroup limits.
    else:
        firejail_rw = [*rw_binds]
        for index, flag in enumerate(git_binds):
            if flag in ("--bind", "--ro-bind"):
                firejail_rw.append(Path(git_binds[index + 1]))
        full_argv = _firejail_argv(decision.argv, sandbox_path, effective_limits, firejail_rw)
        prefix, limits_enforced = _limit_wrapper(container_id, effective_limits)
        full_argv = [*prefix, *full_argv]

    timed_out = False
    try:
        completed = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=effective_limits.timeout_seconds,
            check=False,
        )
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        # `--die-with-parent` (and the systemd scope) mean killing our direct
        # child tears the whole tree down; verified, no survivors.
        timed_out = True
        exit_code = 124
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr) + f"\n[container killed after {effective_limits.timeout_seconds}s]"
    except OSError as exc:
        timed_out = False
        exit_code = 125
        stdout = ""
        stderr = f"could not start the container: {exc}"

    append_entry(
        audit_root,
        command=decision.argv,
        cwd=sandbox_path,
        exit_code=exit_code,
        container_id=container_id,
        stdout=stdout,
        stderr=stderr,
        extra={"backend": chosen, "timed_out": timed_out, "limits_enforced": limits_enforced},
    )

    return ContainedResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        backend=chosen,
        container_id=container_id,
        argv=tuple(decision.argv),
        timed_out=timed_out,
        reason="timed out" if timed_out else "",
        limits_enforced=limits_enforced,
    )


def _as_text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    return stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream


# -------------------------------------------------------------------------- sandbox


def _is_git_repo(path: Path) -> bool:
    """Checks if the path is inside a Git repository."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except OSError:
        return False


@contextlib.contextmanager
def disposable_sandbox(workspace_path: Path | str) -> Generator[Path, None, None]:
    """Creates a temporary sandbox environment using git worktree or a full directory copy.

    Ensures the live repository remains untouched. Discards the sandbox upon exit.
    """
    workspace = Path(workspace_path).resolve()
    temp_dir: str | None = None
    use_worktree = False

    if _is_git_repo(workspace):
        try:
            # Create temp directory for worktree
            temp_dir = tempfile.mkdtemp(prefix="saltcode-sandbox-worktree-")
            # Create detached HEAD worktree
            cmd = ["git", "worktree", "add", "--detach", temp_dir, "HEAD"]
            res = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                use_worktree = True
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir = None
        except OSError:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir = None

    if not use_worktree:
        # Fallback to copy
        temp_dir = tempfile.mkdtemp(prefix="saltcode-sandbox-copy-")

        def ignore_patterns(_src: str, names: list[str]) -> list[str]:
            # Do not copy massive history, caches, or pycache
            ignored: list[str] = []
            for name in names:
                if name in (".git", "cache", "calibration", "__pycache__", ".pytest_cache"):
                    ignored.append(name)
            return ignored

        try:
            # Copy workspace contents to temp directory
            shutil.copytree(workspace, temp_dir, dirs_exist_ok=True, ignore=ignore_patterns)
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise SandboxError(f"Failed to copy workspace directory to sandbox: {e}") from e

    if temp_dir is None:
        raise SandboxError("Failed to create temporary sandbox directory")

    sandbox_path = Path(temp_dir)
    try:
        yield sandbox_path
    finally:
        if use_worktree:
            with contextlib.suppress(OSError):
                # Force remove worktree and clean up
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(sandbox_path)],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                # Drop the now-dangling administrative entry as well.
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    check=False,
                )

        # Clean up files remaining on disk
        shutil.rmtree(sandbox_path, ignore_errors=True)


def apply_diff(
    sandbox_path: Path | str,
    diff_content: str,
    *,
    workspace: Path | str | None = None,
    backend: str | None = None,
) -> bool:
    """Apply a unified diff inside the sandbox, contained.

    `git apply` runs in the container like everything else — it is on the
    allowlist (REQ-SEC-002) and it writes to the sandbox, which is the one
    writable path. The old `patch -p1` fallback is gone: `patch` is not on the
    allowlist, so keeping it meant a command the allowlist forbids ran anyway.

    Returns:
        True when the diff applied. False on any failure, so the caller can
        discard the sandbox and short-circuit to the Builder (design §10).
    """
    path = Path(sandbox_path).resolve()
    patch_file = path / ".saltcode-apply.patch"

    try:
        patch_file.write_text(diff_content, encoding="utf-8")
    except OSError:
        return False

    try:
        check = run_in_container(
            ["git", "apply", "--check", patch_file.name],
            sandbox=path,
            workspace=workspace,
            backend=backend,
        )
        if not check.ok:
            return False

        applied = run_in_container(
            ["git", "apply", patch_file.name],
            sandbox=path,
            workspace=workspace,
            backend=backend,
        )
        return applied.ok
    finally:
        with contextlib.suppress(OSError):
            patch_file.unlink()
