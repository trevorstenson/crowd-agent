"""
Editor phase for the parallel map-reduce agent.

Builds a mega-prompt containing all exploration results as inline file
contents, the planner's strategy and edit hints, and the issue context.
Then runs a mini agent loop (same pattern as _run_agent_ollama from
main.py) for focused editing — the LLM doesn't need to explore because
all context is already provided.
"""

import glob
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# Maximum characters to include per file in the mega-prompt
MAX_FILE_CONTENT_CHARS = 8000


def load_exploration_results(results_dir: str = "exploration-results") -> list[dict]:
    """Load all exploration result JSON files from the results directory."""
    results = []
    pattern = os.path.join(results_dir, "*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                results.append(json.load(f))
        except Exception as e:
            logger.warning(f"Could not load {path}: {e}")
    return results


def _format_exploration_results(results: list[dict]) -> str:
    """Format exploration results as readable context for the editor prompt."""
    if not results:
        return "(no exploration results — direct edit mode)"

    sections = []
    for task_result in results:
        task_id = task_result.get("task_id", "unknown")
        description = task_result.get("description", "")
        sections.append(f"### {task_id}: {description}")

        for step in task_result.get("steps", []):
            tool = step.get("tool", "")
            args = step.get("args", {})
            success = step.get("success", False)
            result = step.get("result", "")

            # Format the step
            status = "OK" if success else "FAILED"
            args_str = json.dumps(args)
            sections.append(f"\n**{tool}({args_str})** [{status}]")

            # Truncate long results
            if len(result) > MAX_FILE_CONTENT_CHARS:
                result = result[:MAX_FILE_CONTENT_CHARS] + "\n... (truncated)"
            sections.append(f"```\n{result}\n```")

    return "\n".join(sections)


def build_mega_prompt(issue_number: int, issue_title: str, issue_body: str,
                      plan: dict, results: list[dict],
                      repo_files: list[str]) -> str:
    """Build the mega-prompt with all exploration context for the editor.

    This prompt gives the LLM everything it needs to make edits without
    any exploration calls.
    """
    # Format exploration results
    results_text = _format_exploration_results(results)

    # Format repo file list
    file_list = "\n".join(f"- `{f}`" for f in repo_files)

    # Get edit hints
    edit_hints = plan.get("edit_hints", {})
    target_files = edit_hints.get("target_files", [])
    approach = edit_hints.get("approach", "")
    reasoning = plan.get("reasoning", "")

    targets_str = ", ".join(f"`{f}`" for f in target_files) if target_files else "(not specified)"

    return (
        f"## Task\n\n"
        f"Implement GitHub issue #{issue_number}: {issue_title}\n\n"
        f"{issue_body or '(no description)'}\n\n"
        f"## Planner Analysis\n\n"
        f"**Reasoning:** {reasoning}\n"
        f"**Target files:** {targets_str}\n"
        f"**Approach:** {approach}\n\n"
        f"## Exploration Results\n\n"
        f"The following file contents and directory listings were gathered for you. "
        f"All the context you need is below — do NOT call read_file or list_files "
        f"unless you need a file that wasn't explored.\n\n"
        f"{results_text}\n\n"
        f"## Repository Structure\n\n{file_list}\n\n"
        f"## Instructions\n\n"
        f"All context has been provided above. Focus on making the edits:\n"
        f"- Use write_file to create NEW files or fully rewrite existing ones: "
        f'{{"tool": "write_file", "args": {{"path": "file.py", "content": "full content"}}}}\n'
        f"- Use edit_file for targeted find-and-replace edits on EXISTING files: "
        f'{{"tool": "edit_file", "args": {{"path": "file.py", "old_string": "text to find", "new_string": "replacement text"}}}}\n'
        f"- IMPORTANT: edit_file requires old_string and new_string, NOT content\n"
        f"- Do NOT explore — all needed file contents are above\n"
        f"- When ALL changes are complete, respond with a plain text summary (no JSON)\n"
    )


def run_editor(config: dict, system_prompt: str, plan: dict,
               issue_number: int, issue_title: str, issue_body: str,
               results: list[dict], repo_files: list[str]) -> dict:
    """Run the editor agent loop with full exploration context.

    Uses the same text-based tool-call pattern as _run_agent_ollama
    from main.py, but with a mega-prompt and limited turns.

    Returns file changes dict.
    """
    from main import (
        _parse_tool_call, _build_tool_prompt, execute_tool_safely,
        get_model_name, get_llm_provider,
    )
    from tools import get_file_changes, reset_file_changes

    import openai

    reset_file_changes()

    # Use EDITOR_MODEL env var if set, otherwise default model
    editor_model = os.environ.get("EDITOR_MODEL", get_model_name(config))
    parallel_config = config.get("parallel", {})
    max_edit_turns = parallel_config.get("max_edit_turns", 4)

    client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        timeout=1800.0,
    )

    # Build the mega-prompt
    user_prompt = build_mega_prompt(
        issue_number, issue_title, issue_body, plan, results, repo_files
    )
    tool_prompt = _build_tool_prompt()

    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + tool_prompt},
        {"role": "user", "content": user_prompt},
    ]

    loop_start = time.time()
    for turn in range(max_edit_turns):
        turn_start = time.time()
        elapsed_total = turn_start - loop_start
        print(f"--- Editor turn {turn + 1}/{max_edit_turns} (elapsed: {elapsed_total:.1f}s) ---")

        try:
            llm_start = time.time()
            response = client.chat.completions.create(
                model=editor_model,
                max_tokens=config.get("max_tokens", 8096),
                temperature=config.get("temperature", 0),
                messages=messages,
            )
            llm_elapsed = time.time() - llm_start
            print(f"  LLM response: {llm_elapsed:.1f}s")
        except Exception as e:
            logger.warning(f"API error on editor turn {turn + 1}: {e}")
            time.sleep(2)
            continue

        content = (response.choices[0].message.content or "").strip()
        messages.append({"role": "assistant", "content": content})

        # Try to parse a tool call
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
            # No tool call — editor is done
            print(f"Editor summary: {content[:200]}...")
            break

        turn_elapsed = time.time() - turn_start
        print(f"  Turn total: {turn_elapsed:.1f}s")
    else:
        logger.warning("Editor reached max turns without finishing.")

    total_elapsed = time.time() - loop_start
    changes = get_file_changes()
    print(f"Editor finished in {total_elapsed:.1f}s ({turn + 1} turns, {len(changes)} files changed)")
    return changes
