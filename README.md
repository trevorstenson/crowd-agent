# Crowd Agent

**An AI coding agent governed by the crowd.**

Every night at midnight UTC, Crowd Agent reads the top-voted GitHub Issue, implements it using Claude, and opens a pull request. The twist: the agent's own source code is part of the repo — so the community can vote to change how the agent thinks, what tools it has, and how it behaves.

The agent modifies itself.

## How It Works

1. **Vote** — Browse [open issues](https://github.com/trevorstenson/crowd-agent/issues?q=is%3Aissue+is%3A

## Project Structure

```
crowd-agent/
├── agent/
│   ├── __pycache__/
│   ├── checkpoint.py
│   ├── config.json
│   ├── main.py
│   ├── memory.json
│   ├── prompt.md
│   ├── tools.py
│   ├── twitter.py
│   └── ... 
├── dashboard/
│   └── ...
├── .github/
│   └── workflows/
├── CHANGELOG.md
├── CONSTITUTION.md
├── README.md
└── requirements.txt
```