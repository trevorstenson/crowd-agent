"""
State management for the dynamic round-based agent.

Each workflow run is one "round." State persists on the git branch as
.agent-round-state.json. Each round reads it, does work, writes updated
state, and triggers the next round.
"""

import json
import os
from datetime import datetime, timezone

STATE_FILE = ".agent-round-state.json"

REPO_DIR = os.environ.get(
    "GITHUB_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)


def load_state() -> dict | None:
    """Load round state from repo root. Returns dict or None if not found."""
    state_path = os.path.join(REPO_DIR, STATE_FILE)
    if not os.path.isfile(state_path):
        return None
    with open(state_path) as f:
        return json.load(f)


def save_state(state: dict):
    """Write round state to disk (does NOT commit — that's dispatch's job)."""
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path = os.path.join(REPO_DIR, STATE_FILE)
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def init_state(issue_number: int, issue_title: str, issue_body: str,
               branch: str, model: str, provider: str,
               repo_files: list[str], config: dict) -> dict:
    """Create a fresh state dict for a new issue."""
    dynamic = config.get("dynamic", {})
    return {
        "version": 2,
        "issue_number": issue_number,
        "issue_title": issue_title,
        "issue_body": issue_body,
        "branch": branch,
        "current_phase": "plan",
        "round_number": 1,
        "total_llm_calls": 0,
        "plan_steps": [],
        "current_step": 0,
        "round_logs": [],
        "files_modified": [],
        "pending_decision": None,
        "model": model,
        "provider": provider,
        "repo_files_snapshot": repo_files,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "max_rounds": dynamic.get("max_rounds", 10),
        "max_llm_calls": dynamic.get("max_llm_calls", 8),
        "consecutive_errors": 0,
    }


def check_safety_limits(state: dict) -> str | None:
    """Check if any safety limits are exceeded.

    Returns a reason string if limits hit, None otherwise.
    """
    if state["round_number"] > state.get("max_rounds", 10):
        return f"Max rounds exceeded ({state['round_number']} > {state.get('max_rounds', 10)})"

    if state["total_llm_calls"] >= state.get("max_llm_calls", 8):
        return f"Max LLM calls exceeded ({state['total_llm_calls']} >= {state.get('max_llm_calls', 8)})"

    max_errors = 2
    if state.get("consecutive_errors", 0) >= max_errors:
        return f"Too many consecutive errors ({state['consecutive_errors']} >= {max_errors})"

    return None


def _format_plan_steps(state: dict) -> str:
    """Format plan steps as a checklist with progress markers."""
    steps = state.get("plan_steps", [])
    if not steps:
        return "(no plan yet)"

    current = state.get("current_step", 0)
    lines = []
    for step in steps:
        sid = step["id"]
        status = step.get("status", "pending")
        desc = step["description"]
        result = step.get("result_summary", "")

        if status == "completed":
            marker = "DONE"
            detail = f" -> {result}" if result else ""
        elif status == "in_progress":
            marker = "IN PROGRESS"
            detail = ""
        else:
            marker = "PENDING"
            detail = ""

        lines.append(f"{sid}. [{marker}] {desc}{detail}")

    total = len(steps)
    done = sum(1 for s in steps if s.get("status") == "completed")
    header = f"## Your Plan (step {done + 1}/{total} next)"
    return header + "\n" + "\n".join(lines)


def _format_round_logs(state: dict) -> str:
    """Format round logs as a progress summary."""
    logs = state.get("round_logs", [])
    if not logs:
        return "(no actions yet)"

    lines = []
    for log in logs:
        rnd = log.get("round", "?")
        phase = log.get("phase", "?")
        summary = log.get("summary", "")
        files = log.get("files_modified", [])
        files_str = f" [{', '.join(files)}]" if files else ""
        lines.append(f"- Round {rnd} ({phase}): {summary}{files_str}")

    return "\n".join(lines)


def build_llm_context(state: dict, system_prompt: str) -> list[dict]:
    """Rebuild chat messages from state for the LLM.

    Includes: issue, plan with step statuses, compressed history,
    latest round detail, files modified, instructions.
    """
    from main import _build_tool_prompt

    tool_prompt = _build_tool_prompt()
    plan_text = _format_plan_steps(state)
    progress_text = _format_round_logs(state)

    files_modified = state.get("files_modified", [])
    files_text = ", ".join(files_modified) if files_modified else "(none yet)"

    phase = state.get("current_phase", "edit")
    round_num = state.get("round_number", 1)

    user_content = (
        f"## Task\n"
        f"Implement GitHub issue #{state['issue_number']}: {state['issue_title']}\n"
        f"{state.get('issue_body', '(no description)')}\n\n"
        f"{plan_text}\n\n"
        f"## Progress So Far\n"
        f"{progress_text}\n\n"
        f"## Files Modified So Far\n"
        f"{files_text}\n\n"
        f"## Repository Structure\n"
        + "\n".join(f"- `{f}`" for f in state.get("repo_files_snapshot", []))
        + "\n\n"
        f"## Instructions\n"
        f"You are in the **{phase}** phase (round {round_num}).\n"
        f"Make your next change using ONE tool call. Respond with ONLY a JSON object.\n"
        f"- To modify an existing file, prefer edit_file (find and replace) over write_file.\n"
        f"  edit_file only needs the old and new text, not the entire file.\n"
        f"- When ALL changes are complete, respond with a plain text summary starting with \"DONE:\".\n"
        f"- If you need to read a file you previously edited, use read_file — your edits are saved."
    )

    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
        {"role": "user", "content": user_content},
    ]
    return messages


def compress_round_logs(state: dict) -> None:
    """In-place: keep last 2 rounds full, older rounds summary-only."""
    logs = state.get("round_logs", [])
    if len(logs) <= 2:
        return

    # Keep last 2 full, compress older
    for log in logs[:-2]:
        log.pop("actions", None)  # Remove detailed action list
        # Keep summary, round, phase, files_modified


def append_round_log(state: dict, phase: str, summary: str,
                     actions: list | None = None,
                     files_modified: list | None = None) -> None:
    """Add a round log entry."""
    if "round_logs" not in state:
        state["round_logs"] = []

    entry = {
        "round": state.get("round_number", 1),
        "phase": phase,
        "summary": summary,
    }
    if actions:
        entry["actions"] = actions
    if files_modified:
        entry["files_modified"] = files_modified

    state["round_logs"].append(entry)
    compress_round_logs(state)
