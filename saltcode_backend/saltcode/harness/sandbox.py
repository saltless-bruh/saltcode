import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path


class SandboxError(Exception):
    """Base exception for sandbox operations."""


class DiffApplicationError(SandboxError):
    """Exception raised when a diff fails to apply to the sandbox."""


def _is_git_repo(path: Path) -> bool:
    """Checks if the path is inside a Git repository."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
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
            res = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
            if res.returncode == 0:
                use_worktree = True
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir = None
        except Exception:
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
            with contextlib.suppress(Exception):
                # Force remove worktree and clean up
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(sandbox_path)],
                    cwd=workspace,
                    capture_output=True,
                    text=True
                )
        
        # Clean up files remaining on disk
        shutil.rmtree(sandbox_path, ignore_errors=True)


def apply_diff(sandbox_path: Path | str, diff_content: str) -> bool:
    """Applies a unified diff inside the sandbox using git apply or patch.

    Returns True if successfully applied, False otherwise.
    """
    path = Path(sandbox_path)
    
    # 1. Try git apply (which works even if not a git repo under some conditions,
    # or inside a worktree sandbox)
    try:
        # First check if it applies cleanly
        check_res = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=diff_content,
            cwd=path,
            capture_output=True,
            text=True
        )
        if check_res.returncode == 0:
            apply_res = subprocess.run(
                ["git", "apply", "-"],
                input=diff_content,
                cwd=path,
                capture_output=True,
                text=True
            )
            if apply_res.returncode == 0:
                return True
    except Exception:
        pass

    # 2. Fallback to patch utility if git apply fails
    try:
        patch_res = subprocess.run(
            ["patch", "-p1"],
            input=diff_content,
            cwd=path,
            capture_output=True,
            text=True
        )
        if patch_res.returncode == 0:
            return True
    except Exception:
        pass

    return False


def run_command_in_sandbox(
    sandbox_path: Path | str,
    cmd: str | list[str],
    timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Runs a shell command inside the sandbox directory with a timeout constraint."""
    path = Path(sandbox_path)
    is_shell = isinstance(cmd, str)
    
    try:
        return subprocess.run(
            cmd,
            cwd=path,
            shell=is_shell,
            capture_output=True,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        # Return a CompletedProcess indicating failure due to timeout
        stdout = e.stdout.decode("utf-8") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-1,
            stdout=stdout + f"\n[TimeoutExpired after {timeout}s]",
            stderr=stderr + f"\n[TimeoutExpired after {timeout}s]"
        )
