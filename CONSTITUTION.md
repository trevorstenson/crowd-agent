# Crowd Agent Constitution

The rules that govern how Crowd Agent operates. These rules can be amended through community-voted pull requests.

---

## 1. Voting

- Anyone can open an issue in this repository.
- To enter the vote pool, add the `voting` label to your issue.
- Votes are counted by thumbs-up reactions on issues.
- The agent builds the top-voted issue every night at midnight UTC.
- One issue is built per night.

## 2. Building

- The agent picks the issue with the most thumbs-up reactions from the `voting` pool.
- The agent relabels the issue from `voting` to `building` while working.
- The agent opens a pull request with its implementation and links it to the issue.

## 3. Review & Merge

- Pull requests opened by the agent require **1 human approval** to merge.
- If a PR receives a "changes requested" review, it will not be auto-merged.
- If 48 hours pass with no approval, the PR is closed and the issue is labeled `rejected`.
- Approved PRs are squash-merged automatically.

## 4. Protected Files

The following files **cannot be modified by the agent** and require human-only pull requests:

- `.github/workflows/nightly-build.yml` — the stable launcher
- `CONSTITUTION.md` — this file

All other files — including `agent/main.py`, `agent/tools.py`, `agent/prompt.md`, and the dashboard — are fair game for community-voted agent modification.

## 5. Failure Handling

- If the agent's nightly build crashes, a GitHub Issue is automatically opened with the error details.
- The community can diagnose the failure and vote on a fix.
- The failed issue is relabeled back to `voting` so it can be retried.

## 6. Amendments

- These rules can be amended via community-voted pull requests.
- Constitutional amendments follow the same voting and approval process as feature requests.
- Amendment PRs must be opened and merged by humans, not the agent.

## 7. Scope

- The agent operates only within this repository.
- The agent has no access to external services, secrets (other than API keys for its operation), or other repositories.
- The agent's capabilities are defined by its tools in `agent/tools.py`, which the community can expand or restrict through votes.

---

*Last updated: Initial ratification*
