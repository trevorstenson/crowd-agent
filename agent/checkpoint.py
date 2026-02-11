"""
Checkpoint management for workflow chaining.

Splits the agent loop across multiple GitHub Actions workflow runs,
one turn per run. Each run checkpoints its progress, commits it to
the agent branch, and triggers the next run. Context stays small and
flat (~2.5K tokens) instead of growing unboundedly.
"""

import json
import os
import subprocess
from datetime import datetime, timezone

CHECKPOINT_FILE = ".agent-checkpoint.json"

REPO_DIR = os.environ.get(
    "GITHUB_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)


def _run_git(*args):
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


def save_checkpoint(checkpoint: dict, changed_files: list[str]):
    """Write checkpoint to disk, git add changed files + checkpoint, commit, push."""
    checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint_path = os.path.join(REPO_DIR, CHECKPOINT_FILE)

    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
        f.write("\n")

    # Stage checkpoint file + any files changed this turn
    _run_git("add", CHECKPOINT_FILE)
    for fpath in changed_files:
        _run_git("add", fpath)

    turn = checkpoint.get("turn", 0)
    # Build a descriptive commit message
    if changed_files:
        file_list = ", ".join(changed_files)
        commit_msg = f"\U0001f9e0 turn {turn}: edited {file_list}"
    else:
        commit_msg = f"\U0001f9e0 turn {turn}: checkpoint"

    _run_git("commit", "-m", commit_msg)
    _run_git("push")


def load_checkpoint() -> dict | None:
    """Load checkpoint from repo root. Returns dict or None if not found."""
    checkpoint_path = os.path.join(REPO_DIR, CHECKPOINT_FILE)
    if not os.path.isfile(checkpoint_path):
        return None
    with open(checkpoint_path) as f:
        return json.load(f)


def build_continuation_prompt(checkpoint: dict, system_prompt: str) -> list[dict]:
    """Reconstruct a minimal prompt from checkpoint data.

    Returns a messages list ready for the OpenAI chat API, with the system
    prompt and tool descriptions baked in. The total context is ~2.5K tokens
    regardless of how many turns have passed.
    """
    # Import here to avoid circular dependency
    from main import _build_tool_prompt

    tool_prompt = _build_tool_prompt()

    # Build progress summary from action log
    progress_lines = []
    for entry in checkpoint.get("action_log", []):
        progress_lines.append(
            f"- Turn {entry['turn']}: {entry['action']} — {entry['result_summary']}"
        )
    progress_text = "\n".join(progress_lines) if progress_lines else "(no actions yet)"

    # Files modified so far
    files_modified = checkpoint.get("files_modified", [])
    files_text = ", ".join(files_modified) if files_modified else "(none yet)"

    turn = checkpoint.get("turn", 0)
    max_turns = checkpoint.get("max_turns", 10)

    user_content = (
        f"## Task\n"
        f"Implement GitHub issue #{checkpoint['issue_number']}: {checkpoint['issue_title']}\n"
        f"{checkpoint.get('issue_body', '(no description)')}\n\n"
        f"## Your Plan\n"
        f"{checkpoint.get('plan', '(no plan)')}\n\n"
        f"## Progress So Far\n"
        f"{progress_text}\n\n"
        f"## Files Modified So Far\n"
        f"{files_text}\n\n"
        f"## Instructions\n"
        f"Continue implementing the plan. You are on turn {turn + 1}/{max_turns}.\n"
        f"Use tools to make your next change. When ALL changes are complete, respond with\n"
        f'a plain text summary starting with "DONE:".\n'
        f"If you need to read a file you previously edited, use read_file — your edits are saved."
    )

    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
        {"role": "user", "content": user_content},
    ]
    return messages


def append_action_log(
    checkpoint: dict, tool_name: str, tool_args: dict, result: str
) -> None:
    """Add a log entry to the checkpoint's action_log.

    The result_summary is truncated to 500 chars but preserves enough
    for the next brain to make decisions.
    """
    args_summary = ", ".join(
        f"{k}={repr(v)[:60]}" for k, v in tool_args.items()
    )
    action_str = f"{tool_name}({args_summary})"

    result_summary = result[:500] if len(result) > 500 else result

    if "action_log" not in checkpoint:
        checkpoint["action_log"] = []

    checkpoint["action_log"].append(
        {
            "turn": checkpoint.get("turn", 0),
            "action": action_str,
            "result_summary": result_summary,
        }
    )


def trigger_next_workflow(checkpoint: dict):
    """Dispatch the next workflow run to continue the chain.

    Uses `gh workflow run` with the checkpoint branch and model as inputs.
    Targets the `local-agent` ref where the workflow file lives.
    """
    branch = checkpoint["branch"]
    model = checkpoint.get("model", "qwen3:8b")

    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    repo_name = os.environ.get("REPO_NAME", "crowd-agent")

    env = os.environ.copy()
    env["GH_TOKEN"] = token

    result = subprocess.run(
        [
            "gh", "workflow", "run", "nightly-build-local.yml",
            "-R", f"{owner}/{repo_name}",
            "--ref", "local-agent",
            "-f", f"checkpoint_branch={branch}",
            "-f", f"model={model}",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to trigger next workflow: {result.stderr}"
        )

    print(f"Triggered next workflow run for branch {branch}")


def should_finalize(checkpoint: dict) -> bool:
    """Return True when the chain should stop and create a PR.

    Conditions: status is "done", or turn >= max_turns, or
    chain_depth >= max_chain_depth.
    """
    if checkpoint.get("status") == "done":
        return True
    if checkpoint.get("turn", 0) >= checkpoint.get("max_turns", 10):
        return True
    if checkpoint.get("chain_depth", 0) >= checkpoint.get("max_chain_depth", 15):
        return True
    return False


def remove_checkpoint():
    """Remove the checkpoint file from disk and stage the removal."""
    checkpoint_path = os.path.join(REPO_DIR, CHECKPOINT_FILE)
    if os.path.isfile(checkpoint_path):
        os.remove(checkpoint_path)
        try:
            _run_git("rm", "--cached", CHECKPOINT_FILE)
        except RuntimeError:
            pass  # Already untracked
