"""
Rego parser & validator.

Two responsibilities:
  1. Extract the Rego code block from the LLM's markdown response.
  2. Validate the extracted Rego via the `opa` CLI (`opa check` and `opa eval`).

If the OPA binary is not installed, validation degrades gracefully: the parser
still extracts the code, but `validate()` returns a 'skipped' verdict.
"""

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    SKIPPED = "skipped"  # OPA CLI not installed


@dataclass
class ValidationResult:
    status: ValidationStatus
    rego_code: str
    errors: str = ""
    opa_version: Optional[str] = None


# Regex: matches ```rego ... ``` or ``` ... ``` fences (case-insensitive on lang tag).
# We grab the FIRST fenced block after the "### Policy" heading, falling back to
# any fenced block in the response.
FENCE_RE = re.compile(
    r"```(?:rego)?\s*\n(?P<code>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def extract_rego(llm_response: str) -> str:
    """
    Pull the Rego source out of the LLM's markdown response.

    Strategy:
      1. Find the section after a "### Policy" heading (if present).
      2. Within that section (or the whole response), grab the first fenced block.
      3. Strip leading/trailing whitespace from the code.

    Raises:
        ValueError: if no fenced code block is found.
    """
    # Restrict to the "### Policy" section if the model obeyed the format spec.
    policy_section_match = re.search(
        r"###\s*Policy\s*\n(?P<rest>.*)",
        llm_response,
        re.DOTALL | re.IGNORECASE,
    )
    haystack = policy_section_match.group("rest") if policy_section_match else llm_response

    match = FENCE_RE.search(haystack)
    if not match:
        raise ValueError(
            "No fenced Rego code block found in the LLM response. "
            "Did the model follow the output format?"
        )
    return match.group("code").strip()


def _find_opa_binary(override: Optional[str] = None) -> Optional[str]:
    """Return the path to the `opa` binary, or None if not found."""
    if override:
        if Path(override).is_file():
            return override
        return None
    return shutil.which("opa")


def validate_rego(rego_code: str, opa_binary: Optional[str] = None) -> ValidationResult:
    """
    Validate Rego source via the OPA CLI.

    Runs `opa check` on the code; if that passes, also runs a smoke-test
    `opa eval` against an empty input document to ensure the policy compiles
    and a default evaluation returns without runtime errors.

    Args:
        rego_code: the Rego source to validate.
        opa_binary: optional explicit path to the opa binary.

    Returns:
        ValidationResult: status + extracted code + (if invalid) error text.
    """
    binary = _find_opa_binary(opa_binary)
    if binary is None:
        logger.warning(
            "OPA binary not found on PATH. Skipping CLI validation. "
            "Install OPA: https://www.openpolicyagent.org/docs/latest/#running-opa"
        )
        return ValidationResult(
            status=ValidationStatus.SKIPPED,
            rego_code=rego_code,
            errors="OPA CLI not installed; validation skipped.",
        )

    # Write the code to a temp file so `opa check` can operate on it.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".rego", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(rego_code)
        tmp_path = tmp.name

    try:
        # `opa check <file>` — catches syntax errors, undefined refs, type issues.
        check_proc = subprocess.run(
            [binary, "check", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check_proc.returncode != 0:
            return ValidationResult(
                status=ValidationStatus.INVALID,
                rego_code=rego_code,
                errors=check_proc.stderr.strip() or check_proc.stdout.strip(),
            )

        # Smoke test: `opa eval -d <file> 'data'` with empty input.
        # If the package compiled, this should succeed and print JSON.
        eval_proc = subprocess.run(
            [binary, "eval", "-d", tmp_path, "-i", "/dev/stdin", "data"],
            input="{}",
            capture_output=True,
            text=True,
            timeout=30,
        )
        if eval_proc.returncode != 0:
            return ValidationResult(
                status=ValidationStatus.INVALID,
                rego_code=rego_code,
                errors=eval_proc.stderr.strip() or eval_proc.stdout.strip(),
            )

        # Try to capture the OPA version for diagnostics.
        version_proc = subprocess.run(
            [binary, "version"], capture_output=True, text=True, timeout=10
        )
        opa_version = version_proc.stdout.splitlines()[0] if version_proc.returncode == 0 else None

        return ValidationResult(
            status=ValidationStatus.VALID,
            rego_code=rego_code,
            opa_version=opa_version,
        )

    except subprocess.TimeoutExpired:
        return ValidationResult(
            status=ValidationStatus.INVALID,
            rego_code=rego_code,
            errors="OPA validation timed out (>30s). Policy may have infinite recursion.",
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
