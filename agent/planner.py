"""
Planner phase for the parallel map-reduce agent.

One LLM call produces a structured JSON exploration plan that determines
which files to read and directories to list. The plan drives a dynamic
GitHub Actions job matrix — the AI literally shapes the infrastructure.

For simple issues, outputs strategy="direct_edit" with no exploration
tasks, skipping the explore phase entirely.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Read-only tools allowed in exploration tasks
ALLOWED_EXPLORE_TOOLS = {"read_file", "list_files", "search_files"}

PLANNER_SCHEMA_DESCRIPTION = """\
You must respond with ONLY a valid JSON object (no markdown fences, no explanation) matching this schema:

{
  "strategy": "explore_then_edit" or "direct_edit",
  "reasoning": "<1-2 sentences explaining your approach>",
  "exploration_tasks": [
    {
      "id": "explore-0",
      "description": "<what this task discovers>",
      "steps": [
        {"tool": "read_file", "args": {"path": "some/file.py"}},
        {"tool": "list_files", "args": {"directory": "some/dir"}},
        {"tool": "search_files", "args": {"pattern": "some_pattern"}}
      ]
    }
  ],
  "edit_hints": {
    "target_files": ["file1.py", "file2.py"],
    "approach": "<brief description of what edits to make>"
  }
}

Rules:
- strategy must be "explore_then_edit" or "direct_edit"
- For simple issues (typo fixes, small config changes), use "direct_edit" with exploration_tasks=[]
- For complex issues, use "explore_then_edit" with 1-8 exploration tasks
- Each task can have 1-6 steps
- Only these tools are allowed in steps: read_file, list_files, search_files
- read_file args: {"path": "<relative path>"}
- list_files args: {"directory": "<relative path>"}
- search_files args: {"pattern": "<text or regex>"}
- Task IDs must be "explore-0", "explore-1", etc.
- edit_hints.target_files: list of files you expect to modify
- edit_hints.approach: brief description of the edit strategy
"""


def build_planner_prompt(issue_number: int, issue_title: str, issue_body: str, repo_files: list[str]) -> str:
    """Build the prompt for the planner LLM call."""
    file_list = "\n".join(f"- `{f}`" for f in repo_files)

    return (
        f"You are a software engineering planner. Your job is to analyze a GitHub issue "
        f"and produce a structured exploration plan.\n\n"
        f"## GitHub Issue #{issue_number}: {issue_title}\n\n"
        f"{issue_body or '(no description)'}\n\n"
        f"## Repository Files\n\n{file_list}\n\n"
        f"## Instructions\n\n"
        f"Analyze the issue and decide:\n"
        f"1. Is this simple enough to edit directly (typo fix, small change)? → strategy: direct_edit\n"
        f"2. Do you need to explore the codebase first? → strategy: explore_then_edit\n\n"
        f"For explore_then_edit, create exploration tasks that will run IN PARALLEL "
        f"(each as a separate GitHub Actions job). Group related reads into the same task. "
        f"Each task runs independently — it can't see results from other tasks.\n\n"
        f"{PLANNER_SCHEMA_DESCRIPTION}"
    )


def parse_planner_output(raw_text: str) -> dict:
    """Parse the planner's JSON output, handling common LLM formatting issues.

    Strips markdown fences and uses brace-depth matching to extract JSON.
    """
    text = raw_text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Brace-depth extraction — find outermost {...}
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
                start = -1

    raise ValueError(f"Could not parse planner output as JSON: {raw_text[:200]}...")


def validate_plan(plan: dict, config: dict | None = None) -> dict:
    """Validate and sanitize the planner output.

    Ensures strategy is valid, caps tasks/steps, and filters to read-only tools.
    Returns the validated plan (may be modified in-place).
    """
    parallel_config = (config or {}).get("parallel", {})
    max_tasks = parallel_config.get("max_exploration_tasks", 8)
    max_steps = parallel_config.get("max_steps_per_task", 6)

    # Validate strategy
    strategy = plan.get("strategy", "explore_then_edit")
    if strategy not in ("explore_then_edit", "direct_edit"):
        logger.warning(f"Invalid strategy '{strategy}', defaulting to explore_then_edit")
        plan["strategy"] = "explore_then_edit"

    # Ensure required fields
    if "reasoning" not in plan:
        plan["reasoning"] = ""
    if "exploration_tasks" not in plan:
        plan["exploration_tasks"] = []
    if "edit_hints" not in plan:
        plan["edit_hints"] = {"target_files": [], "approach": ""}

    # For direct_edit, clear exploration tasks
    if plan["strategy"] == "direct_edit":
        plan["exploration_tasks"] = []
        return plan

    # Cap number of tasks
    tasks = plan["exploration_tasks"][:max_tasks]

    validated_tasks = []
    for idx, task in enumerate(tasks):
        # Normalize task ID
        task["id"] = f"explore-{idx}"

        if "description" not in task:
            task["description"] = f"Exploration task {idx}"

        # Validate and cap steps
        steps = task.get("steps", [])[:max_steps]
        valid_steps = []
        for step in steps:
            tool = step.get("tool", "")
            if tool not in ALLOWED_EXPLORE_TOOLS:
                logger.warning(f"Skipping disallowed tool '{tool}' in task {task['id']}")
                continue
            if "args" not in step:
                step["args"] = {}
            valid_steps.append(step)

        if valid_steps:
            task["steps"] = valid_steps
            validated_tasks.append(task)

    plan["exploration_tasks"] = validated_tasks
    return plan


def fallback_plan(issue_title: str, repo_files: list[str]) -> dict:
    """Generate a conservative exploration plan when the LLM fails.

    Uses keyword matching from the issue title to find relevant files.
    """
    # Extract keywords from issue title (3+ char words, lowercased)
    words = re.findall(r"[a-zA-Z_]{3,}", issue_title.lower())
    # Common words to skip
    stop_words = {"the", "and", "for", "add", "fix", "update", "implement", "create",
                  "make", "change", "modify", "remove", "delete", "new", "use", "with"}
    keywords = [w for w in words if w not in stop_words]

    # Find files matching keywords
    matching_files = []
    for f in repo_files:
        f_lower = f.lower()
        if any(kw in f_lower for kw in keywords):
            matching_files.append(f)

    # Build exploration steps — read matching files + list key directories
    steps = []
    for f in matching_files[:4]:
        steps.append({"tool": "read_file", "args": {"path": f}})

    # Always include root listing
    steps.append({"tool": "list_files", "args": {"directory": "."}})

    tasks = []
    if steps:
        tasks.append({
            "id": "explore-0",
            "description": f"Read files related to: {issue_title}",
            "steps": steps[:6],
        })

    return {
        "strategy": "explore_then_edit" if tasks else "direct_edit",
        "reasoning": f"Fallback plan: keyword-matched files from issue title '{issue_title}'",
        "exploration_tasks": tasks,
        "edit_hints": {
            "target_files": matching_files[:5],
            "approach": f"Address the issue: {issue_title}",
        },
    }


def run_planner(config: dict, issue_number: int, issue_title: str,
                issue_body: str, repo_files: list[str]) -> dict:
    """Main planner entry point.

    Makes one LLM call to produce a structured exploration plan.
    Falls back to keyword-based plan on failure.
    """
    # Import here to avoid circular dependency
    from main import llm_complete

    parallel_config = config.get("parallel", {})
    max_tokens = parallel_config.get("planner_max_tokens", 2000)

    prompt = build_planner_prompt(issue_number, issue_title, issue_body, repo_files)

    try:
        raw = llm_complete(config, prompt, max_tokens=max_tokens, temperature=0.3)
        logger.info(f"Planner raw output ({len(raw)} chars): {raw[:200]}...")
        plan = parse_planner_output(raw)
        plan = validate_plan(plan, config)
        logger.info(f"Plan: strategy={plan['strategy']}, tasks={len(plan['exploration_tasks'])}")
        return plan
    except Exception as e:
        logger.warning(f"Planner failed, using fallback: {e}")
        plan = fallback_plan(issue_title, repo_files)
        plan = validate_plan(plan, config)
        return plan
