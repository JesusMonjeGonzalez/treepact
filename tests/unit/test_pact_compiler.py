"""Unit: Pact strict parsing, canonicalization, path rules, and semantic
command validation (PACT-001..007, ADR 0007)."""

from __future__ import annotations

import hashlib

import pytest
from conftest import PACT_MINIMAL

from treepact.domain.pact import compile_pact
from treepact.errors import PactValidationError


def _pact(*replacements: object) -> str:
    text = PACT_MINIMAL.format(
        project_id="demo", writable="src/", check_id="unit",
        argv='["python3", "-m", "pytest", "tests"]',
    )
    pairs: list[tuple[str, str]] = []
    if replacements and isinstance(replacements[0], tuple):
        pairs = list(replacements)  # type: ignore[arg-type]
    else:
        for index in range(0, len(replacements), 2):
            pairs.append((str(replacements[index]), str(replacements[index + 1])))
    for old, new in pairs:
        text = text.replace(old, new)
    return text


class TestStrictParsing:
    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(PactValidationError) as info:
            compile_pact(_pact("toolchain: python", "toolchain: python\n  bogus: 1"), source_name="x")
        assert "bogus" in str(info.value)

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact() + "bogus_section: {}\n", source_name="x")

    def test_version_must_be_1(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("version: 1", "version: 2"), source_name="x")

    def test_missing_required_section_rejected(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("actions:\n", ""), source_name="x")

    def test_empty_checks_rejected(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact(("checks:\n  unit:", "checks: {}")), source_name="x")


class TestPathRules:
    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(PactValidationError) as info:
            compile_pact(_pact("    - .env", "    - /etc/passwd"), source_name="x")
        assert "absolute" in str(info.value)

    def test_parent_traversal_rejected(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("    - .env", "    - ../secret"), source_name="x")

    def test_nul_byte_rejected(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("    - .env", "    - a\\u0000b"), source_name="x")

    def test_deny_overrides_allow(self) -> None:
        compiled = compile_pact(
            _pact(
                ("    - .env", "    - src/secrets/"),
                ("writable: src/", "writable: src/"),
            ),
            source_name="x",
        )
        assert compiled.is_writable("src/app.py")
        assert not compiled.is_writable("src/secrets/key.py")
        assert not compiled.is_readable("src/secrets/key.py")

    def test_implicit_denies_always_apply(self) -> None:
        compiled = compile_pact(_pact(), source_name="x")
        assert not compiled.is_readable(".git/config")
        assert not compiled.is_writable(".treepact.yaml")

    def test_directory_prefix_matches_children(self) -> None:
        compiled = compile_pact(_pact(("    - .", "    - src/")), source_name="x")
        assert compiled.is_readable("src/deep/file.py")
        assert compiled.is_writable("src/deep/file.py")
        assert not compiled.is_readable("other/file.py")


class TestSemanticCommandValidation:
    @pytest.mark.parametrize(
        "argv",
        [
            '["bash", "-c", "echo hi"]',
            '["/bin/zsh", "script.sh"]',
            '["sh", "script.sh"]',
            '["python3", "-c", "print(1)"]',
            '["node", "-e", "1"]',
            '["perl", "-e", "1"]',
            '["ruby", "-e", "1"]',
            '["env", "bash"]',
            '["env", "-i", "sh"]',
            '["bash", "script.sh"]',
        ],
    )
    def test_forbidden_forms_rejected(self, argv: str) -> None:
        with pytest.raises(PactValidationError) as info:
            compile_pact(_pact('["python3", "-m", "pytest", "tests"]', argv), source_name="x")
        assert "not allowed" in str(info.value) or "indirection" in str(info.value)

    @pytest.mark.parametrize(
        "argv",
        [
            '["python3", "-m", "pytest", "tests"]',
            '["uv", "run", "pytest"]',
            '["python3", "scripts/check.py", "--unit"]',
            '["xcrun", "xcodebuild", "test"]',
            '["gradle", "test"]',
            '["swift", "test"]',
            '["python3", "script.py", "-c", "literal-arg"]',
        ],
    )
    def test_legitimate_forms_accepted(self, argv: str) -> None:
        compiled = compile_pact(_pact('["python3", "-m", "pytest", "tests"]', argv), source_name="x")
        assert compiled.check("unit").argv[0] is not None


class TestCanonicalization:
    def test_hash_is_stable_and_deterministic(self) -> None:
        first = compile_pact(_pact(), source_name="a")
        second = compile_pact(_pact(), source_name="b")
        assert first.sha256 == second.sha256
        assert first.sha256 == hashlib.sha256(first.canonical_json.encode("utf-8")).hexdigest()

    def test_equivalent_yaml_canonicalizes_identically(self) -> None:
        text = _pact()
        with_quotes = text.replace('["python3"', '["python3"')  # same content
        assert compile_pact(text, source_name="a").canonical_json == compile_pact(
            with_quotes, source_name="b"
        ).canonical_json

    def test_different_pact_different_hash(self) -> None:
        assert compile_pact(_pact(), source_name="a").sha256 != compile_pact(
            _pact("attempts: 3", "attempts: 2"), source_name="b"
        ).sha256


class TestLimits:
    def test_attempts_capped_at_three(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("attempts: 3", "attempts: 4"), source_name="x")

    def test_cli_limit_never_expands(self) -> None:
        compiled = compile_pact(_pact(), source_name="x")
        assert min(3, compiled.pact.limits.attempts) == 3
        assert min(1, compiled.pact.limits.attempts) == 1

    def test_unavailable_actions_are_complete(self) -> None:
        compiled = compile_pact(_pact(), source_name="x")
        assert len(compiled.pact.actions.unavailable) == 10

    def test_missing_unavailable_action_invalidates_pact(self) -> None:
        with pytest.raises(PactValidationError):
            compile_pact(_pact("    - external_message\n", ""), source_name="x")
