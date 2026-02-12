"""
Explorer phase for the parallel map-reduce agent.

Deterministic Python — zero LLM calls. Each explorer job receives a
single exploration task from the planner's output and executes the
steps sequentially using tools.py functions. Results are written as
JSON artifacts for the editor phase.

Each step is sub-second (pure file I/O). An explorer job completes
in ~30s including GitHub Actions overhead.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


def execute_exploration_task(task: dict) -> dict:
    """Execute a single exploration task and return structured results.

    Args:
        task: A task dict with "id", "description", and "steps" list.
              Each step has "tool" and "args".

    Returns:
        Dict with task metadata and step results.
    """
    from tools import execute_tool

    task_id = task.get("id", "unknown")
    description = task.get("description", "")
    steps = task.get("steps", [])

    results = {
        "task_id": task_id,
        "description": description,
        "step_count": len(steps),
        "steps": [],
    }

    for i, step in enumerate(steps):
        tool_name = step.get("tool", "")
        args = step.get("args", {})

        logger.info(f"[{task_id}] Step {i+1}/{len(steps)}: {tool_name}({json.dumps(args)[:80]})")

        try:
            result = execute_tool(tool_name, args)
            results["steps"].append({
                "step_index": i,
                "tool": tool_name,
                "args": args,
                "success": not (isinstance(result, str) and result.startswith("Error")),
                "result": result,
            })
        except Exception as e:
            logger.error(f"[{task_id}] Step {i+1} failed: {e}")
            results["steps"].append({
                "step_index": i,
                "tool": tool_name,
                "args": args,
                "success": False,
                "result": f"Exception: {e}",
            })

    return results


def main():
    """Entry point for explorer jobs.

    Reads TASK_ID from env, loads the plan from exploration-plan.json,
    finds its assigned task, executes it, and writes results to
    exploration-results/{task_id}.json.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    task_id = os.environ.get("TASK_ID", "")
    if not task_id:
        logger.error("TASK_ID environment variable not set")
        sys.exit(1)

    # Load the exploration plan
    plan_path = "exploration-plan.json"
    if not os.path.isfile(plan_path):
        logger.error(f"Plan file not found: {plan_path}")
        sys.exit(1)

    with open(plan_path) as f:
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

    logger.info(f"Executing task: {task_id} — {task.get('description', '')}")

    # Execute the task
    results = execute_exploration_task(task)

    # Write results
    results_dir = "exploration-results"
    os.makedirs(results_dir, exist_ok=True)
    result_path = os.path.join(results_dir, f"{task_id}.json")

    with open(result_path, "w") as f:
        json.dump(results, f, indent=2)
        f.write("\n")

    logger.info(f"Results written to {result_path}")
    step_count = len(results["steps"])
    success_count = sum(1 for s in results["steps"] if s["success"])
    logger.info(f"Completed: {success_count}/{step_count} steps succeeded")


if __name__ == "__main__":
    main()
