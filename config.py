"""
OPA Policy Generation Workflow - Configuration
================================================
Centralized configuration for the Bedrock-backed OPA policy generator.
Tweak model IDs, regions, retry counts, and OPA binary paths here.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BedrockConfig:
    """AWS Bedrock connection & model settings."""

    region_name: str = "us-east-1"

    # Claude Sonnet 5 via the US cross-region inference profile. Our org's SCP
    # blocks global.* profiles and newer models (Sonnet 5.5, Opus 4.8+), so
    # check access before swapping. Allowed alternatives:
    # "us.anthropic.claude-sonnet-4-6" or, for cost-sensitive workloads,
    # "us.anthropic.claude-haiku-4-5-20251001-v1:0".
    model_id: str = "us.anthropic.claude-sonnet-5"

    # No temperature / top_p: Claude Sonnet 5 rejects sampling parameters.
    max_tokens: int = 4096

    # AWS credentials are pulled from the standard chain:
    # env vars (~/.aws/credentials) -> IAM role -> etc.
    # Override here only if you need profile-based auth.
    profile_name: Optional[str] = None


@dataclass(frozen=True)
class WorkflowConfig:
    """Workflow behavior settings."""

    # Maximum number of refinement attempts after validation failure.
    # 0 = generate once, no retry. 3 is a good default.
    max_refinement_iterations: int = 3

    # If True, run `opa check` after generation. Requires the OPA binary
    # on PATH (https://www.openpolicyagent.org/docs/latest/#running-opa).
    validate_with_opa_cli: bool = True

    # Path to the opa binary. If None, we rely on PATH lookup.
    opa_binary_path: Optional[str] = None

    # If True, the full prompt sent to Bedrock is logged. Useful for debugging.
    log_prompts: bool = True

    # If True, the model's raw response is logged (including markdown fences).
    log_raw_responses: bool = True


# Default singleton instances
BEDROCK_CONFIG = BedrockConfig()
WORKFLOW_CONFIG = WorkflowConfig()
