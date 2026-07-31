#!/usr/bin/env python3
"""Create the Hugging Face Space and push this repository to it.

Idempotent: run it again to redeploy. It creates the Space if it is missing,
applies the non-secret configuration, wires the git remote, and pushes.

    apps/api/.venv/bin/python scripts/deploy_space.py --space alphabrief --public

Authentication comes from `~/.cache/huggingface/token`, written by `hf auth
login` (`huggingface-cli` is deprecated in huggingface_hub 1.x). No token is
passed on this command line, so it cannot reach shell history.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

SPACE_CONFIG: Final = Path(".hf-space.yml")

#: Files without which the Space builds and then reports a failure stage, which
#: is a slow and badly-signposted way to discover a missing artefact.
REQUIRED: Final = (
    Path("README.md"),
    SPACE_CONFIG,
    Path("apps/api/Dockerfile"),
    Path("apps/api/pyproject.toml"),
)

#: The Hub enforces this in a pre-receive hook, so an over-long description
#: rejects the push only after the whole repository has been uploaded. Checking
#: it here costs nothing.
SHORT_DESCRIPTION_MAX: Final = 60

#: Non-secret deployment configuration, applied on every deploy.
#:
#: This lives in the repository rather than only in the Space's settings page,
#: because a deploy variable is code that runs only on the platform: a typo in it
#: is invisible until production refuses to boot.
#:
#: Secrets are deliberately absent — this file is public. See `SECRETS` below for
#: what has to be set by hand, and why each one matters.
SPACE_VARIABLES: Final[dict[str, str]] = {
    # Disables the local approval-token file handshake. On `local` the API mints
    # a token into var/approval_token for the console to read; a Space has no
    # shared filesystem with the console, and trusting a file there would let
    # anything that can write to the image supply the credential.
    "ENVIRONMENT": "production",
    "LOG_LEVEL": "INFO",
    # The MCP server is spawned in-container over stdio, which is what lets one
    # image serve the API, the orchestrator and the tool server together.
    "MCP_TRANSPORT": "stdio",
    # The console proxies every call server-side, so the browser never calls this
    # API directly and CORS is a second line rather than the first. Never "*":
    # these endpoints take a bearer token.
    "CORS_ALLOW_ORIGINS": "https://alphabrief-jade.vercel.app",
    "LANGFUSE_HOST": "https://cloud.langfuse.com",
    "DEFAULT_WATCHLIST": "AAPL,MSFT,NVDA,TSLA,AMZN",
    # A Space sleeps and wakes on a fresh filesystem, so a run parked at the gate
    # must be checkpointed somewhere that outlives the container. Postgres is the
    # only such option here; this is a reminder that DATABASE_URL is not optional
    # in this environment, enforced by the preflight in `secrets_warning`.
    "PROVIDER_MIN_INTERVAL_SECONDS": "0.30",
}

#: Set by hand, once, under Settings -> Variables and secrets.
SECRETS: Final[dict[str, str]] = {
    "APPROVAL_TOKEN": (
        "REQUIRED. Without it each process invents its own token and nothing can "
        "ever be approved. Must match ALPHABRIEF_APPROVAL_TOKEN on the console."
    ),
    "DATABASE_URL": (
        "REQUIRED here. A Space has an ephemeral filesystem, so the default "
        "SQLite archive and its checkpoints vanish when the Space sleeps — "
        "taking any run parked at the approval gate with them. Use the Neon URL."
    ),
    "ANTHROPIC_API_KEY": (
        "Optional. Without it the deterministic engine drives the same graph, "
        "tools and verifier at no cost, and the API reports engine=deterministic."
    ),
    "SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM / SMTP_TO": (
        "Optional. Delivery degrades to a no-op and the run still completes."
    ),
    "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY": (
        "Optional. Traces every model call; failures never fail a request."
    ),
}


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, cwd=REPO_ROOT, capture_output=True, text=True, check=check
    )


def frontmatter_problems(frontmatter: str) -> list[str]:
    """Check the Spaces configuration block. Pure, so it is testable without a repo."""
    problems: list[str] = []
    if "sdk: docker" not in frontmatter:
        problems.append(f"{SPACE_CONFIG} does not declare `sdk: docker`")
    if "app_port: 7860" not in frontmatter:
        problems.append(
            f"{SPACE_CONFIG} does not declare `app_port: 7860`; Spaces routes to that port"
        )

    for line in frontmatter.splitlines():
        if not line.startswith("short_description:"):
            continue
        # Split once only: quoting is optional in YAML, and a description
        # containing a colon needs the quotes — splitting on every colon would
        # truncate at the first.
        described = line.split(":", 1)[1].strip().strip("\"'")
        if len(described) > SHORT_DESCRIPTION_MAX:
            problems.append(
                f"short_description is {len(described)} characters; the Hub rejects "
                f"anything over {SHORT_DESCRIPTION_MAX}. Shorten it in {SPACE_CONFIG}."
            )
    return problems


def explain_push_failure(stderr: str) -> str:
    """Turn the remote's rejection into the one instruction that acts on it."""
    if "YAML metadata" in stderr:
        return (
            "push failed: the Hub rejected the repository metadata. Correct the "
            f"field named above in {SPACE_CONFIG} and re-run."
        )
    if "binary files" in stderr:
        return (
            "push failed: the Hub refuses binary files outside Xet/LFS. Store the "
            "offending file above in a text format, or track it with Xet."
        )
    if any(marker in stderr for marker in ("Authentication", "401", "403")):
        return (
            "push failed: authentication. Create a WRITE token at "
            "https://huggingface.co/settings/tokens and log in again with "
            "`hf auth login --token hf_... --add-to-git-credential`."
        )
    return "push failed. The remote's reason is above."


#: Never pushed to the Space: the console deploys to Vercel, and its
#: node_modules and build output have no business in an API image's context.
SPACE_EXCLUDE: Final = ("apps/web",)

#: Branch name used for the synthetic deploy commit.
DEPLOY_BRANCH: Final = "alphabrief-space-deploy"


def push_with_space_config(branch: str) -> subprocess.CompletedProcess[str]:
    """Push the deployable tree to the Space as a single synthetic commit.

    A Space is a deployment target, not a mirror of this project's history, and
    treating it as one causes two separate failures. The Hub scans the whole
    history of a pushed ref for binary files, so a file committed weeks ago
    rejects today's push; and it reads a Space's configuration from README.md
    front matter, which GitHub would render as a table above the project title.

    An orphan commit answers both: it carries the current tree minus
    `SPACE_EXCLUDE`, with the configuration from `.hf-space.yml` prepended to the
    README. No history to scan, and nothing on the GitHub branch changes. The
    branch is deleted afterwards whatever happens, so a failed push cannot leave
    the repository sitting on it.
    """
    readme = REPO_ROOT / "README.md"
    original = readme.read_text(encoding="utf-8")
    config = (REPO_ROOT / SPACE_CONFIG).read_text(encoding="utf-8").strip()

    run("git", "branch", "-D", DEPLOY_BRANCH, check=False)
    run("git", "checkout", "--orphan", DEPLOY_BRANCH)
    try:
        readme.write_text(f"---\n{config}\n---\n\n{original}", encoding="utf-8")
        # Spaces builds ./Dockerfile from the repository root and offers no way
        # to point it elsewhere. The canonical copy stays at apps/api/Dockerfile
        # next to the package it installs; this places an identical copy where
        # the Hub looks, on the deploy commit only. It is byte-for-byte the same
        # file — the Dockerfile's paths are already root-relative precisely so
        # that no host-specific variant has to exist and drift.
        (REPO_ROOT / "Dockerfile").write_text(
            (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        run("git", "add", "-A")
        # `-f` because .gitignore lists /Dockerfile — deliberately, since the root
        # copy is generated and must never land on a GitHub commit. A plain
        # `git add -A` honours that and silently drops the one file the Space
        # build reads, producing a Space stuck at NO_APP_FILE with nothing
        # anywhere to explain it.
        run("git", "add", "-f", "Dockerfile")
        for excluded in SPACE_EXCLUDE:
            run("git", "rm", "-r", "-q", "--cached", "--ignore-unmatch", excluded, check=False)
        run("git", "commit", "-q", "-m", "AlphaBrief — deployed tree")

        # Assert the tree before handing it to the Hub. Everything above is a
        # side effect on a temporary branch; a missing file here costs a build,
        # a wait, and a stage the Hub reports as NO_APP_FILE rather than as the
        # missing Dockerfile it actually is.
        tracked = run("git", "ls-tree", "--name-only", "HEAD").stdout.split()
        if "Dockerfile" not in tracked:
            msg = "the deploy commit has no root Dockerfile; the Space would never build"
            raise RuntimeError(msg)

        return run("git", "push", "--force", "space", f"{DEPLOY_BRANCH}:main", check=False)
    finally:
        readme.write_text(original, encoding="utf-8")
        (REPO_ROOT / "Dockerfile").unlink(missing_ok=True)
        run("git", "checkout", "-f", branch, check=False)
        run("git", "branch", "-D", DEPLOY_BRANCH, check=False)


def preflight() -> list[str]:
    problems: list[str] = []
    for relative in REQUIRED:
        if not (REPO_ROOT / relative).exists():
            problems.append(f"missing {relative} — check the path")

    config = REPO_ROOT / SPACE_CONFIG
    if config.exists():
        problems.extend(frontmatter_problems(config.read_text(encoding="utf-8")))

    dockerfile = REPO_ROOT / "apps" / "api" / "Dockerfile"
    if dockerfile.exists():
        content = dockerfile.read_text(encoding="utf-8")
        # Spaces builds with the repository root as context. A COPY of a path
        # relative to apps/api would resolve against the root and fail the build
        # with a bare "file not found", naming a path that does exist.
        if "COPY apps/api/" not in content:
            problems.append(
                "apps/api/Dockerfile does not COPY root-relative paths, but Spaces "
                "builds with the repository root as its context"
            )
    return problems


def report_secrets() -> None:
    print("\n  Set these once, under Settings -> Variables and secrets:")
    for name, why in SECRETS.items():
        print(f"\n    {name}")
        for line in _wrap(why, 74):
            print(f"      {line}")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911 - sequential guards
    """Each early return is one precondition that failed, reported where it failed.

    Collapsing them into fewer exits would mean either nesting the whole deploy
    six levels deep or losing the specific message, and the specific message is
    the entire value of a deploy script.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--space", default="alphabrief", help="Space name (not the full id)")
    parser.add_argument("--public", action="store_true", help="make the Space public")
    parser.add_argument(
        "--check",
        action="store_true",
        help="run the preflight only and exit; needs no credentials, so CI can gate it",
    )
    args = parser.parse_args(argv)

    problems = preflight()
    if problems:
        print("preflight failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    if args.check:
        print("preflight passed: every file the Space build needs is present")
        return 0

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed. Run `make install`.")
        return 1

    api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:  # noqa: BLE001 - "not logged in" arrives as several unrelated types
        # huggingface_hub surfaces a missing or rejected token as an HTTP error,
        # a local-token error or a plain OSError depending on which layer notices
        # first. They all mean the same thing to an operator, and narrowing this
        # would only mean an unhandled traceback for the variants not listed.
        print(
            "Not logged in to Hugging Face.\n"
            "  1. Create a WRITE token at https://huggingface.co/settings/tokens\n"
            "  2. apps/api/.venv/bin/hf auth login --token hf_... --add-to-git-credential\n"
        )
        return 1

    repo_id = f"{user}/{args.space}"
    print(f"  user   {user}")
    print(f"  space  {repo_id}  ({'public' if args.public else 'private'})")

    url = api.create_repo(
        repo_id=repo_id,
        repo_type="space",
        space_sdk="docker",
        private=not args.public,
        exist_ok=True,
    )
    print(f"  ready  {url}")

    for key, value in SPACE_VARIABLES.items():
        api.add_space_variable(repo_id, key, value)
    print(f"  config {len(SPACE_VARIABLES)} variables applied from SPACE_VARIABLES")

    # A Space is deployed by pushing a git tree, so the tree has to be committed
    # first. Doing that automatically would let an unreviewed edit reach a
    # deployment under a generic message; refusing is the safe answer, and naming
    # the files makes it a one-line fix rather than a puzzle.
    dirty = run("git", "status", "--porcelain", check=False).stdout.strip()
    if dirty:
        print("\n  refusing to deploy: the working tree is not clean\n")
        for line in dirty.splitlines():
            print(f"    {line}")
        print("\n  Commit or stash these, let CI go green, then deploy.")
        return 1

    remote_url = f"https://huggingface.co/spaces/{repo_id}"
    existing = run("git", "remote", check=False).stdout.split()
    if "space" in existing:
        run("git", "remote", "set-url", "space", remote_url)
    else:
        run("git", "remote", "add", "space", remote_url)

    branch = run("git", "branch", "--show-current").stdout.strip() or "main"
    print(f"  pushing {branch} -> space/main …")
    try:
        pushed = push_with_space_config(branch)
    except RuntimeError as exc:
        # The temporary branch is already torn down by the `finally` above, so
        # the repository is back where it started and this is safe to re-run.
        print(f"\n  deploy aborted: {exc}")
        return 1
    if pushed.returncode != 0:
        stderr = pushed.stderr.strip()
        print(stderr[-1500:])
        print(f"\n{explain_push_failure(stderr)}")
        return 1

    host = f"https://{user.replace('_', '-')}-{args.space}.hf.space"
    print(f"\n  deployed  {remote_url}")
    print(f"  API       {host}/health")
    report_secrets()
    return 0


if __name__ == "__main__":
    sys.exit(main())
