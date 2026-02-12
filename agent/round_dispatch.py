"""
Dispatch and finalization for the dynamic round-based agent.

After llm_work completes, dispatch either:
- Triggers the next round (workflow_dispatch)
- Finalizes: creates PR, reports result, tweets, votes
"""

import json
import logging
import os
import subprocess

from round_state import load_state, save_state, check_safety_limits, STATE_FILE

logger = logging.getLogger(__name__)

REPO_DIR = os.environ.get(
    "GITHUB_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)


def dispatch_or_finalize(state: dict, config: dict) -> None:
    """Main dispatch logic.

    1. Commit state + file changes to branch
    2. Check safety limits
    3. If phase == "done" or limits hit → finalize_success()
    4. If phase == "failed" → finalize_failure()
    5. Otherwise → trigger_next_round()
    """
    from main import run_git

    # Commit any pending changes to the branch
    _commit_state_and_files(state)

    # Check safety limits
    limit_reason = check_safety_limits(state)
    if limit_reason:
        logger.warning(f"Safety limit hit: {limit_reason}")
        if state.get("files_modified"):
            # We have changes, finalize what we have
            logger.info("Files were modified, finalizing with current changes")
            state["current_phase"] = "done"
            state["pending_decision"] = {
                "next_phase": "done",
                "reasoning": f"Safety limit: {limit_reason}",
            }
            save_state(state)
            _commit_state_and_files(state)
            finalize_success(state, config)
        else:
            finalize_failure(state, f"Safety limit hit with no changes: {limit_reason}")
        return

    # Determine next action from pending_decision
    decision = state.get("pending_decision")
    if decision is None:
        logger.error("No pending_decision in state — LLM work may not have completed")
        # Default to finalizing if we have changes, otherwise fail
        if state.get("files_modified"):
            finalize_success(state, config)
        else:
            finalize_failure(state, "No pending_decision in state — LLM work may have crashed")
        return
    next_phase = decision.get("next_phase", "done")

    if next_phase == "done":
        if state.get("files_modified"):
            finalize_success(state, config)
        else:
            finalize_failure(state, "Agent reported done but made no file changes")
    elif next_phase == "failed":
        reason = decision.get("reasoning", "Unknown failure")
        finalize_failure(state, reason)
    else:
        # Trigger next round
        trigger_next_round(state)


def _commit_state_and_files(state: dict) -> None:
    """Commit state file and any modified files to the branch."""
    from main import run_git

    run_git("config", "user.name", "Crowd Agent[bot]")
    run_git("config", "user.email", "crowd-agent-bot@users.noreply.github.com")

    # Set up authenticated remote
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    if token:
        owner = os.environ.get("REPO_OWNER", "trevorstenson")
        repo_name = os.environ.get("REPO_NAME", "crowd-agent")
        remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
        run_git("remote", "set-url", "origin", remote_url)

    # Stage state file
    run_git("add", STATE_FILE)

    # Stage any modified files
    for fpath in state.get("files_modified", []):
        try:
            run_git("add", fpath)
        except RuntimeError as e:
            logger.warning(f"Could not stage {fpath}: {e}")

    # Check if there's anything to commit
    try:
        run_git("diff", "--cached", "--quiet")
        logger.info("No changes to commit")
        return
    except RuntimeError:
        pass  # There are staged changes

    round_num = state.get("round_number", 1)
    phase = state.get("current_phase", "unknown")
    files = state.get("files_modified", [])

    if files:
        file_list = ", ".join(files[-3:])  # Show last 3 files
        commit_msg = f"🧠 round {round_num} ({phase}): edited {file_list}"
    else:
        commit_msg = f"🧠 round {round_num} ({phase}): state update"

    run_git("commit", "-m", commit_msg)
    run_git("push")
    logger.info(f"Committed and pushed: {commit_msg}")


def trigger_next_round(state: dict) -> None:
    """Trigger the next workflow run via gh workflow run.

    Retry once on failure. If still fails, attempt inline finalize.
    """
    branch = state["branch"]
    model = state.get("model", "qwen3:8b")

    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    repo_name = os.environ.get("REPO_NAME", "crowd-agent")

    # The workflow must exist on a discoverable ref (usually main or the current branch)
    workflow_ref = os.environ.get("WORKFLOW_REF", "local-agent-dynamic")

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    cmd = [
        "gh", "workflow", "run", "nightly-build-dynamic.yml",
        "-R", f"{owner}/{repo_name}",
        "--ref", workflow_ref,
        "-f", f"round_state_branch={branch}",
        "-f", f"model={model}",
    ]

    for attempt in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode == 0:
            logger.info(f"Triggered next round for branch {branch}")
            return
        logger.warning(f"Trigger attempt {attempt + 1} failed: {result.stderr}")
        if attempt == 0:
            import time
            time.sleep(5)

    # Both attempts failed — try to finalize with what we have
    logger.error("Could not trigger next round, attempting inline finalize")
    if state.get("files_modified"):
        finalize_success(state, _load_config())
    else:
        finalize_failure(state, f"Failed to trigger next round: {result.stderr}")


def finalize_success(state: dict, config: dict) -> None:
    """Create PR, report result, generate changelog, vote, tweet."""
    from main import (
        get_github, get_repo, report_result, report_failure,
        generate_changelog_entry, vote_on_next_issue, run_git,
    )
    from twitter import tweet_build_success

    logger.info("Finalizing with success...")

    gh = get_github()
    repo = get_repo(gh)
    issue = repo.get_issue(state["issue_number"])

    # Remove state file before creating PR
    state_path = os.path.join(REPO_DIR, STATE_FILE)
    if os.path.isfile(state_path):
        os.remove(state_path)
        try:
            run_git("rm", "--cached", STATE_FILE)
        except RuntimeError:
            pass

    # Commit removal of state file
    try:
        run_git("commit", "-m", "🧹 remove agent state file before PR")
        run_git("push")
    except RuntimeError:
        pass  # No changes to commit

    # Create PR from the existing branch
    branch_name = state["branch"]
    base_branch = repo.default_branch
    files_modified = state.get("files_modified", [])
    files_str = ", ".join(files_modified) if files_modified else "(none)"
    round_count = state.get("round_number", 1)

    # Build changes dict for changelog (we don't have full content, just paths)
    changes = {f: "" for f in files_modified}

    # Generate changelog
    changelog_text = ""
    try:
        changelog_text = generate_changelog_entry(config, issue, changes, success=True)
    except Exception as e:
        logger.warning(f"Could not generate changelog: {e}")

    commit_msg = f"feat: implement #{issue.number} — {issue.title}"
    pr_body = (
        f"Closes #{issue.number}\n\n"
        f"**Issue:** {issue.title}\n\n"
        f"This PR was automatically generated by Crowd Agent "
        f"(dynamic round-based, {round_count} rounds).\n\n"
        f"**Files changed:** {files_str}\n\n"
        f"Please review and approve to merge."
    )
    if changelog_text:
        pr_body += f"\n\n<!-- CHANGELOG_START -->\n{changelog_text}<!-- CHANGELOG_END -->"

    pr = repo.create_pull(
        title=commit_msg,
        body=pr_body,
        head=branch_name,
        base=base_branch,
    )
    pr_url = pr.html_url
    logger.info(f"Created PR #{pr.number}: {pr_url}")

    # Report success on issue
    report_result(issue, pr_url)

    # Tweet success
    try:
        dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
        tweet_build_success(issue.title, pr_url, dry_run=dry_run)
    except Exception as e:
        logger.warning(f"Could not tweet build success: {e}")

    # Vote on next issue
    try:
        vote_on_next_issue(repo, config, issue.number)
    except Exception as e:
        logger.warning(f"Could not vote on next issue: {e}")

    logger.info("Finalization complete (success)")


def finalize_failure(state: dict, error: str) -> None:
    """Report failure on the issue, tweet about it."""
    from main import get_github, get_repo, report_failure
    from twitter import tweet_build_failure

    logger.info(f"Finalizing with failure: {error}")

    try:
        gh = get_github()
        repo = get_repo(gh)
        issue = repo.get_issue(state["issue_number"])
        report_failure(repo, issue, error)
    except Exception as e:
        logger.error(f"Could not report failure: {e}")

    try:
        owner = os.environ.get("REPO_OWNER", "trevorstenson")
        repo_name = os.environ.get("REPO_NAME", "crowd-agent")
        dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
        tweet_build_failure(
            state.get("issue_title", "Unknown"),
            state.get("issue_number", 0),
            owner, repo_name, dry_run=dry_run,
        )
    except Exception as e:
        logger.warning(f"Could not tweet build failure: {e}")

    logger.info("Finalization complete (failure)")


def _load_config() -> dict:
    """Load config for inline finalize fallback."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(config_path) as f:
        return json.load(f)
