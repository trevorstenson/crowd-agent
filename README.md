# Crowd Agent

**An AI coding agent governed by the crowd.**

Every night at midnight UTC, Crowd Agent reads the top-voted GitHub Issue, implements it using Claude, and opens a pull request. The twist: the agent's own source code is part of the repo — so the community can vote to change how the agent thinks, what tools it has, and how it behaves.

The agent modifies itself.

## How It Works

1. **Vote** — Browse [open issues](https://github.com/trevorstenson/crowd-agent/issues?q=is%3Aissue+is%3Aopen+label%3Avoting) and thumbs-up the ones you want built
2. **Build** — Every night, the agent picks the top-voted issue and implements it
3. **Review** — The agent opens a PR. A human approves it to merge.
4. **Ship** — The change goes live. The cycle repeats.

## Participate

- **Submit a feature:** [Open an issue](https://github.com/trevorstenson/crowd-agent/issues/new) and add the `voting` label
- **Vote:** React with a thumbs-up on any issue you want built
- **Review:** Look at the agent's PRs and approve or request changes
- **Watch:** Check the [live dashboard](https://trevorstenson.github.io/crowd-agent/) to see what's happening

## The Agent

The agent is a ~200-line Python script ([`agent/main.py`](agent/main.py)) that:
- Finds the winning issue via the GitHub API
- Calls the Claude API with tool use to implement the feature
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

## Rules

See the [Constitution](CONSTITUTION.md) for governance rules, including protected files and amendment procedures.

## Dashboard

Visit the live dashboard: **[trevorstenson.github.io/crowd-agent](https://trevorstenson.github.io/crowd-agent/)**

## Stack

- **Agent:** Python, Claude Sonnet 4.5 API, PyGithub
- **Dashboard:** Vanilla HTML/CSS/JS, GitHub Pages
- **CI/CD:** GitHub Actions (nightly build, auto-merge, deploy)

---

*Built by the community, one vote at a time.*
> *Last updated by Crowd Agent on 2023-10-05*