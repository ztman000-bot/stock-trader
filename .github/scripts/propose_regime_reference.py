"""Propose generated aggregates without writing main or approving/merging PRs.

Git and the runner's GitHub CLI use their normally configured credentials. No PAT,
broker credentials, runtime modules or Android database are accessed by this script.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

REPOSITORY = "ztman000-bot/stock-trader"
BRANCH_PREFIX = "automation/marcap-regime-"
ARTIFACTS = (
    "research/regime/marcap_regime_daily.csv",
    "research/regime/marcap_regime_meta.json",
)


def run(*args: str) -> str:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def changed_paths(*args: str) -> set[str]:
    return {name for name in run("git", *args).split("\0") if name}


def propose() -> str | None:
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Only the configured Stock Trader repository may publish proposals")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Run the aggregate workflow from main only")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isascii() or not run_id.isdecimal() or not attempt.isascii() or not attempt.isdecimal():
        raise ValueError("Numeric GitHub run ID and attempt are required")
    branch = f"{BRANCH_PREFIX}{run_id}-{attempt}"

    # Do not rebase or reinterpret a build if main advanced during aggregation.
    run("git", "fetch", "--no-tags", "origin", "main")
    base_sha = run("git", "rev-parse", "HEAD")
    if base_sha != run("git", "rev-parse", "origin/main"):
        raise ValueError("main changed since checkout; rerun the workflow against latest main")
    if changed_paths("diff", "--cached", "--name-only", "-z"):
        raise ValueError("Unexpected staged changes; refusing to publish")
    changed = changed_paths("diff", "--name-only", "-z") | changed_paths(
        "ls-files", "--others", "--exclude-standard", "-z"
    )
    if changed - set(ARTIFACTS):
        raise ValueError("Changes outside the two aggregate files are not allowed")
    if not changed:
        print("No aggregate changes; no branch or PR created.")
        return None
    for filename in ARTIFACTS:
        path = Path(filename)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Both aggregate outputs must be regular files")

    # Keep a pending review stable: never update/force-push an existing bot branch.
    # Authentication/permission errors propagate; there is no direct-push fallback.
    pending = json.loads(run(
        "gh", "pr", "list", "--repo", REPOSITORY, "--state", "open", "--base", "main",
        "--limit", "1000", "--json", "number,headRefName,url",
    ))
    if not isinstance(pending, list) or len(pending) >= 1000:
        raise ValueError("Cannot establish a complete open-PR list; refusing to publish")
    for pr in pending:
        if str(pr["headRefName"]).startswith(BRANCH_PREFIX):
            print(f"Pending aggregate PR #{pr['number']}: {pr['url']}; review it before refreshing.")
            return None
    if run("git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"):
        raise ValueError("Proposal branch already exists; refusing to overwrite it")

    run("git", "switch", "-c", branch)
    run("git", "add", "--", *ARTIFACTS)
    run(
        "git", "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", "research: propose marcap regime aggregate refresh",
    )
    committed = changed_paths("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    if not committed or committed - set(ARTIFACTS):
        raise ValueError("Committed changes are not aggregate-only; refusing to push")
    # A unique run/attempt branch plus a non-forced push preserves existing work.
    run("git", "push", "origin", f"HEAD:refs/heads/{branch}")
    body = (
        "## Research-only aggregate refresh\n"
        "Only the market-level CSV and provenance metadata are proposed. No raw per-stock data.\n\n"
        f"Build base: `{base_sha}`\n"
        f"Build run: https://github.com/{REPOSITORY}/actions/runs/{run_id}\n\n"
        "## Required review before merge\n"
        "- Approve workflows to run if GitHub requests maintainer approval.\n"
        "- Wait for Safety Invariants to pass on this PR's current head.\n"
        "- Review the aggregate diff and upstream provenance, then merge through the PR.\n"
        "- Do not merge missing, pending, failed or stale Safety checks.\n\n"
        "The builder's checks do not replace PR Safety. No automatic approval or merge.\n"
        "Control v0.8.0 LOCKED; Paper semantics unchanged; REAL ORDER OFF; 068270 protected.\n"
    )
    url = run(
        "gh", "pr", "create", "--repo", REPOSITORY, "--base", "main", "--head", branch,
        "--title", "research: refresh marcap regime aggregate", "--body", body,
    )
    print(f"Proposed aggregate PR: {url}\nmain is unchanged; PR Safety and review are still required.")
    return url


if __name__ == "__main__":
    try:
        propose()
    except (ValueError, KeyError) as exc:
        raise SystemExit(f"Aggregate proposal stopped: {exc}. No fallback write to main.") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"Aggregate proposal stopped ({type(exc).__name__}). No fallback write to main. "
            "Inspect the step log and permissions; if a proposal branch was pushed, it remains unmerged."
        ) from exc
