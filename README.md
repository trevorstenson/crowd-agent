# Crowd Agent

**A self-improving coding organism influenced by the internet.**

Every night at midnight UTC, Crowd Agent selects a mutation, implements it using Groq-hosted open models, and opens a pull request. The agent's own source code is part of the repo, so the internet can influence what traits it evolves toward.

The agent modifies itself.

## Mission

Crowd Agent is trying to become a more capable, autonomous, and legible software-building organism.

The goal is not just to close tickets. The goal is to improve the system's ability to plan, edit, evaluate, explain itself, and keep making progress in public under severe compute constraints.

Those constraints are part of the experiment. If the agent hits limits in inference speed, context size, API budget, or workflow runtime, the intended behavior is to adapt structurally rather than stall.

## How It Works

1. **Influence** — The crowd pushes a small set of evolution tracks and submits mutation proposals
2. **Select** — Every night, the agent chooses the next mutation from its roadmap, crowd proposals, and urgent survival work
3. **Build** — The agent implements the mutation and opens a PR
4. **Review** — A human approves it to merge
5. **Evolve** — The system carries the new behavior forward into the next cycle

## Autonomous Mode

If the crowd is quiet, Crowd Agent does not idle. It advances its autonomous roadmap and keeps improving itself anyway.

That backlog lives in:
- [`agent/mission.md`](agent/mission.md)
- [`agent/autonomous_roadmap.json`](agent/autonomous_roadmap.json)

This keeps the project moving toward a coherent long-term goal even when nobody is actively steering it.

## Governance

- [CONSTITUTION.md](CONSTITUTION.md) defines the durable rules and hard constraints
- [EVOLUTION_GOVERNANCE.md](EVOLUTION_GOVERNANCE.md) defines the current operating model

## Participate

- **Push a trait:** Influence the evolution tracks the agent should optimize for
- **Propose a mutation:** Suggest a compounding change to how the organism behaves via the [mutation proposal form](https://github.com/trevorstenson/crowd-agent/issues/new?template=mutation-proposal.yml)
- **Review:** Look at the agent's PRs and approve or request changes
- **Watch:** Check the [live dashboard](https://trevorstenson.github.io/crowd-agent/) to see what's happening

## The Agent

The agent is a Python script ([`agent/main.py`](agent/main.py)) that:
- Selects a mutation from autonomous roadmap work, crowd influence, and constraint-driven needs
- Keeps moving even when no direct task is submitted
- Calls Groq's OpenAI-compatible API with tool use to implement the mutation
- Creates a branch and pull request with the changes

Its tools, personality, and behavior are all defined in editable files that the community controls.

## Agent Tools

The agent has access to the following tools:

### read_file
Read the contents of a file in the repository.

### write_file
Write or overwrite a file in the repository.

### edit_file
Edit a file by finding and replacing a substring. This is more efficient than rewriting entire files.

**Example:**
```python
edit_file(
    path="config.json",
    old_string="""debug": false"",
    new_string="""debug": true""
)
```

This tool reduces token usage and improves reliability by allowing targeted edits instead of full file rewrites.

### list_files
List files and directories in a given directory.

### search_files
Search for text patterns across the repository using regex patterns.

## Dashboard

Visit the live dashboard: **[trevorstenson.github.io/crowd-agent](https://trevorstenson.github.io/crowd-agent/)**

## Stack

- **Agent:** Python, Groq API, PyGithub
- **Dashboard:** Vanilla HTML/CSS/JS, GitHub Pages
- **CI/CD:** GitHub Actions (nightly build, auto-merge, deploy)

---

*Built in public, one mutation at a time.*
