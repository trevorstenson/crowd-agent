"""
Routing logic for the dynamic round-based agent.

Determines what each round should do:
- Fresh run (no state): find winning issue, create branch, init state with phase=plan
- Existing state: read current_phase + pending_decision, set job outputs
"""

import json
import logging
import os
from datetime import datetime, timezone

from round_state import load_state, save_state, init_state, STATE_FILE

logger = logging.getLogger(__name__)


def route_round(config: dict) -> dict:
    """Route the current round based on state.

    If state is None (fresh run):
        Find winning issue, announce, create branch, init state with phase=plan.
    If state exists:
        Read current_phase + pending_decision, determine job flags.

    Returns dict of GHA outputs:
        phase, has_llm, has_explore, explore_matrix, round_number, issue_number
    """
    from main import (
        get_github, get_repo, find_winning_issue, announce_build,
        get_repo_file_list, run_git, get_model_name, get_llm_provider,
    )
    from twitter import tweet_build_start

    # Check if we're continuing from a previous round
    state_branch = os.environ.get("ROUND_STATE_BRANCH", "")
    state = None

    if state_branch:
        # Fetch and checkout the agent branch to load state
        logger.info(f"Loading state from branch: {state_branch}")
        try:
            run_git("fetch", "origin", state_branch)
            run_git("checkout", state_branch)
        except RuntimeError as e:
            logger.error(f"Could not checkout state branch {state_branch}: {e}")
            raise

        state = load_state()
        if state is None:
            raise RuntimeError(f"No state file found on branch {state_branch}")

    if state is not None:
        # Continuing an existing round
        phase = state.get("current_phase", "plan")

        # Apply pending decision from previous round
        decision = state.get("pending_decision")
        if decision:
            phase = decision.get("next_phase", phase)
            state["current_phase"] = phase
            state["pending_decision"] = None
            state["round_number"] = state.get("round_number", 1) + 1
            save_state(state)

        logger.info(f"Continuing round {state['round_number']}, phase={phase}")

        return {
            "phase": phase,
            "has_llm": "true" if phase in ("plan", "edit") else "false",
            "has_explore": "false",  # Phase 1: no explore
            "explore_matrix": "[]",
            "round_number": str(state.get("round_number", 1)),
            "issue_number": str(state["issue_number"]),
            "state_branch": state["branch"],
        }

    # Fresh run — find issue and initialize
    logger.info("Fresh run — finding winning issue")

    gh = get_github()
    repo = get_repo(gh)

    issue = find_winning_issue(repo, gh)
    if issue is None:
        logger.info("No issues to build.")
        return {
            "phase": "none",
            "has_llm": "false",
            "has_explore": "false",
            "explore_matrix": "[]",
            "round_number": "0",
            "issue_number": "0",
            "state_branch": "",
        }

    # Announce build
    announce_build(repo, issue)

    # Tweet about build starting
    try:
        owner = os.environ.get("REPO_OWNER", "trevorstenson")
        repo_name = os.environ.get("REPO_NAME", "crowd-agent")
        dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
        tweet_build_start(issue.title, issue.number, owner, repo_name, dry_run=dry_run)
    except Exception as e:
        logger.warning(f"Could not tweet build start: {e}")

    # Get repo context
    repo_files = get_repo_file_list()

    # Create agent branch
    branch_name = f"agent/issue-{issue.number}"
    model = get_model_name(config)
    provider = get_llm_provider()

    run_git("config", "user.name", "Crowd Agent[bot]")
    run_git("config", "user.email", "crowd-agent-bot@users.noreply.github.com")

    # Delete local branch if it exists, then create fresh
    try:
        run_git("branch", "-D", branch_name)
    except RuntimeError:
        pass
    run_git("checkout", "-b", branch_name)

    # Set up authenticated remote
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Neither GH_PAT nor GITHUB_TOKEN is set.")
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    repo_name = os.environ.get("REPO_NAME", "crowd-agent")
    remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
    run_git("remote", "set-url", "origin", remote_url)
    run_git("push", "--force", "--set-upstream", "origin", branch_name)

    # Initialize state
    state = init_state(
        issue_number=issue.number,
        issue_title=issue.title,
        issue_body=issue.body or "(no description)",
        branch=branch_name,
        model=model,
        provider=provider,
        repo_files=repo_files,
        config=config,
    )
    save_state(state)

    # Commit initial state
    run_git("add", STATE_FILE)
    run_git("commit", "-m", f"🧠 round 1: init state for issue #{issue.number}")
    run_git("push")

    logger.info(f"Initialized round 1 for issue #{issue.number}: {issue.title}")

    return {
        "phase": "plan",
        "has_llm": "true",
        "has_explore": "false",
        "explore_matrix": "[]",
        "round_number": "1",
        "issue_number": str(issue.number),
        "state_branch": branch_name,
    }
