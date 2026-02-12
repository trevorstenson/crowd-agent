"""
CLI entry point for the dynamic round-based agent.

Called by the GitHub Actions workflow with:
    python agent/round_main.py --phase <route|llm_work|dispatch>

Each phase function corresponds to a job in the workflow:
- route:     Find issue or load state, determine what to do
- llm_work:  Run planner or editor LLM calls
- dispatch:  Commit state, trigger next round or finalize
"""

import argparse
import json
import logging
import os
import sys
import uuid

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
                delimiter = f"ghadelimiter_{uuid.uuid4().hex[:8]}"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")
        logger.info(f"Set output: {name}={value[:100]}...")
    else:
        logger.info(f"(no GITHUB_OUTPUT) {name}={value[:100]}...")


def phase_route():
    """Router job: find issue or load state, set outputs for downstream jobs."""
    from main import load_config
    from round_router import route_round

    print("=== Dynamic Agent: Route Phase ===")

    config = load_config()
    outputs = route_round(config)

    # Set all outputs for the workflow
    for key, value in outputs.items():
        _set_output(key, value)

    print(f"Route complete: phase={outputs['phase']}, "
          f"round={outputs['round_number']}, "
          f"issue=#{outputs['issue_number']}")


def phase_llm_work():
    """LLM job: run planner or editor based on state."""
    from main import load_config, load_system_prompt
    from round_state import load_state
    from round_llm import run_llm_work

    print("=== Dynamic Agent: LLM Work Phase ===")

    config = load_config()
    system_prompt = load_system_prompt()

    state = load_state()
    if state is None:
        raise RuntimeError("No state file found — route phase must run first")

    print(f"Phase: {state.get('current_phase')}, "
          f"Round: {state.get('round_number')}, "
          f"Issue: #{state.get('issue_number')}")

    state = run_llm_work(state, config, system_prompt)

    print(f"LLM work complete. Next phase: "
          f"{state.get('pending_decision', {}).get('next_phase', 'unknown')}")


def phase_dispatch():
    """Dispatch job: commit state, trigger next round or finalize."""
    from main import load_config
    from round_state import load_state
    from round_dispatch import dispatch_or_finalize

    print("=== Dynamic Agent: Dispatch Phase ===")

    config = load_config()

    state = load_state()
    if state is None:
        # llm_work may have crashed — try to report failure
        logger.error("No state file found in dispatch phase")
        # Check if we can recover issue number from env
        issue_number = os.environ.get("ISSUE_NUMBER", "0")
        if issue_number != "0":
            from round_dispatch import finalize_failure
            fake_state = {
                "issue_number": int(issue_number),
                "issue_title": "Unknown (state lost)",
            }
            finalize_failure(fake_state, "State file not found in dispatch phase — llm_work may have crashed")
        else:
            logger.error("Cannot recover — no state and no issue number")
        return

    print(f"Phase: {state.get('current_phase')}, "
          f"Round: {state.get('round_number')}, "
          f"Files modified: {state.get('files_modified', [])}")

    dispatch_or_finalize(state, config)

    print("Dispatch complete.")


def main():
    parser = argparse.ArgumentParser(description="Dynamic round-based agent phase dispatcher")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["route", "llm_work", "dispatch"],
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

    if args.phase == "route":
        phase_route()
    elif args.phase == "llm_work":
        phase_llm_work()
    elif args.phase == "dispatch":
        phase_dispatch()


if __name__ == "__main__":
    main()
