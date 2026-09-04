from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "assert_main_branch.sh"


def run_guard(cwd: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in ("BUILD_ID", "BRANCH_NAME", "TAG_NAME", "GITHUB_ACTIONS", "GITHUB_REF"):
        env.pop(name, None)
    env.update(extra_env)
    return subprocess.run(
        [str(GUARD)],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def init_repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Deployment Guard Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_clean_main_at_origin_main_is_allowed(tmp_path: Path) -> None:
    result = run_guard(init_repo(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_non_main_branch_is_blocked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    subprocess.run(["git", "switch", "-c", "qa"], cwd=repo, check=True, capture_output=True)

    result = run_guard(repo)

    assert result.returncode == 64
    assert "only 'main' is deployable" in result.stderr


def test_dirty_worktree_is_blocked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "untracked.txt").write_text("not released\n", encoding="utf-8")

    result = run_guard(repo)

    assert result.returncode == 64
    assert "worktree must be clean" in result.stderr


def test_main_commit_not_published_to_origin_is_blocked(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    (repo / "tracked.txt").write_text("local-only\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "local-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    result = run_guard(repo)

    assert result.returncode == 64
    assert "exact commit published as origin/main" in result.stderr


def test_cloud_build_requires_main_branch(tmp_path: Path) -> None:
    result = run_guard(tmp_path, BUILD_ID="build-123", BRANCH_NAME="qa")

    assert result.returncode == 64
    assert "Cloud Build must run from branch 'main'" in result.stderr


def test_cloud_build_main_branch_is_allowed_without_git_checkout(tmp_path: Path) -> None:
    result = run_guard(tmp_path, BUILD_ID="build-123", BRANCH_NAME="main")

    assert result.returncode == 0, result.stderr


def test_tagged_cloud_build_is_blocked(tmp_path: Path) -> None:
    result = run_guard(tmp_path, BUILD_ID="build-123", BRANCH_NAME="main", TAG_NAME="v1")

    assert result.returncode == 64
    assert "tag builds are not deployable" in result.stderr
