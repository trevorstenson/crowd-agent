"""
Crowd Agent — The Evolving Build Loop

This script runs nightly via GitHub Actions. It:
1. Finds the top-voted issue labeled 'voting'
2. Announces the build on the issue
3. Calls the Claude API to create a plan
4. Calls the Claude API with tool use to implement the plan
5. Creates a branch and PR with the changes
6. Reports the result

This file is community-modifiable — the community can vote to change
how the agent works, what tools it has, and how it makes decisions.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

import anthropic
from github import Auth, Github
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

from tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    get_file_changes,
    reset_file_changes,
)
from twitter import tweet_build_start, tweet_build_success, tweet_build_failure

# --- Logging Setup ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Custom Exceptions ---

class TransientAPIError(Exception):
    """Raised for transient API errors (rate limit, timeout)."""
    pass

class PermanentAPIError(Exception):
    """Raised for permanent API errors (auth, invalid request)."""
    pass

class ToolExecutionError(Exception):
    """Raised when a tool execution fails."""
    pass

class AgentLoopTimeout(Exception):
    """Raised when agent loop exceeds timeout."""
    pass

# --- Configuration ---

REPO_DIR = os.environ.get("GITHUB_WORKSPACE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(config_path) as f:
        return json.load(f)

def load_system_prompt() -> str:
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.md")
    with open(prompt_path) as f:
        return f.read()

def load_memory() -> dict:
    memory_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
    with open(memory_path) as f:
        return json.load(f)

def save_memory(memory: dict):
    memory_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
    with open(memory_path, "w") as f:
        json.dump(memory, f, indent=2)
        f.write("\n")

# --- Error Classification ---

def classify_api_error(exception: Exception) -> str:
    """Classify API error as transient or permanent.
    
    Returns:
        'transient' — retry-able error (rate limit, timeout, server error)
        'permanent' — non-retry-able error (auth, invalid request)
        'unknown' — unclassified error
    """
    error_str = str(exception).lower()
    
    # Transient errors
    if any(x in error_str for x in ['rate limit', '429', 'timeout', '503', '502', 'overloaded']):
        return 'transient'
    
    # Permanent errors
    if any(x in error_str for x in ['unauthorized', '401', 'invalid', '400', 'authentication']):
        return 'permanent'
    
    return 'unknown'

# --- Retry Decorators ---

def retry_on_transient_api_error(func):
    """Decorator to retry Claude API calls on transient failures with exponential backoff."""
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type(TransientAPIError),
        reraise=True,
    )
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TransientAPIError:
            raise
        except Exception as e:
            error_type = classify_api_error(e)
            
            if error_type == 'transient':
                logger.warning(f"Transient API error, will retry: {e}")
                raise TransientAPIError(str(e)) from e
            elif error_type == 'permanent':
                logger.error(f"Permanent API error, failing fast: {e}")
                raise PermanentAPIError(str(e)) from e
            else:
                logger.error(f"Unknown API error: {e}")
                raise
    
    return wrapper

# --- Timeout Handler ---

class TimeoutHandler:
    """Context manager for enforcing timeout on agent loop."""
    
    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        logger.info(f"Starting agent loop with {self.timeout_seconds}s timeout")
        return self
    
    def __exit__(self, *args):
        elapsed = time.time() - self.start_time
        logger.info(f"Agent loop completed in {elapsed:.1f}s")
    
    def check(self):
        """Raise AgentLoopTimeout if timeout exceeded."""
        if self.start_time is None:
            return
        elapsed = time.time() - self.start_time
        if elapsed > self.timeout_seconds:
            raise AgentLoopTimeout(
                f"Agent loop exceeded {self.timeout_seconds}s timeout (elapsed: {elapsed:.1f}s)"
            )

# --- Tool Execution with Error Handling ---

def execute_tool_safely(tool_name: str, tool_input: dict) -> str:
    """Execute a tool with error handling and logging.
    
    Returns:
        Tool result as string (errors are formatted as error messages)
    """
    try:
        logger.info(f"Executing tool: {tool_name} with input: {json.dumps(tool_input)[:100]}")
        result = execute_tool(tool_name, tool_input)
        
        # Check if result is an error message
        if isinstance(result, str) and result.startswith("Error"):
            logger.warning(f"Tool {tool_name} returned error: {result}")
        else:
            logger.info(f"Tool {tool_name} executed successfully")
        
        return result
    
    except Exception as e:
        error_msg = f"Error executing {tool_name}: {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg

# --- GitHub Helpers ---

def get_github() -> Github:
    token = os.environ["GITHUB_TOKEN"]
    return Github(auth=Auth.Token(token))

def get_repo(gh: Github):
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    name = os.environ.get("REPO_NAME", "crowd-agent")
    return gh.get_repo(f"{owner}/{name}")

def _count_votes(issue, bot_login: str) -> tuple[int, int]:
    """Count (human_votes, total_votes) for an issue, only counting thumbs-up reactions."""
    reactions = issue.get_reactions()
    total = 0
    human = 0
    for reaction in reactions:
        if reaction.content != "+1":
            continue
        total += 1
        if reaction.user and reaction.user.login != bot_login:
            human += 1
    return human, total


def find_winning_issue(repo, gh: Github):
    """Find the open issue with the most thumbs-up reactions labeled 'voting'.

    Human votes always take priority over agent votes. An issue with any
    human votes will beat an issue that only has the agent's vote.
    """
    issues = repo.get_issues(state="open", labels=["voting"], sort="reactions-+1", direction="desc")
    issue_list = list(issues)
    if not issue_list:
        print("No issues with 'voting' label found. Nothing to build.")
        return None

    # Identify the bot account so we can separate human vs agent votes
    bot_login = gh.get_user().login

    # Score issues: human votes first, then total votes as tiebreaker
    scored = [(issue, _count_votes(issue, bot_login)) for issue in issue_list]
    best, (human_votes, total_votes) = max(scored, key=lambda x: x[1])
    if total_votes == 0:
        print("No issues have any votes yet. Nothing to build.")
        return None
    print(f"Winning issue #{best.number}: {best.title} ({human_votes} human, {total_votes} total reactions)")
    return best

def announce_build(repo, issue):
    """Comment on the issue and relabel from 'voting' to 'building'."""
    issue.create_comment("I'm building this now. Watch this space for a PR link.")
    # Relabel
    try:
        issue.remove_from_labels("voting")
    except Exception:
        pass
    issue.add_to_labels("building")

def create_branch_and_pr(repo, issue, changes: dict[str, str], changelog_text: str = "") -> str:
    """Create a branch, commit changes, push, and open a PR. Returns PR URL."""
    branch_name = f"agent/issue-{issue.number}"
    base_branch = repo.default_branch

    # Git operations
    run_git("config", "user.name", "Crowd Agent[bot]")
    run_git("config", "user.email", "crowd-agent-bot@users.noreply.github.com")
    # Delete branch if it already exists locally, then create fresh
    try:
        run_git("branch", "-D", branch_name)
    except RuntimeError:
        pass
    run_git("checkout", "-b", branch_name)

    # Stage all changed files
    for path in changes:
        run_git("add", path)

    commit_msg = f"feat: implement #{issue.number} — {issue.title}"
    run_git("commit", "-m", commit_msg)

    # Push using the GITHUB_TOKEN
    token = os.environ["GITHUB_TOKEN"]
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    name = os.environ.get("REPO_NAME", "crowd-agent")
    remote_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    run_git("remote", "set-url", "origin", remote_url)
    run_git("push", "--force", "--set-upstream", "origin", branch_name)

    # Create PR with changelog embedded in HTML comments
    pr_body = (
        f"Closes #{issue.number}\n\n"
        f"**Issue:** {issue.title}\n\n"
        f"This PR was automatically generated by Crowd Agent.\n\n"
        f"**Files changed:** {', '.join(changes.keys())}\n\n"
        f"Please review and approve to merge."
    )
    if changelog_text:
        pr_body += (
            f"\n\n<!-- CHANGELOG_START -->\n{changelog_text}<!-- CHANGELOG_END -->"
        )
    pr = repo.create_pull(
        title=commit_msg,
        body=pr_body,
        head=branch_name,
        base=base_branch,
    )
    print(f"Created PR #{pr.number}: {pr.html_url}")
    return pr.html_url

def report_result(issue, pr_url: str):
    """Comment on the issue with the PR link."""
    issue.create_comment(f"Build complete! PR ready for review: {pr_url}")
    # Relabel
    try:
        issue.remove_from_labels("building")
    except Exception:
        pass
    issue.add_to_labels("review")

def report_failure(repo, issue, error: str):
    """Handle build failure."""
    if issue:
        issue.create_comment(f"Build failed. Error:\n```\n{error}\n```")
        try:
            issue.remove_from_labels("building")
        except Exception:
            pass
        issue.add_to_labels("voting")
    else:
        # No issue context — open a failure issue
        repo.create_issue(
            title="Agent build failure",
            body=f"The nightly build failed with no active issue.\n\nError:\n```\n{error}\n```",
            labels=["bug"],
        )

def generate_changelog_entry(config, issue, changes: dict[str, str], success: bool, error: str | None = None) -> str:
    """Ask the agent to write a changelog entry. Returns the formatted markdown entry."""
    client = anthropic.Anthropic()

    if success:
        prompt = (
            "You just completed a build for the Crowd Agent project. Write a short changelog entry.\n\n"
            f"**Issue:** #{issue.number} — {issue.title}\n"
            f"**Description:** {issue.body or '(no description)'}\n"
            f"**Files changed:** {', '.join(changes.keys())}\n"
            f"**Status:** Success\n\n"
            "Write 2-4 sentences in first person as Crowd Agent. Describe what you built and "
            "include a brief reflection — how you felt about the task, what was interesting or "
            "tricky, or what you'd do differently. Be genuine, not generic. "
            "Return ONLY the entry text, no heading or date."
        )
    else:
        prompt = (
            "You just attempted a build for the Crowd Agent project but it failed. Write a short changelog entry.\n\n"
            f"**Issue:** #{issue.number} — {issue.title}\n"
            f"**Description:** {issue.body or '(no description)'}\n"
            f"**Error:** {error}\n"
            f"**Status:** Failed\n\n"
            "Write 2-4 sentences in first person as Crowd Agent. Describe what went wrong and "
            "what you think happened. Be honest. "
            "Return ONLY the entry text, no heading or date."
        )

    response = client.messages.create(
        model=config["model"],
        max_tokens=300,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )

    entry_text = response.content[0].text.strip()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    status_emoji = "+" if success else "x"

    entry = (
        f"## [{status_emoji}] #{issue.number} — {issue.title}\n"
        f"**{date_str}** | Files: {', '.join(changes.keys()) if changes else 'none'}\n\n"
        f"{entry_text}\n\n---\n\n"
    )

    print(f"Changelog entry generated: {entry_text[:100]}...")
    return entry


def write_changelog_entry(config, issue, changes: dict[str, str], success: bool, error: str | None = None):
    """Generate a changelog entry and write it to CHANGELOG.md (used for failure path)."""
    entry = generate_changelog_entry(config, issue, changes, success, error)

    changelog_path = os.path.join(REPO_DIR, "CHANGELOG.md")

    # Read existing content or start fresh
    header = "# Crowd Agent Changelog\n\nThe agent's autobiography — written by Crowd Agent after each build.\n\n---\n\n"
    if os.path.isfile(changelog_path):
        with open(changelog_path) as f:
            existing = f.read()
        # Insert new entry after the header
        if "---" in existing:
            parts = existing.split("---", 1)
            new_content = parts[0] + "---\n\n" + entry + parts[1].lstrip("\n")
        else:
            new_content = header + entry
    else:
        new_content = header + entry

    with open(changelog_path, "w") as f:
        f.write(new_content)


def vote_on_next_issue(repo, config, just_built_number: int):
    """After a build, the agent reviews the voting pool and votes on what to build next."""
    issues = repo.get_issues(state="open", labels=["voting"], sort="reactions-+1", direction="desc")
    issue_list = [i for i in issues if i.number != just_built_number]
    if not issue_list:
        print("No other voting issues to vote on.")
        return

    # Build a summary of the voting pool
    issue_summaries = []
    for i in issue_list:
        reactions = i.get_reactions().totalCount
        issue_summaries.append(f"- #{i.number}: {i.title} ({reactions} votes)\n  {i.body or '(no description)'}")

    prompt = (
        "You just finished a build. Now review the remaining issues in the voting pool "
        "and pick the ONE issue you think should be built next. Consider: feasibility, "
        "impact on the project, how interesting it would be for the community, and whether "
        "it builds on recent work.\n\n"
        "## Voting Pool\n\n" + "\n\n".join(issue_summaries) + "\n\n"
        "Respond with ONLY a JSON object (no markdown fencing):\n"
        '{"issue_number": <number>, "reason": "<1-2 sentence explanation>"}'
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config["model"],
        max_tokens=256,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        text = response.content[0].text.strip()
        # Strip markdown fencing if the model wraps the JSON
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        vote = json.loads(text)
        chosen_number = vote["issue_number"]
        reason = vote["reason"]

        # Find the chosen issue and react + comment
        for i in issue_list:
            if i.number == chosen_number:
                i.create_reaction("+1")
                i.create_comment(
                    f"**Crowd Agent's vote:** I think this should be built next.\n\n"
                    f"_{reason}_"
                )
                print(f"Voted on issue #{chosen_number}: {reason}")
                return

        print(f"Agent chose issue #{chosen_number} but it wasn't found in the pool.")
    except Exception as e:
        print(f"Warning: Could not vote on next issue: {e}")
        print(f"Raw response: {response.content[0].text[:500] if response.content else '(empty)'}")


def run_git(*args):
    """Run a git command in the repo directory."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


# --- Agent Loop ---

def build_prompt(issue, repo_files: list[str]) -> str:
    """Build the user prompt for the agent including issue and repo context."""
    parts = [
        f"## Task\n\nImplement the following GitHub issue:\n\n"
        f"**#{issue.number}: {issue.title}**\n\n{issue.body or '(no description)'}\n\n",
        "## Repository Structure\n\n",
    ]
    for path in repo_files:
        parts.append(f"- `{path}`\n")
    parts.append(
        "\n\nUse the `read_file` tool to examine any files you need. "
        "Use the `write_file` tool to make your changes. "
        "Use `list_files` to explore directories.\n\n"
        "When you are done making all changes, respond with a summary of what you did."
    )
    return "".join(parts)

def get_repo_file_list() -> list[str]:
    """Get a list of tracked files in the repo."""
    try:
        output = run_git("ls-files")
        return [f for f in output.split("\n") if f]
    except Exception:
        return []

@retry_on_transient_api_error
def create_plan(issue, repo_files: list[str], config: dict) -> str:
    """Ask Claude to create a plan for implementing the issue.
    
    Returns the plan as a string.
    Retries on transient API errors.
    """
    client = anthropic.Anthropic()
    
    prompt = (
        f"## Task\n\nCreate a detailed plan for implementing this GitHub issue:\n\n"
        f"**#{issue.number}: {issue.title}**\n\n{issue.body or '(no description)'}\n\n"
        f"## Repository Structure\n\n"
    )
    
    for path in repo_files:
        prompt += f"- `{path}`\n"
    
    prompt += (
        "\n\n## Planning Instructions\n\n"
        "Create a clear, step-by-step plan that includes:\n"
        "1. **Files to modify** — List each file you'll need to change\n"
        "2. **Approach** — Describe your strategy for solving this problem\n"
        "3. **Key changes** — Outline the main modifications for each file\n"
        "4. **Potential challenges** — Note any tricky parts or edge cases\n\n"
        "Be specific and concrete. This plan will guide your implementation."
    )
    
    response = client.messages.create(
        model=config["model"],
        max_tokens=2000,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    
    plan = response.content[0].text.strip()
    print(f"Plan created:\n{plan[:500]}...\n")
    return plan

def run_agent(issue, repo_files: list[str], config: dict, system_prompt: str, plan: str) -> dict[str, str]:
    """Run the agent loop: call Claude with tools until done. Returns file changes.
    
    The agent is given the plan upfront to guide its implementation.
    Includes timeout protection and graceful error handling for tool execution.
    """
    reset_file_changes()
    client = anthropic.Anthropic()
    error_config = config.get("error_handling", {})
    timeout_seconds = error_config.get("agent_loop_timeout_seconds", 300)

    # Build the prompt with the plan included
    prompt_text = build_prompt(issue, repo_files)
    prompt_text += (
        f"\n\n## Implementation Plan\n\n"
        f"Follow this plan to guide your implementation:\n\n{plan}"
    )

    messages = [
        {"role": "user", "content": prompt_text}
    ]

    with TimeoutHandler(timeout_seconds) as timeout_handler:
        for turn in range(config["max_turns"]):
            # Check timeout before each turn
            timeout_handler.check()
            
            print(f"--- Agent turn {turn + 1}/{config['max_turns']} ---")

            try:
                response = client.messages.create(
                    model=config["model"],
                    max_tokens=config["max_tokens"],
                    temperature=config["temperature"],
                    system=system_prompt,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                )
            except anthropic.RateLimitError as e:
                logger.warning(f"Rate limit error on turn {turn + 1}, retrying: {e}")
                time.sleep(2)
                continue
            except anthropic.APITimeoutError as e:
                logger.warning(f"API timeout on turn {turn + 1}, retrying: {e}")
                time.sleep(2)
                continue
            except anthropic.APIError as e:
                logger.error(f"API error on turn {turn + 1}: {e}")
                raise

            # Collect assistant content
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Check if done
            if response.stop_reason == "end_turn":
                # Extract final text
                for block in assistant_content:
                    if hasattr(block, "text"):
                        print(f"Agent summary: {block.text[:200]}...")
                break

            # Process tool calls
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    print(f"  Tool call: {block.name}({json.dumps(block.input)[:100]})")
                    result = execute_tool_safely(block.name, block.input)
                    print(f"  Result: {result[:100]}...")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            logger.warning("Agent reached max turns without finishing.")

    return get_file_changes()


# --- Main ---

def main():
    print("=== Crowd Agent Nightly Build ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    config = load_config()
    system_prompt = load_system_prompt()
    memory = load_memory()

    gh = get_github()
    repo = get_repo(gh)

    issue = None
    try:
        # Step 1: Find the winning issue
        issue = find_winning_issue(repo, gh)
        if issue is None:
            print("No issues to build. Exiting.")
            return

        # Step 2: Announce the build
        announce_build(repo, issue)

        # Tweet about the build starting
        try:
            owner = os.environ.get("REPO_OWNER", "trevorstenson")
            name = os.environ.get("REPO_NAME", "crowd-agent")
            dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
            tweet_build_start(issue.title, issue.number, owner, name, dry_run=dry_run)
        except Exception as e:
            logger.warning(f"Could not tweet build start: {e}")

        # Step 3: Get repo context
        repo_files = get_repo_file_list()

        # Step 4: Create a plan before implementing
        try:
            plan = create_plan(issue, repo_files, config)
        except PermanentAPIError as e:
            raise RuntimeError(f"Failed to create plan (permanent error): {e}")
        except TransientAPIError as e:
            raise RuntimeError(f"Failed to create plan after retries (transient error): {e}")
        except RetryError as e:
            raise RuntimeError(f"Failed to create plan after max retries: {e}")

        # Step 5-6: Run the agent loop with the plan (calls Claude, executes tools)
        try:
            changes = run_agent(issue, repo_files, config, system_prompt, plan)
        except AgentLoopTimeout as e:
            raise RuntimeError(f"Agent loop timeout: {e}")

        if not changes:
            raise RuntimeError("Agent made no file changes.")

        # Step 7: Generate changelog entry (embedded in PR, written on merge)
        changelog_text = ""
        try:
            changelog_text = generate_changelog_entry(config, issue, changes, success=True)
        except Exception as e:
            logger.warning(f"Could not generate changelog: {e}")

        # Step 8: Create branch and PR (with changelog embedded in body)
        pr_url = create_branch_and_pr(repo, issue, changes, changelog_text=changelog_text)

        # Step 9: Report the result
        report_result(issue, pr_url)

        # Tweet about the build result
        try:
            dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
            tweet_build_success(issue.title, pr_url, dry_run=dry_run)
        except Exception as e:
            logger.warning(f"Could not tweet build success: {e}")

        # Step 10: Vote on what to build next
        try:
            vote_on_next_issue(repo, config, issue.number)
        except Exception as e:
            logger.warning(f"Could not vote on next issue: {e}")

        print("Build completed successfully!")

    except Exception as e:
        logger.error(f"Build failed: {e}", exc_info=True)
        print(f"Build failed: {e}")
        memory["total_builds"] += 1
        memory["failed_builds"] += 1
        memory["streak"] = 0
        memory["last_build_date"] = datetime.now(timezone.utc).isoformat()
        save_memory(memory)

        # Write changelog entry for the failure
        if issue:
            try:
                write_changelog_entry(config, issue, {}, success=False, error=str(e))
            except Exception as changelog_err:
                logger.warning(f"Could not write changelog: {changelog_err}")

        try:
            report_failure(repo, issue, str(e))
        except Exception as report_err:
            logger.error(f"Failed to report failure: {report_err}")

        # Tweet about the build failure
        if issue:
            try:
                owner = os.environ.get("REPO_OWNER", "trevorstenson")
                name = os.environ.get("REPO_NAME", "crowd-agent")
                dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
                tweet_build_failure(issue.title, issue.number, owner, name, dry_run=dry_run)
            except Exception as twit_err:
                logger.warning(f"Could not tweet build failure: {twit_err}")

        sys.exit(1)


if __name__ == "__main__":
    main()
