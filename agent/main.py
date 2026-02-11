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
import re
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
from checkpoint import (
    CHECKPOINT_FILE,
    save_checkpoint,
    load_checkpoint,
    build_continuation_prompt,
    append_action_log,
    trigger_next_workflow,
    should_finalize,
    remove_checkpoint,
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

# --- LLM Provider Abstraction ---

def get_llm_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "anthropic").lower()

def get_model_name(config: dict) -> str:
    if get_llm_provider() == "ollama":
        return os.environ.get("OLLAMA_MODEL", "qwen3:8b")
    return config["model"]

def get_agent_loop_timeout(config: dict) -> int:
    env_timeout = os.environ.get("AGENT_LOOP_TIMEOUT")
    if env_timeout:
        return int(env_timeout)
    default = config.get("error_handling", {}).get("agent_loop_timeout_seconds", 300)
    if get_llm_provider() == "ollama":
        return max(default, 2400)  # 40 min minimum for CPU inference
    return default

def llm_complete(config: dict, prompt: str, max_tokens: int = 300, temperature: float = 0.7) -> str:
    """Simple LLM text completion. Works with both Anthropic and Ollama."""
    model = get_model_name(config)
    if get_llm_provider() == "ollama":
        import openai
        client = openai.OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",  # required by client but unused by Ollama
            timeout=1800.0,  # 30 min — CPU inference is slow
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    else:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

def _tools_to_openai_format(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }
        }
        for t in tools
    ]

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
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    return Github(auth=Auth.Token(token))

def get_repo(gh: Github):
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    name = os.environ.get("REPO_NAME", "crowd-agent")
    return gh.get_repo(f"{owner}/{name}")

def _count_votes(issue, bot_login: str) -> tuple[int, int]:
    """Count (human_net, total_net) for an issue using thumbs-up and thumbs-down reactions."""
    reactions = issue.get_reactions()
    total = 0
    human = 0
    for reaction in reactions:
        if reaction.content == "+1":
            total += 1
            if reaction.user and reaction.user.login != bot_login:
                human += 1
        elif reaction.content == "-1":
            total -= 1
            if reaction.user and reaction.user.login != bot_login:
                human -= 1
    return human, total


def find_winning_issue(repo, gh: Github):
    """Find the open issue with the highest net votes labeled 'voting'.

    Net votes = thumbs-up minus thumbs-down. Human votes always take priority
    over agent votes. An issue with positive human net votes will beat an issue
    that only has the agent's vote. Ties are broken by oldest issue first.
    Always builds something if there are voting issues.
    """
    issues = repo.get_issues(state="open", labels=["voting"], sort="reactions-+1", direction="desc")
    issue_list = list(issues)
    if not issue_list:
        print("No issues with 'voting' label found. Nothing to build.")
        return None

    # Identify the bot account so we can separate human vs agent votes
    try:
        bot_login = gh.get_user().login
    except Exception:
        # App installation tokens can't call GET /user; fall back to env or app bot name
        bot_login = os.environ.get("BOT_LOGIN", "")

    # Score issues: human net votes first, then total net votes, then oldest issue as tiebreaker
    scored = [(issue, _count_votes(issue, bot_login)) for issue in issue_list]
    best, (human_net, total_net) = max(scored, key=lambda x: (*x[1], -x[0].created_at.timestamp()))
    print(f"Winning issue #{best.number}: {best.title} ({human_net} human net, {total_net} total net votes)")
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

    # Push using the GH_PAT (personal access token) so it can trigger other workflows
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Neither GH_PAT nor GITHUB_TOKEN is set. Cannot push or create PR.")
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

    entry_text = llm_complete(config, prompt, max_tokens=300, temperature=0.7)
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

    try:
        text = llm_complete(config, prompt, max_tokens=256, temperature=0)
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
    """Ask the LLM to create a plan for implementing the issue.

    Returns the plan as a string.
    Retries on transient API errors.
    """
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

    plan = llm_complete(config, prompt, max_tokens=2000, temperature=0.3)
    print(f"Plan created:\n{plan[:500]}...\n")
    return plan

def run_agent(issue, repo_files: list[str], config: dict, system_prompt: str, plan: str) -> dict[str, str]:
    """Run the agent loop with tools until done. Returns file changes.

    Dispatches to the appropriate provider (Anthropic or Ollama).
    """
    if get_llm_provider() == "ollama":
        return _run_agent_ollama(issue, repo_files, config, system_prompt, plan)
    return _run_agent_anthropic(issue, repo_files, config, system_prompt, plan)

def _run_agent_anthropic(issue, repo_files: list[str], config: dict, system_prompt: str, plan: str) -> dict[str, str]:
    """Run the agent loop using the Anthropic API."""
    reset_file_changes()
    client = anthropic.Anthropic()
    timeout_seconds = get_agent_loop_timeout(config)

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
            timeout_handler.check()
            print(f"--- Agent turn {turn + 1}/{config['max_turns']} ---")

            try:
                response = client.messages.create(
                    model=get_model_name(config),
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

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn":
                for block in assistant_content:
                    if hasattr(block, "text"):
                        print(f"Agent summary: {block.text[:200]}...")
                break

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

def _parse_tool_call(content: str):
    """Parse a tool call from model text output. Returns (name, args) or None.

    Handles common issues with small models:
    - Literal newlines inside JSON string values (should be \\n)
    - JSON embedded in surrounding text
    - Markdown code fences around JSON
    """
    content = content.strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        content = content.rsplit("```", 1)[0].strip()

    # Try parsing the whole content as JSON directly
    try:
        obj = json.loads(content)
        if isinstance(obj, dict) and "tool" in obj:
            args = obj.get("args", obj)
            # Normalize common key mistakes: "file" → "path"
            if "file" in args and "path" not in args:
                args["path"] = args.pop("file")
            if "args" in obj:
                return obj["tool"], args
    except json.JSONDecodeError:
        pass

    # Try with escaped newlines — models often output literal newlines inside
    # JSON string values instead of proper \n escapes. Since we instruct the
    # model to output a single-line JSON object, any literal newlines are
    # inside string values and should be escaped.
    try:
        fixed = content.replace('\r\n', '\\n').replace('\n', '\\n')
        obj = json.loads(fixed)
        if isinstance(obj, dict) and "tool" in obj and "args" in obj:
            return obj["tool"], obj["args"]
    except json.JSONDecodeError:
        pass

    # Brace-depth extraction — find outermost {...} containing "tool"
    depth = 0
    start = -1
    for i, c in enumerate(content):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = content[start:i + 1]
                if '"tool"' in candidate:
                    try:
                        obj = json.loads(candidate)
                        if "tool" in obj and "args" in obj:
                            return obj["tool"], obj["args"]
                    except json.JSONDecodeError:
                        pass
                    # Try with escaped newlines on the extracted candidate
                    try:
                        fixed = candidate.replace('\r\n', '\\n').replace('\n', '\\n')
                        obj = json.loads(fixed)
                        if "tool" in obj and "args" in obj:
                            return obj["tool"], obj["args"]
                    except json.JSONDecodeError:
                        pass
                start = -1

    # Regex fallback — extract tool call from malformed JSON (e.g. unescaped
    # quotes inside write_file content that break json.loads even after
    # newline escaping)
    tool_match = re.search(r'"tool"\s*:\s*"(\w+)"', content)
    if tool_match:
        tool_name = tool_match.group(1)
        if tool_name == "write_file":
            path_match = re.search(r'"(?:path|file)"\s*:\s*"([^"]+)"', content)
            content_start = re.search(r'"content"\s*:\s*"', content)
            if path_match and content_start:
                cs = content_start.end()
                # Find the closing "}} at the end of the response
                end_match = re.search(r'"\s*\}\s*\}\s*$', content)
                if end_match:
                    file_content = content[cs:end_match.start()]
                    # Unescape any \n the model did properly escape
                    file_content = file_content.replace('\\n', '\n')
                    file_content = file_content.replace('\\"', '"')
                    return tool_name, {"path": path_match.group(1), "content": file_content}
        elif tool_name == "read_file":
            path_match = re.search(r'"(?:path|file)"\s*:\s*"([^"]+)"', content)
            if path_match:
                return tool_name, {"path": path_match.group(1)}
        elif tool_name == "list_files":
            dir_match = re.search(r'"directory"\s*:\s*"([^"]*)"', content)
            return tool_name, {"directory": dir_match.group(1) if dir_match else "."}
        elif tool_name == "search_files":
            pattern_match = re.search(r'"pattern"\s*:\s*"([^"]+)"', content)
            if pattern_match:
                return tool_name, {"pattern": pattern_match.group(1)}

    return None

def _build_tool_prompt() -> str:
    """Build a prompt section describing available tools and the JSON calling convention."""
    tool_lines = []
    for t in TOOL_DEFINITIONS:
        params = t["input_schema"].get("properties", {})
        required = t["input_schema"].get("required", [])
        param_desc = ", ".join(
            f'"{k}" ({v.get("type", "string")}, {"required" if k in required else "optional"}): {v.get("description", "")}'
            for k, v in params.items()
        )
        tool_lines.append(f'- **{t["name"]}**: {t["description"]}\n  Parameters: {param_desc}')

    return (
        "## Available Tools\n\n"
        + "\n\n".join(tool_lines)
        + '\n\n## How to Call Tools\n\n'
        'To call a tool, respond with ONLY a JSON object in this exact format:\n'
        '{"tool": "<tool_name>", "args": {<arguments>}}\n\n'
        'Examples:\n'
        '{"tool": "read_file", "args": {"path": "agent/prompt.md"}}\n'
        '{"tool": "write_file", "args": {"path": "README.md", "content": "# Hello"}}\n'
        '{"tool": "list_files", "args": {"directory": "."}}\n\n'
        'RULES:\n'
        '- Call ONE tool per response\n'
        '- Respond with ONLY the JSON object — no explanation, no markdown fences\n'
        '- First read_file to see current content, then write_file with the COMPLETE updated content\n'
        '- IMPORTANT: In write_file content, use \\n for newlines — do NOT use literal line breaks inside the JSON string\n'
        '- When you are done making ALL changes, respond with a plain text summary (no JSON)\n'
    )

def _run_agent_ollama(issue, repo_files: list[str], config: dict, system_prompt: str, plan: str) -> dict[str, str]:
    """Run the agent loop using Ollama with structured JSON tool calls parsed from text."""
    import openai

    reset_file_changes()
    client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        timeout=1800.0,  # 30 min — CPU inference is slow for large outputs
    )
    model = get_model_name(config)
    timeout_seconds = get_agent_loop_timeout(config)

    prompt_text = build_prompt(issue, repo_files)
    prompt_text += (
        f"\n\n## Implementation Plan\n\n"
        f"Follow this plan to guide your implementation:\n\n{plan}"
    )

    tool_prompt = _build_tool_prompt()

    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
        {"role": "user", "content": prompt_text},
    ]

    loop_start = time.time()
    with TimeoutHandler(timeout_seconds) as timeout_handler:
        for turn in range(config["max_turns"]):
            timeout_handler.check()
            turn_start = time.time()
            elapsed_total = turn_start - loop_start
            print(f"--- Agent turn {turn + 1}/{config['max_turns']} (elapsed: {elapsed_total:.1f}s) ---")

            try:
                llm_start = time.time()
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=config["max_tokens"],
                    temperature=config["temperature"],
                    messages=messages,
                )
                llm_elapsed = time.time() - llm_start
                print(f"  LLM response: {llm_elapsed:.1f}s")
            except Exception as e:
                logger.warning(f"API error on turn {turn + 1}: {e}")
                time.sleep(2)
                continue

            content = (response.choices[0].message.content or "").strip()
            messages.append({"role": "assistant", "content": content})

            # Try to parse a tool call from the text
            tool_call = _parse_tool_call(content)

            if tool_call:
                name, args = tool_call
                print(f"  Tool call: {name}({json.dumps(args)[:100]})")
                tool_start = time.time()
                result = execute_tool_safely(name, args)
                tool_elapsed = time.time() - tool_start
                print(f"  Tool result ({tool_elapsed:.1f}s): {result[:100]}...")
                messages.append({
                    "role": "user",
                    "content": f"Tool result for {name}:\n{result}",
                })
            else:
                # No tool call — model is done
                print(f"Agent summary: {content[:200]}...")
                break

            turn_elapsed = time.time() - turn_start
            print(f"  Turn total: {turn_elapsed:.1f}s")
        else:
            logger.warning("Agent reached max turns without finishing.")

    total_elapsed = time.time() - loop_start
    print(f"Agent loop finished in {total_elapsed:.1f}s ({turn + 1} turns)")

    return get_file_changes()


# --- Workflow Chaining (multi-turn per workflow run) ---

TURNS_PER_WORKFLOW = 2  # How many LLM turns to run per workflow invocation


def run_chained_turns(checkpoint: dict, config: dict, system_prompt: str) -> dict:
    """Execute up to TURNS_PER_WORKFLOW LLM turns for workflow chaining.

    Each turn rebuilds the prompt fresh from the checkpoint, so context
    stays flat regardless of how many turns have passed. Stops early if
    the agent says DONE or an error occurs.

    Returns the updated checkpoint dict.
    """
    import openai

    client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        timeout=1800.0,
    )
    model = checkpoint.get("model", get_model_name(config))

    for step in range(TURNS_PER_WORKFLOW):
        # Stop if we've hit turn/chain limits
        if should_finalize(checkpoint):
            break

        # Rebuild prompt fresh each turn from checkpoint
        messages = build_continuation_prompt(checkpoint, system_prompt)

        turn = checkpoint.get("turn", 0) + 1
        checkpoint["turn"] = turn
        print(f"--- Chained turn {turn}/{checkpoint.get('max_turns', 10)} "
              f"(step {step + 1}/{TURNS_PER_WORKFLOW}, chain_depth={checkpoint.get('chain_depth', 0)}) ---")

        llm_start = time.time()
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                messages=messages,
            )
        except Exception as e:
            logger.error(f"LLM call failed on turn {turn}: {e}")
            checkpoint["status"] = "error"
            checkpoint["error"] = str(e)
            return checkpoint

        llm_elapsed = time.time() - llm_start
        print(f"  LLM response: {llm_elapsed:.1f}s")

        content = (response.choices[0].message.content or "").strip()

        # Check if agent says it's done
        if content.upper().startswith("DONE:") or content.upper().startswith("DONE "):
            print(f"Agent finished: {content[:200]}...")
            checkpoint["status"] = "done"
            checkpoint["final_summary"] = content
            return checkpoint

        # Try to parse a tool call
        tool_call = _parse_tool_call(content)

        if tool_call:
            name, args = tool_call
            print(f"  Tool call: {name}({json.dumps(args)[:100]})")
            tool_start = time.time()
            result = execute_tool_safely(name, args)
            tool_elapsed = time.time() - tool_start
            print(f"  Tool result ({tool_elapsed:.1f}s): {result[:100]}...")

            # Update checkpoint
            append_action_log(checkpoint, name, args, result)

            # Track modified files across all turns
            turn_changes = get_file_changes()
            for fpath in turn_changes:
                if fpath not in checkpoint.get("files_modified", []):
                    checkpoint.setdefault("files_modified", []).append(fpath)
        else:
            # No tool call and no DONE: — wasted turn (model talked instead of acting).
            # Keep status as in_progress so the chain continues with a fresh prompt.
            print(f"  No tool call parsed (wasted turn): {content[:200]}...")
            append_action_log(
                checkpoint, "(no_tool_call)", {},
                f"Model responded with text instead of a tool call: {content[:300]}",
            )

    return checkpoint


def create_agent_branch(issue) -> str:
    """Create an agent branch for the issue from current HEAD and push it."""
    branch_name = f"agent/issue-{issue.number}"

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
    name = os.environ.get("REPO_NAME", "crowd-agent")
    remote_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    run_git("remote", "set-url", "origin", remote_url)
    run_git("push", "--force", "--set-upstream", "origin", branch_name)

    print(f"Created and pushed branch: {branch_name}")
    return branch_name


def commit_turn_changes(checkpoint: dict, turn_files: list[str]):
    """Commit this turn's file edits + checkpoint to the agent branch."""
    save_checkpoint(checkpoint, turn_files)


def create_pr_from_branch(repo, issue, checkpoint: dict, changelog_text: str = "") -> str:
    """Create a PR from the existing agent branch. Returns PR URL."""
    branch_name = checkpoint["branch"]
    base_branch = repo.default_branch

    files_modified = checkpoint.get("files_modified", [])
    files_str = ", ".join(files_modified) if files_modified else "(none)"

    commit_msg = f"feat: implement #{issue.number} — {issue.title}"
    pr_body = (
        f"Closes #{issue.number}\n\n"
        f"**Issue:** {issue.title}\n\n"
        f"This PR was automatically generated by Crowd Agent (workflow chaining, "
        f"{checkpoint.get('chain_depth', 0)} workflow runs).\n\n"
        f"**Files changed:** {files_str}\n\n"
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


# --- Main ---

def main():
    """Entry point — dispatches to fresh, fresh-chained, or continuation path."""
    checkpoint_branch = os.environ.get("CHECKPOINT_BRANCH", "")
    chaining_enabled = os.environ.get("WORKFLOW_CHAINING", "").lower() == "true"

    if checkpoint_branch:
        return main_continuation(checkpoint_branch)
    elif chaining_enabled and get_llm_provider() == "ollama":
        return main_fresh_chained()
    else:
        return main_fresh()


def main_fresh():
    """Original behavior — full agent loop in a single workflow run.

    Used for the Anthropic path and non-chained Ollama path.
    """
    print("=== Crowd Agent Nightly Build ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    config = load_config()
    print(f"LLM Provider: {get_llm_provider()}, Model: {get_model_name(config)}")
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


def main_fresh_chained():
    """First run of a chained workflow — find issue, plan, run turns, trigger next."""
    print("=== Crowd Agent Chained Build (Fresh) ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    config = load_config()
    model = get_model_name(config)
    print(f"LLM Provider: {get_llm_provider()}, Model: {model}")
    system_prompt = load_system_prompt()

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
            repo_name = os.environ.get("REPO_NAME", "crowd-agent")
            dry_run = os.environ.get("TWITTER_DRY_RUN", "").lower() == "true"
            tweet_build_start(issue.title, issue.number, owner, repo_name, dry_run=dry_run)
        except Exception as e:
            logger.warning(f"Could not tweet build start: {e}")

        # Step 3: Get repo context
        repo_files = get_repo_file_list()

        # Step 4: Create a plan (LLM call #1)
        try:
            plan = create_plan(issue, repo_files, config)
        except (PermanentAPIError, TransientAPIError, RetryError) as e:
            raise RuntimeError(f"Failed to create plan: {e}")

        # Step 5: Create agent branch
        branch_name = create_agent_branch(issue)

        # Step 6: Initialize checkpoint
        chaining_config = config.get("chaining", {})
        checkpoint = {
            "version": 1,
            "issue_number": issue.number,
            "issue_title": issue.title,
            "issue_body": issue.body or "(no description)",
            "plan": plan,
            "branch": branch_name,
            "turn": 0,
            "max_turns": config.get("max_turns", 10),
            "chain_depth": 1,
            "max_chain_depth": chaining_config.get("max_chain_depth", 15),
            "status": "in_progress",
            "files_modified": [],
            "action_log": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "repo_files_snapshot": repo_files,
        }

        # Step 7: Run turns (LLM calls)
        reset_file_changes()
        checkpoint = run_chained_turns(checkpoint, config, system_prompt)

        # Step 8: Commit file edits + checkpoint to branch
        turn_files = list(get_file_changes().keys())
        commit_turn_changes(checkpoint, turn_files)

        # Step 9: Check if we're already done, otherwise trigger next
        if should_finalize(checkpoint):
            _finalize_chain(repo, issue, checkpoint, config)
        else:
            trigger_next_workflow(checkpoint)
            print("Fresh chained run complete — next workflow triggered.")

    except Exception as e:
        logger.error(f"Chained build (fresh) failed: {e}", exc_info=True)
        print(f"Build failed: {e}")

        if issue:
            try:
                report_failure(repo, issue, str(e))
            except Exception as report_err:
                logger.error(f"Failed to report failure: {report_err}")

        sys.exit(1)


def main_continuation(checkpoint_branch: str):
    """Subsequent run of a chained workflow — load checkpoint, run turns, continue or finalize."""
    print(f"=== Crowd Agent Chained Build (Continuation: {checkpoint_branch}) ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    config = load_config()
    print(f"LLM Provider: {get_llm_provider()}, Model: {get_model_name(config)}")
    system_prompt = load_system_prompt()

    gh = get_github()
    repo = get_repo(gh)

    issue = None
    try:
        # Step 1: Load checkpoint
        checkpoint = load_checkpoint()
        if checkpoint is None:
            raise RuntimeError(
                f"No checkpoint found on branch {checkpoint_branch}. "
                f"Expected {CHECKPOINT_FILE} at repo root."
            )

        # Get issue for error reporting
        issue = repo.get_issue(checkpoint["issue_number"])

        # Step 2: Validate checkpoint
        if checkpoint.get("status") != "in_progress":
            raise RuntimeError(
                f"Checkpoint status is '{checkpoint.get('status')}', expected 'in_progress'"
            )
        if checkpoint.get("chain_depth", 0) >= checkpoint.get("max_chain_depth", 15):
            raise RuntimeError(
                f"Max chain depth reached ({checkpoint.get('chain_depth')}/"
                f"{checkpoint.get('max_chain_depth')})"
            )
        if checkpoint.get("turn", 0) >= checkpoint.get("max_turns", 10):
            raise RuntimeError(
                f"Max turns reached ({checkpoint.get('turn')}/{checkpoint.get('max_turns')})"
            )

        # Step 3: Increment chain depth
        checkpoint["chain_depth"] = checkpoint.get("chain_depth", 0) + 1

        # Step 4: Configure git for commits
        run_git("config", "user.name", "Crowd Agent[bot]")
        run_git("config", "user.email", "crowd-agent-bot@users.noreply.github.com")

        # Set up authenticated remote for push
        token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
        if token:
            owner = os.environ.get("REPO_OWNER", "trevorstenson")
            repo_name = os.environ.get("REPO_NAME", "crowd-agent")
            remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
            run_git("remote", "set-url", "origin", remote_url)

        # Step 5: Run turns
        reset_file_changes()
        checkpoint = run_chained_turns(checkpoint, config, system_prompt)

        # Step 6: Commit file edits + checkpoint
        turn_files = list(get_file_changes().keys())
        commit_turn_changes(checkpoint, turn_files)

        # Step 7: Finalize or continue
        if should_finalize(checkpoint):
            _finalize_chain(repo, issue, checkpoint, config)
        else:
            if checkpoint.get("status") == "error":
                # Error occurred — report and stop
                error_msg = checkpoint.get("error", "Unknown error during agent turn")
                report_failure(repo, issue, error_msg)
                sys.exit(1)

            trigger_next_workflow(checkpoint)
            print("Continuation run complete — next workflow triggered.")

    except Exception as e:
        logger.error(f"Chained build (continuation) failed: {e}", exc_info=True)
        print(f"Build failed: {e}")

        if issue:
            try:
                report_failure(repo, issue, str(e))
            except Exception as report_err:
                logger.error(f"Failed to report failure: {report_err}")

        sys.exit(1)


def _finalize_chain(repo, issue, checkpoint: dict, config: dict):
    """Complete the chain — remove checkpoint, create PR, report, tweet, vote."""
    print("=== Finalizing chained build ===")

    files_modified = checkpoint.get("files_modified", [])
    if not files_modified:
        raise RuntimeError("Agent completed chain but made no file changes.")

    # Remove checkpoint file from the branch
    remove_checkpoint()
    try:
        run_git("add", "-A")
        run_git("commit", "-m", "remove checkpoint file")
        run_git("push")
    except RuntimeError:
        pass  # No changes to commit (checkpoint already removed)

    # Build changes dict for changelog (read current file contents)
    changes = {}
    for fpath in files_modified:
        full_path = os.path.join(REPO_DIR, fpath)
        if os.path.isfile(full_path):
            with open(full_path) as f:
                changes[fpath] = f.read()

    # Generate changelog
    changelog_text = ""
    try:
        changelog_text = generate_changelog_entry(config, issue, changes, success=True)
    except Exception as e:
        logger.warning(f"Could not generate changelog: {e}")

    # Create PR from existing branch
    pr_url = create_pr_from_branch(repo, issue, checkpoint, changelog_text=changelog_text)

    # Report result
    report_result(issue, pr_url)

    # Tweet about success
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

    print("Chained build completed successfully!")


if __name__ == "__main__":
    main()
