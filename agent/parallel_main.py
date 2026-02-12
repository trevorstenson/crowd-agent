"""
Entry point dispatcher for the parallel map-reduce agent.

Called by the GitHub Actions workflow with:
    python agent/parallel_main.py --phase <plan|explore|edit|finalize>

Each phase function corresponds to a job in the workflow:
- plan:     Find issue, LLM plans exploration, set dynamic matrix output
- explore:  Deterministic file reads (no LLM), run as parallel matrix jobs
- edit:     LLM edits with full exploration context, creates branch + PR
- finalize: Report results, tweet, vote on next issue
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _set_output(name: str, value: str):
    """Write a key=value pair to $GITHUB_OUTPUT for job outputs."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            # Use heredoc syntax for multi-line values
            if "\n" in value:
                import uuid
                delimiter = f"ghadelimiter_{uuid.uuid4().hex[:8]}"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")
        logger.info(f"Set output: {name}={value[:100]}...")
    else:
        logger.info(f"(no GITHUB_OUTPUT) {name}={value[:100]}...")


# --- Phase: Plan ---

def phase_plan():
    """Find the winning issue, run the planner, set matrix outputs."""
    from main import (
        get_github, get_repo, find_winning_issue, announce_build,
        get_repo_file_list, load_config,
    )
    from twitter import tweet_build_start
    from planner import run_planner

    print("=== Parallel Agent: Plan Phase ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    config = load_config()

    gh = get_github()
    repo = get_repo(gh)

    # Find the winning issue
    issue = find_winning_issue(repo, gh)
    if issue is None:
        print("No issues to build. Exiting.")
        _set_output("has_issue", "false")
        _set_output("strategy", "none")
        _set_output("matrix", "[]")
        return

    _set_output("has_issue", "true")
    _set_output("issue_number", str(issue.number))

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

    # Run the planner (1 LLM call)
    plan = run_planner(config, issue.number, issue.title, issue.body or "", repo_files)

    # Save plan and issue context as artifacts
    with open("exploration-plan.json", "w") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")

    issue_context = {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body or "(no description)",
        "repo_files": repo_files,
    }
    with open("issue-context.json", "w") as f:
        json.dump(issue_context, f, indent=2)
        f.write("\n")

    # Set outputs for workflow
    strategy = plan.get("strategy", "explore_then_edit")
    _set_output("strategy", strategy)

    # Build matrix from task IDs
    task_ids = [t["id"] for t in plan.get("exploration_tasks", [])]
    _set_output("matrix", json.dumps(task_ids))

    print(f"Plan complete: strategy={strategy}, tasks={len(task_ids)}")
    if task_ids:
        print(f"Matrix: {task_ids}")


# --- Phase: Explore ---

def phase_explore():
    """Execute a single exploration task (called as a matrix job)."""
    from explorer import execute_exploration_task

    task_id = os.environ.get("TASK_ID", "")
    if not task_id:
        logger.error("TASK_ID not set")
        sys.exit(1)

    print(f"=== Parallel Agent: Explore Phase (task: {task_id}) ===")

    # Load the plan
    if not os.path.isfile("exploration-plan.json"):
        logger.error("exploration-plan.json not found")
        sys.exit(1)

    with open("exploration-plan.json") as f:
        plan = json.load(f)

    # Find this task
    task = None
    for t in plan.get("exploration_tasks", []):
        if t.get("id") == task_id:
            task = t
            break

    if task is None:
        logger.error(f"Task '{task_id}' not found in plan")
        sys.exit(1)

    # Execute
    results = execute_exploration_task(task)

    # Write results
    results_dir = "exploration-results"
    os.makedirs(results_dir, exist_ok=True)
    result_path = os.path.join(results_dir, f"{task_id}.json")

    with open(result_path, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")

    step_count = len(results["steps"])
    success_count = sum(1 for s in results["steps"] if s["success"])
    print(f"Explore complete: {success_count}/{step_count} steps succeeded")


# --- Phase: Edit ---

def phase_edit():
    """Load exploration results, run the editor, create branch + PR."""
    from main import (
        get_github, get_repo, load_config, load_system_prompt,
        get_repo_file_list, create_branch_and_pr, generate_changelog_entry,
    )
    from editor import load_exploration_results, run_editor

    print("=== Parallel Agent: Edit Phase ===")

    config = load_config()
    system_prompt = load_system_prompt()

    # Load plan and issue context
    with open("exploration-plan.json") as f:
        plan = json.load(f)
    with open("issue-context.json") as f:
        issue_ctx = json.load(f)

    issue_number = issue_ctx["number"]
    issue_title = issue_ctx["title"]
    issue_body = issue_ctx["body"]
    repo_files = issue_ctx.get("repo_files", get_repo_file_list())

    # Load exploration results (may be empty for direct_edit)
    results = load_exploration_results()
    print(f"Loaded {len(results)} exploration result files")

    # Run editor (LLM calls)
    changes = run_editor(
        config, system_prompt, plan,
        issue_number, issue_title, issue_body,
        results, repo_files,
    )

    if not changes:
        raise RuntimeError("Editor made no file changes.")

    # Get the actual issue object for PR creation
    gh = get_github()
    repo = get_repo(gh)
    issue = repo.get_issue(issue_number)

    # Generate changelog
    changelog_text = ""
    try:
        changelog_text = generate_changelog_entry(config, issue, changes, success=True)
    except Exception as e:
        logger.warning(f"Could not generate changelog: {e}")

    # Create branch and PR
    pr_url = create_branch_and_pr(repo, issue, changes, changelog_text=changelog_text)

    # Save edit result for finalize phase
    edit_result = {
        "success": True,
        "pr_url": pr_url,
        "files_changed": list(changes.keys()),
        "issue_number": issue_number,
        "issue_title": issue_title,
    }
    with open("edit-result.json", "w") as f:
        json.dump(edit_result, f, indent=2)
        f.write("\n")

    print(f"Edit complete: PR created at {pr_url}")


# --- Phase: Finalize ---

def phase_finalize():
    """Report results, tweet, vote on next issue."""
    from main import (
        get_github, get_repo, load_config,
        report_result, report_failure, vote_on_next_issue,
    )
    from twitter import tweet_build_success, tweet_build_failure

    print("=== Parallel Agent: Finalize Phase ===")

    config = load_config()
    gh = get_github()
    repo = get_repo(gh)

    # Load issue context
    with open("issue-context.json") as f:
        issue_ctx = json.load(f)

    issue_number = issue_ctx["number"]
    issue = repo.get_issue(issue_number)

    # Check if edit succeeded
    edit_result_path = "edit-result.json"
    if os.path.isfile(edit_result_path):
        with open(edit_result_path) as f:
            edit_result = json.load(f)

        if edit_result.get("success"):
            pr_url = edit_result["pr_url"]

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

            print("Finalize complete: success reported")
            return

    # Edit failed or no result — report failure
    error_msg = "Edit phase did not produce a result"
    report_failure(repo, issue, error_msg)

    try:
        owner = os.environ.get("REPO_OWNER", "trevorstenson")
        repo_name = os.environ.get("REPO_NAME", "crowd-agent")
        dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
        tweet_build_failure(issue.title, issue.number, owner, repo_name, dry_run=dry_run)
    except Exception as e:
        logger.warning(f"Could not tweet build failure: {e}")

    print("Finalize complete: failure reported")


# --- CLI Dispatcher ---

def main():
    parser = argparse.ArgumentParser(description="Parallel agent phase dispatcher")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["plan", "explore", "edit", "finalize"],
        help="Which phase to run",
    )
    args = parser.parse_args()

    # Change to repo root (agent scripts expect to run from there)
    repo_dir = os.environ.get(
        "GITHUB_WORKSPACE",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    os.chdir(repo_dir)

    # Add agent directory to path so imports work
    agent_dir = os.path.join(repo_dir, "agent")
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)

    if args.phase == "plan":
        phase_plan()
    elif args.phase == "explore":
        phase_explore()
    elif args.phase == "edit":
        phase_edit()
    elif args.phase == "finalize":
        phase_finalize()


if __name__ == "__main__":
    main()
