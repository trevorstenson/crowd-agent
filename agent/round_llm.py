"""
LLM work for the dynamic round-based agent.

Handles both plan and edit phases:
- plan: 1 LLM call to analyze issue and produce plan steps
- edit: Multi-turn tool-call loop (max 4 turns) to implement the plan
"""

import json
import logging
import os
import time

from round_state import (
    load_state, save_state, build_llm_context,
    append_round_log, STATE_FILE,
)

logger = logging.getLogger(__name__)

# JSON schema the planner LLM must produce
PLANNER_SCHEMA = """\
You must respond with ONLY a valid JSON object (no markdown fences, no explanation) matching this schema:

{
  "plan_steps": [
    {
      "id": 1,
      "type": "read" | "edit" | "create" | "verify",
      "description": "<what to do in this step>"
    }
  ],
  "next_action": {
    "phase": "edit",
    "reasoning": "<why edit is the right next phase>"
  }
}

Rules:
- plan_steps: ordered list of steps to implement the issue (3-8 steps)
- Each step has a numeric id, a type, and a description
- type is one of: "read" (examine files), "edit" (modify existing), "create" (new file), "verify" (check work)
- next_action.phase should be "edit" (Phase 1 always goes plan → edit)
- Keep steps concrete and actionable
"""


def run_llm_work(state: dict, config: dict, system_prompt: str) -> dict:
    """Dispatch to run_plan() or run_edit() based on current_phase.

    After LLM work completes, commits+pushes state and file changes to
    the branch so the dispatch job (on a different runner) can see them.

    Returns updated state.
    """
    phase = state.get("current_phase", "plan")

    if phase == "plan":
        state = run_plan(state, config)
    elif phase == "edit":
        state = run_edit(state, config, system_prompt)
    else:
        logger.warning(f"Unexpected phase for LLM work: {phase}")
        state["current_phase"] = "failed"
        state["pending_decision"] = {
            "next_phase": "failed",
            "reasoning": f"Unexpected phase: {phase}",
        }
        save_state(state)

    # Commit+push so dispatch job on a different runner can see the results
    _commit_and_push(state)
    return state


def _commit_and_push(state: dict) -> None:
    """Commit state file and any modified files, then push."""
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

    # Stage state file and modified files
    run_git("add", STATE_FILE)
    for fpath in state.get("files_modified", []):
        try:
            run_git("add", fpath)
        except RuntimeError as e:
            logger.warning(f"Could not stage {fpath}: {e}")

    # Check if there's anything to commit
    try:
        run_git("diff", "--cached", "--quiet")
        logger.info("No changes to commit after LLM work")
        return
    except RuntimeError:
        pass  # There are staged changes

    round_num = state.get("round_number", 1)
    phase = state.get("current_phase", "unknown")
    files = state.get("files_modified", [])

    if files:
        file_list = ", ".join(files[-3:])
        commit_msg = f"🧠 round {round_num} ({phase}): edited {file_list}"
    else:
        commit_msg = f"🧠 round {round_num} ({phase}): state update"

    run_git("commit", "-m", commit_msg)
    run_git("push")
    logger.info(f"LLM work committed and pushed: {commit_msg}")


def run_plan(state: dict, config: dict) -> dict:
    """Run the planner: 1 LLM call to analyze issue and produce plan steps.

    Updates state with plan_steps, current_step, pending_decision, round_log.
    """
    from main import llm_complete, get_model_name
    from planner import parse_planner_output

    issue_number = state["issue_number"]
    issue_title = state["issue_title"]
    issue_body = state.get("issue_body", "(no description)")
    repo_files = state.get("repo_files_snapshot", [])

    file_list = "\n".join(f"- `{f}`" for f in repo_files)

    prompt = (
        f"You are a software engineering planner. Analyze this GitHub issue "
        f"and produce a step-by-step implementation plan.\n\n"
        f"## GitHub Issue #{issue_number}: {issue_title}\n\n"
        f"{issue_body}\n\n"
        f"## Repository Files\n\n{file_list}\n\n"
        f"## Instructions\n\n"
        f"Create a concrete plan with 3-8 steps to implement this issue. "
        f"Each step should be a specific action (read a file, edit a file, "
        f"create a new file, verify something).\n\n"
        f"{PLANNER_SCHEMA}"
    )

    logger.info("Running planner LLM call...")
    try:
        max_tokens = config.get("parallel", {}).get("planner_max_tokens", 2000)
        raw = llm_complete(config, prompt, max_tokens=max_tokens, temperature=0.3)
        state["total_llm_calls"] = state.get("total_llm_calls", 0) + 1
        logger.info(f"Planner raw output ({len(raw)} chars): {raw[:200]}...")

        # Parse the output — reuse planner's robust JSON extraction
        plan_data = parse_planner_output(raw)

        # Extract plan steps
        plan_steps = plan_data.get("plan_steps", [])
        if not plan_steps:
            raise ValueError("Planner produced no steps")

        # Normalize steps
        normalized = []
        for i, step in enumerate(plan_steps[:8]):  # Cap at 8
            normalized.append({
                "id": step.get("id", i + 1),
                "type": step.get("type", "edit"),
                "description": step.get("description", f"Step {i + 1}"),
                "status": "pending",
                "result_summary": "",
            })

        state["plan_steps"] = normalized
        state["current_step"] = 1
        state["consecutive_errors"] = 0

        # Decide next phase
        next_action = plan_data.get("next_action", {})
        next_phase = next_action.get("phase", "edit")
        reasoning = next_action.get("reasoning", "Plan complete, proceeding to edit")

        state["pending_decision"] = {
            "next_phase": next_phase,
            "reasoning": reasoning,
        }

        # Log this round
        step_summary = "; ".join(s["description"][:60] for s in normalized[:3])
        append_round_log(
            state, "plan",
            f"Created {len(normalized)}-step plan: {step_summary}...",
        )

        logger.info(f"Plan created with {len(normalized)} steps, next phase: {next_phase}")

    except Exception as e:
        logger.error(f"Planner failed: {e}")
        state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1

        # Create a minimal fallback plan
        state["plan_steps"] = [
            {"id": 1, "type": "read", "description": "Read relevant files",
             "status": "pending", "result_summary": ""},
            {"id": 2, "type": "edit", "description": f"Implement: {state['issue_title']}",
             "status": "pending", "result_summary": ""},
            {"id": 3, "type": "verify", "description": "Verify changes",
             "status": "pending", "result_summary": ""},
        ]
        state["current_step"] = 1
        state["pending_decision"] = {
            "next_phase": "edit",
            "reasoning": f"Planner failed ({e}), using fallback plan",
        }
        append_round_log(state, "plan", f"Planner failed: {e}, using fallback plan")

    save_state(state)
    return state


def run_edit(state: dict, config: dict, system_prompt: str) -> dict:
    """Run the editor: multi-turn tool-call loop to implement the plan.

    Uses the same text-based tool-call pattern as the existing editor.
    After tool calls, LLM decides: done | needs_more_edits.

    Updates state: plan step statuses, files_modified, pending_decision, round_log.
    """
    from main import _parse_tool_call, _build_tool_prompt, execute_tool_safely
    from tools import get_file_changes, reset_file_changes

    import openai

    reset_file_changes()

    dynamic_config = config.get("dynamic", {})
    max_tool_calls = dynamic_config.get("max_tool_calls_per_edit", 4)
    model = state.get("model", "qwen3:8b")

    client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        timeout=1800.0,
    )

    # Build context from state
    messages = build_llm_context(state, system_prompt)

    actions = []
    turn = 0
    loop_start = time.time()

    for turn in range(max_tool_calls):
        turn_start = time.time()
        elapsed = turn_start - loop_start
        logger.info(f"--- Editor turn {turn + 1}/{max_tool_calls} (elapsed: {elapsed:.1f}s) ---")

        try:
            llm_start = time.time()
            response = client.chat.completions.create(
                model=model,
                max_tokens=config.get("max_tokens", 8096),
                temperature=config.get("temperature", 0),
                messages=messages,
            )
            llm_elapsed = time.time() - llm_start
            state["total_llm_calls"] = state.get("total_llm_calls", 0) + 1
            logger.info(f"  LLM response: {llm_elapsed:.1f}s")
        except Exception as e:
            logger.warning(f"API error on editor turn {turn + 1}: {e}")
            state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
            time.sleep(2)
            continue

        content = (response.choices[0].message.content or "").strip()
        messages.append({"role": "assistant", "content": content})

        # Check for DONE signal
        if content.upper().startswith("DONE:") or (not _parse_tool_call(content) and turn > 0):
            logger.info(f"Editor finished: {content[:200]}...")
            state["consecutive_errors"] = 0

            # Mark remaining steps as completed
            for step in state.get("plan_steps", []):
                if step.get("status") == "pending":
                    step["status"] = "completed"
                    step["result_summary"] = "Completed in edit phase"

            state["pending_decision"] = {
                "next_phase": "done",
                "reasoning": content[:200],
            }
            break

        # Try to parse a tool call
        tool_call = _parse_tool_call(content)

        if tool_call:
            name, args = tool_call
            logger.info(f"  Tool call: {name}({json.dumps(args)[:100]})")
            tool_start = time.time()
            result = execute_tool_safely(name, args)
            tool_elapsed = time.time() - tool_start
            logger.info(f"  Tool result ({tool_elapsed:.1f}s): {result[:100]}...")

            messages.append({
                "role": "user",
                "content": f"Tool result for {name}:\n{result}",
            })

            # Track action
            args_summary = json.dumps(args)[:100]
            result_summary = result[:500]
            actions.append(f"{name}({args_summary}) -> {result_summary[:100]}")

            # Update plan step status based on tool type
            _update_step_progress(state, name, args)

            state["consecutive_errors"] = 0
        else:
            # No tool call and not DONE — unexpected, but continue
            logger.warning(f"No tool call parsed from: {content[:200]}...")
            messages.append({
                "role": "user",
                "content": "Please make your next change using a tool call JSON, "
                           "or respond with DONE: if all changes are complete.",
            })

        turn_elapsed = time.time() - turn_start
        logger.info(f"  Turn total: {turn_elapsed:.1f}s")
    else:
        # Hit max turns without DONE
        logger.warning("Editor reached max turns without finishing.")
        state["pending_decision"] = {
            "next_phase": "done",
            "reasoning": f"Max tool calls ({max_tool_calls}) reached, finalizing with current changes",
        }

    # Record file changes
    changes = get_file_changes()
    if changes:
        existing_files = set(state.get("files_modified", []))
        existing_files.update(changes.keys())
        state["files_modified"] = sorted(existing_files)

    total_elapsed = time.time() - loop_start
    files_changed = list(changes.keys()) if changes else []

    append_round_log(
        state, "edit",
        f"Edited {len(files_changed)} files in {turn + 1} turns ({total_elapsed:.1f}s)",
        actions=actions,
        files_modified=files_changed,
    )

    logger.info(f"Editor finished in {total_elapsed:.1f}s "
                f"({turn + 1} turns, {len(files_changed)} files changed)")

    save_state(state)
    return state


def _update_step_progress(state: dict, tool_name: str, tool_args: dict):
    """Update plan step statuses based on the tool being used."""
    steps = state.get("plan_steps", [])
    current = state.get("current_step", 1)

    # Find the current pending step and mark it in progress
    for step in steps:
        if step.get("status") == "pending":
            step["status"] = "in_progress"
            break

    # If it's an edit/write tool, mark current step as completed
    if tool_name in ("write_file", "edit_file"):
        path = tool_args.get("path", "")
        for step in steps:
            if step.get("status") == "in_progress":
                step["status"] = "completed"
                step["result_summary"] = f"Modified {path}"
                state["current_step"] = step["id"] + 1
                break
