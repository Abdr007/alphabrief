"""The Hugging Face Space deploy configuration.

A deploy variable is code that only ever executes on the platform, so a mistake
in one is invisible until the Space refuses to boot — three minutes after the
push, with a message that names the field but not the file. Everything checkable
without credentials is checked here instead.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_deploy_script() -> ModuleType:
    """Import the script by path: `scripts/` is a directory of CLIs, not a package."""
    path = REPO_ROOT / "scripts" / "deploy_space.py"
    spec = importlib.util.spec_from_file_location("deploy_space", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy_script()


class TestSpaceVariables:
    def test_they_build_a_real_settings_object(self) -> None:
        """Every deployed variable must be one `Settings` actually accepts.

        `ENVIRONMENT: prod` instead of `production` is the archetype: pydantic
        rejects it at import, so the container dies before serving a request and
        the platform reports it as a crashed build.
        """
        settings = Settings(**{key.lower(): value for key, value in deploy.SPACE_VARIABLES.items()})

        assert settings.environment == "production"
        assert settings.mcp_transport == "stdio"

    def test_production_is_declared(self) -> None:
        """On `local` the API mints an approval token into a file for the console.

        A Space shares no filesystem with the console, so that handshake cannot
        work there — and trusting a file would let anything able to write to the
        image supply the credential. `production` disables it and requires an
        explicit APPROVAL_TOKEN.
        """
        assert deploy.SPACE_VARIABLES["ENVIRONMENT"] == "production"

    def test_cors_is_never_a_wildcard(self) -> None:
        """These endpoints take a bearer token; an open origin list undoes that."""
        origins = deploy.SPACE_VARIABLES["CORS_ALLOW_ORIGINS"]
        assert "*" not in origins
        assert origins.startswith("https://")

    def test_no_secret_is_committed_as_a_variable(self) -> None:
        """Space variables are public. The day one holds a password, so is the password."""
        secretish = ("KEY", "TOKEN", "PASSWORD", "SECRET", "DATABASE_URL")
        leaked = [name for name in deploy.SPACE_VARIABLES if any(s in name for s in secretish)]
        assert leaked == []


class TestFrontmatter:
    def test_the_committed_config_passes_its_own_check(self) -> None:
        config = (REPO_ROOT / ".hf-space.yml").read_text(encoding="utf-8")
        assert deploy.frontmatter_problems(config) == []

    def test_a_missing_sdk_is_caught(self) -> None:
        problems = deploy.frontmatter_problems("title: AlphaBrief\napp_port: 7860\n")
        assert any("sdk: docker" in problem for problem in problems)

    def test_a_wrong_port_is_caught(self) -> None:
        """Spaces routes to the declared port; a mismatch is a silent 504."""
        problems = deploy.frontmatter_problems("sdk: docker\napp_port: 8000\n")
        assert any("app_port" in problem for problem in problems)

    def test_an_over_long_description_is_caught_before_the_upload(self) -> None:
        """The Hub enforces this in a pre-receive hook — after the whole push."""
        long = "x" * (deploy.SHORT_DESCRIPTION_MAX + 1)
        problems = deploy.frontmatter_problems(
            f'sdk: docker\napp_port: 7860\nshort_description: "{long}"\n'
        )
        assert any("short_description" in problem for problem in problems)

    def test_a_description_containing_a_colon_is_measured_whole(self) -> None:
        """Splitting on every colon would truncate at the first and under-measure."""
        problems = deploy.frontmatter_problems(
            'sdk: docker\napp_port: 7860\nshort_description: "a: b"\n'
        )
        assert problems == []


class TestPreflight:
    def test_the_repository_as_committed_is_deployable(self) -> None:
        assert deploy.preflight() == []

    def test_the_dockerfile_builds_from_the_repository_root(self) -> None:
        """Spaces builds ./Dockerfile with the repo root as context, not apps/api.

        A COPY of a path relative to apps/api resolves against the root and fails
        with a bare "file not found" naming a path that does exist.
        """
        content = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
        assert "COPY apps/api/" in content

    def test_the_generated_root_dockerfile_is_gitignored(self) -> None:
        """It is written onto the deploy commit only; on main it would be a duplicate."""
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "/Dockerfile" in ignored


class TestPushFailureAdvice:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            ("remote: YAML metadata is invalid", ".hf-space.yml"),
            ("remote: refusing binary files", "Xet"),
            ("fatal: Authentication failed", "WRITE token"),
        ],
    )
    def test_the_remote_reason_selects_the_advice(self, stderr: str, expected: str) -> None:
        """Guessing sends you to the tokens page while the real fault is elsewhere."""
        assert expected in deploy.explain_push_failure(stderr)

    def test_an_unrecognised_failure_does_not_invent_a_cause(self) -> None:
        advice = deploy.explain_push_failure("remote: something entirely new")
        assert "above" in advice
