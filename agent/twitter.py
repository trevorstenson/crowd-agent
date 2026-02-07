"""
Twitter/X integration for Crowd Agent nightly builds.

Posts tweets at two points in the build cycle:
- Pre-build: announces which issue won the vote
- Post-build: links to the PR, or reports failure

This module is a non-critical side effect — if tweeting fails,
the build continues normally.
"""

import os


def is_twitter_configured() -> bool:
    """Check if all required Twitter env vars are set."""
    required = [
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_TOKEN_SECRET",
    ]
    return all(os.environ.get(var) for var in required)


def get_twitter_client():
    """Lazy-import tweepy and return an authenticated Client, or None."""
    if not is_twitter_configured():
        print("Twitter: credentials not configured, skipping.")
        return None
    try:
        import tweepy
    except ImportError:
        print("Twitter: tweepy not installed, skipping.")
        return None

    return tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )


def _truncate_tweet(text: str, max_len: int = 280) -> str:
    """Ensure tweet fits in 280 chars. Preserves URLs by trimming the message body."""
    if len(text) <= max_len:
        return text
    # Split into lines; last line is usually the URL
    lines = text.strip().split("\n")
    if len(lines) >= 2:
        url_line = lines[-1].strip()
        body = "\n".join(lines[:-1])
        available = max_len - len(url_line) - 2  # 2 for \n\n
        if available > 10:
            return body[:available - 1] + "\u2026" + "\n\n" + url_line
    return text[:max_len - 1] + "\u2026"


def _post_tweet(text: str, dry_run: bool = False) -> str | None:
    """Post a tweet. Returns tweet ID on success, None on failure."""
    text = _truncate_tweet(text)
    if dry_run:
        print(f"Twitter (dry run): {text}")
        return "dry-run"
    client = get_twitter_client()
    if client is None:
        return None
    response = client.create_tweet(text=text)
    tweet_id = response.data["id"]
    print(f"Twitter: posted tweet {tweet_id}")
    return tweet_id


# --- Public API ---

def format_build_start_tweet(issue_title: str, issue_number: int, repo_owner: str, repo_name: str) -> str:
    url = f"https://github.com/{repo_owner}/{repo_name}/issues/{issue_number}"
    return f"Tonight the community voted for Fenton to build: {issue_title}\n\n{url}"


def format_build_success_tweet(issue_title: str, pr_url: str) -> str:
    return f"Fenton just built: {issue_title}\n\nHere's the PR: {pr_url}"


def format_build_failure_tweet(issue_title: str, issue_number: int, repo_owner: str, repo_name: str) -> str:
    url = f"https://github.com/{repo_owner}/{repo_name}/issues/{issue_number}"
    return (
        f"Fenton attempted to build: {issue_title} but hit a snag. "
        f"The issue is back in the voting pool.\n\n{url}"
    )


def tweet_build_start(issue_title: str, issue_number: int, repo_owner: str, repo_name: str, dry_run: bool = False) -> str | None:
    text = format_build_start_tweet(issue_title, issue_number, repo_owner, repo_name)
    return _post_tweet(text, dry_run=dry_run)


def tweet_build_success(issue_title: str, pr_url: str, dry_run: bool = False) -> str | None:
    text = format_build_success_tweet(issue_title, pr_url)
    return _post_tweet(text, dry_run=dry_run)


def tweet_build_failure(issue_title: str, issue_number: int, repo_owner: str, repo_name: str, dry_run: bool = False) -> str | None:
    text = format_build_failure_tweet(issue_title, issue_number, repo_owner, repo_name)
    return _post_tweet(text, dry_run=dry_run)
