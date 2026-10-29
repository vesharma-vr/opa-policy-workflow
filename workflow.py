"""
OPA Policy Generation Workflow — Main Orchestrator

Pipeline:
    requirement
        │
        ▼
    build_messages() ──► BedrockClient.invoke()
        │                       │
        │                       ▼
        │                 extract_rego()
        │                       │
        │                       ▼
        │                 validate_rego()  ──► VALID? ──► return PolicyResult
        │                       │
        │                  INVALID
        │                       │
        ▼                       ▼
    build_refinement_messages(feedback) ──► BedrockClient.invoke()  (loop)

The refinement loop is the crucial piece: when `opa check` fails, we feed
the compiler error back to the LLM as a new user message, asking it to fix
the policy. This is prompt-chaining as discussed in the AWS blog post.

Usage:
    from workflow import generate_opa_policy

    result = generate_opa_policy(
        "Deny K8s pods whose image uses the ':latest' tag."
    )
    print(result.rego_code)
    print(result.validation_status)
"""

import json
import logging
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict

from bedrock_client import BedrockClient
from config import BEDROCK_CONFIG, WORKFLOW_CONFIG
from examples import render_fewshot_block
from prompt_builder import build_messages, get_system_prompt
from validator import validate_rego, ValidationResult, ValidationStatus

# Configure root logger so the demo output is readable.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
logger = logging.getLogger("workflow")


@dataclass
class PolicyResult:
    """Final output of the workflow."""

    requirement: str
    rego_code: str
    reasoning: str
    validation_status: ValidationStatus
    validation_errors: str
    iterations: int  # how many Bedrock calls were made (1 = first-shot success)
    opa_version: Optional[str] = None
    saved_path: Optional[str] = None  # if save_to_disk was called

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["validation_status"] = self.validation_status.value
        return d


def _split_reasoning_and_policy(raw_response: str) -> tuple[str, str]:
    """Pull the '### Reasoning' section and the rego code apart."""
    reasoning = ""
    reasoning_match = None
    import re
    reasoning_match = re.search(
        r"###\s*Reasoning\s*\n(?P<text>.*?)(?=###\s*Policy|\Z)",
        raw_response, re.DOTALL | re.IGNORECASE,
    )
    if reasoning_match:
        reasoning = reasoning_match.group("text").strip()
    # The rego code itself is extracted by validator.extract_rego inside the loop.
    return reasoning


def _build_refinement_messages(
    original_messages: List[Dict],
    model_response: str,
    validation_errors: str,
) -> List[Dict]:
    """
    Append the previous assistant turn + a feedback user turn.

    This is the prompt-chaining pattern: the LLM sees the conversation history
    and a new instruction referencing the validation error, so it can fix the
    policy without losing context.
    """
    refined = list(original_messages)  # shallow copy of original user turn
    refined.append({
        "role": "assistant",
        "content": [{"text": model_response}],
    })
    feedback = (
        "The Rego policy you produced failed validation with the following error:\n\n"
        f"```\n{validation_errors}\n```\n\n"
        "Please fix the issue and produce a corrected version. "
        "Keep the same package name and intent. Respond again with "
        "'### Reasoning' followed by a single fenced ```rego block."
    )
    refined.append({
        "role": "user",
        "content": [{"text": feedback}],
    })
    return refined


def generate_opa_policy(
    requirement: str,
    *,
    save_dir: Optional[str] = None,
    bedrock_client: Optional[BedrockClient] = None,
) -> PolicyResult:
    """
    Run the full generate → validate → refine loop for a single policy requirement.

    Args:
        requirement: natural-language description of the policy.
        save_dir: if provided, save the final .rego file in this directory.
        bedrock_client: inject a custom client (e.g. for testing).

    Returns:
        PolicyResult with the final rego code, validation status, and metadata.
    """
    client = bedrock_client or BedrockClient()
    system_prompt = get_system_prompt()

    messages = build_messages(requirement)
    iterations = 0
    last_validation: Optional[ValidationResult] = None
    last_raw_response = ""

    for attempt in range(1, WORKFLOW_CONFIG.max_refinement_iterations + 1):
        iterations = attempt
        logger.info("Attempt %d/%d: invoking Bedrock (%s)",
                    attempt, WORKFLOW_CONFIG.max_refinement_iterations,
                    BEDROCK_CONFIG.model_id)

        if WORKFLOW_CONFIG.log_prompts:
            # Log a trimmed view of the user message for debugging.
            preview = json.dumps(messages, ensure_ascii=False)[:1200]
            logger.debug("Prompt preview: %s...", preview)

        raw_response = client.invoke(messages, system_prompt)
        last_raw_response = raw_response

        if WORKFLOW_CONFIG.log_raw_responses:
            logger.debug("Raw response (first 1500 chars): %s", raw_response[:1500])

        try:
            from validator import extract_rego
            rego_code = extract_rego(raw_response)
        except ValueError as e:
            logger.error("Could not extract Rego from response: %s", e)
            # Treat extraction failure as a validation failure we can feed back.
            last_validation = ValidationResult(
                status=ValidationStatus.INVALID,
                rego_code="",
                errors=f"Could not find a fenced ```rego block in your response: {e}",
            )
            messages = _build_refinement_messages(
                messages, raw_response, str(e),
            )
            continue

        validation = validate_rego(
            rego_code, opa_binary=WORKFLOW_CONFIG.opa_binary_path
            if WORKFLOW_CONFIG.validate_with_opa_cli else None,
        ) if WORKFLOW_CONFIG.validate_with_opa_cli else ValidationResult(
            status=ValidationStatus.SKIPPED,
            rego_code=rego_code,
            errors="Validation disabled in config.",
        )
        last_validation = validation

        logger.info("Attempt %d validation: %s", attempt, validation.status.value)

        if validation.status in (ValidationStatus.VALID, ValidationStatus.SKIPPED):
            break

        # Invalid: feed the error back for another round.
        messages = _build_refinement_messages(
            messages, raw_response, validation.errors,
        )

    # At this point we either succeeded or exhausted retries.
    reasoning = _split_reasoning_and_policy(last_raw_response)

    result = PolicyResult(
        requirement=requirement,
        rego_code=last_validation.rego_code if last_validation else "",
        reasoning=reasoning,
        validation_status=last_validation.status if last_validation else ValidationStatus.INVALID,
        validation_errors=last_validation.errors if last_validation else "No response produced.",
        iterations=iterations,
        opa_version=last_validation.opa_version if last_validation else None,
    )

    if save_dir and result.rego_code:
        result.saved_path = _save_policy(result, save_dir)

    _log_summary(result)
    return result


def _save_policy(result: PolicyResult, save_dir: str) -> str:
    """Write the final .rego file to disk. Returns the absolute path."""
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Derive a filename from the package declaration, else fall back to a hash.
    import hashlib
    import re
    pkg_match = re.search(r"^\s*package\s+([\w\.]+)", result.rego_code, re.MULTILINE)
    if pkg_match:
        pkg_slug = pkg_match.group(1).replace(".", "_")
        filename = f"{pkg_slug}.rego"
    else:
        h = hashlib.sha1(result.rego_code.encode()).hexdigest()[:8]
        filename = f"policy_{h}.rego"

    path = out_dir / filename
    path.write_text(result.rego_code, encoding="utf-8")
    logger.info("Saved policy to %s", path)
    return str(path)


def _log_summary(result: PolicyResult):
    logger.info("=" * 60)
    logger.info("WORKFLOW SUMMARY")
    logger.info("=" * 60)
    logger.info("Requirement:        %s", result.requirement[:100])
    logger.info("Iterations:         %d", result.iterations)
    logger.info("Validation status:  %s", result.validation_status.value)
    if result.validation_status == ValidationStatus.INVALID:
        logger.error("Validation errors:\n%s", result.validation_errors)
    if result.saved_path:
        logger.info("Saved to:           %s", result.saved_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    # Quick CLI: python workflow.py "your requirement"
    if len(sys.argv) < 2:
        print('Usage: python workflow.py "your policy requirement"')
        sys.exit(1)
    requirement = " ".join(sys.argv[1:])
    res = generate_opa_policy(requirement, save_dir="./policies")
    print(res.rego_code)
