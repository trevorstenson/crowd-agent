from __future__ import annotations

import os
from datetime import datetime, timezone

from github import Auth, Github

from track_decay import reaction_weight, weighted_net_reactions

TRACK_NAMES = ("capability", "reliability", "survival", "legibility", "virality")


def get_repo():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    owner = os.environ.get("REPO_OWNER", "trevorstenson")
    name = os.environ.get("REPO_NAME", "crowd-agent")
    gh = Github(auth=Auth.Token(token))
    return gh.get_repo(f"{owner}/{name}")


def normalize_track(value: str) -> str:
    normalized = (value or "").strip().lower().replace(" ", "-")
    return normalized if normalized in TRACK_NAMES else ""


def issue_track(issue) -> str:
    title = (issue.title or "").strip()
    if not title.lower().startswith("track:"):
        return ""
    for label in getattr(issue, "labels", []) or []:
        name = getattr(label, "name", "")
        if name.startswith("track:"):
            return normalize_track(name.split(":", 1)[1])
    if title.lower().startswith("track:"):
        return normalize_track(title.split(":", 1)[1])
    return ""


def main():
    repo = get_repo()
    now = datetime.now(timezone.utc)

    print(f"Track decay debug at {now.isoformat()}")
    print("Decay buckets: <1d => 1.0, <3d => 0.5, <7d => 0.2, >=7d => 0.0")
    print()

    for issue in repo.get_issues(state="open"):
        if getattr(issue, "pull_request", None):
            continue
        track = issue_track(issue)
        if not track:
            continue

        reactions = [r for r in issue.get_reactions() if getattr(r, "content", None) in {"+1", "-1"}]
        weighted_score = weighted_net_reactions(reactions, now=now)
        pressure = max(0.0, min(1.0, 0.5 + 0.08 * weighted_score))

        print(f"Track: {track} | Issue #{issue.number} | {issue.title}")
        print(f"Weighted score: {weighted_score:.2f} | Pressure: {pressure:.2f}")
        if not reactions:
            print("  No +1/-1 reactions found.")
            print()
            continue

        sorted_reactions = sorted(
            reactions,
            key=lambda reaction: getattr(reaction, "created_at", now),
            reverse=True,
        )
        for reaction in sorted_reactions:
            created_at = reaction.created_at.astimezone(timezone.utc)
            age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
            weight = reaction_weight(created_at, now=now)
            sign = 1.0 if reaction.content == "+1" else -1.0
            contribution = sign * weight
            user = getattr(getattr(reaction, "user", None), "login", "unknown")
            print(
                f"  {reaction.content:>2} | user={user:<20} | age={age_days:>4.2f}d | "
                f"weight={weight:>3.1f} | contribution={contribution:>4.1f}"
            )
        print()


if __name__ == "__main__":
    main()
