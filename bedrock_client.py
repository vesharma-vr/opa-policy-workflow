"""
Bedrock client wrapper for the OPA policy generator.

Wraps boto3's bedrock-runtime Converse API. Provides:
  - config-driven model selection
  - consistent error handling
  - a single `invoke()` method that returns the raw text response

Usage:
    from bedrock_client import BedrockClient
    client = BedrockClient()
    response_text = client.invoke(user_messages, system_prompt)
"""

import json
import logging
from typing import List, Dict, Any

import boto3
from botocore.exceptions import ClientError, BotoCoreError

from config import BedrockConfig, BEDROCK_CONFIG


logger = logging.getLogger(__name__)


class BedrockClient:
    """Thin wrapper over boto3 bedrock-runtime Converse API."""

    def __init__(self, config: BedrockConfig = BEDROCK_CONFIG):
        self.config = config
        session_kwargs = {"region_name": config.region_name}
        if config.profile_name:
            session_kwargs["profile_name"] = config.profile_name
        session = boto3.Session(**session_kwargs)
        self.client = session.client("bedrock-runtime")
        logger.debug(
            "Initialized Bedrock client: region=%s model=%s",
            config.region_name, config.model_id,
        )

    def invoke(self, messages: List[Dict[str, Any]], system_prompt: str) -> str:
        """
        Send a Converse API request and return the assistant's text response.

        Args:
            messages: list of {role, content} dicts in Converse API format.
            system_prompt: the system prompt text.

        Returns:
            str: the assistant's text response.

        Raises:
            RuntimeError: if the Bedrock API call fails.
        """
        payload = {
            "modelId": self.config.model_id,
            "messages": messages,
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "maxTokens": self.config.max_tokens,
            },
        }
        logger.debug("Converse payload: %s", json.dumps(payload, indent=2)[:2000])

        try:
            response = self.client.converse(**payload)
        except ClientError as e:
            logger.error("Bedrock ClientError: %s", e)
            raise RuntimeError(f"Bedrock API call failed: {e}") from e
        except BotoCoreError as e:
            logger.error("Bedrock BotoCoreError: %s", e)
            raise RuntimeError(f"Bedrock connection error: {e}") from e

        return self._extract_text(response)

    @staticmethod
    def _extract_text(response: Dict[str, Any]) -> str:
        """Pull the assistant's text out of the Converse response."""
        try:
            output = response["output"]["message"]["content"]
            return "".join(block.get("text", "") for block in output if "text" in block)
        except (KeyError, IndexError) as e:
            logger.error("Unexpected Bedrock response shape: %s", response)
            raise RuntimeError(f"Could not parse Bedrock response: {e}") from e

    def list_available_models(self) -> List[str]:
        """Convenience: list model IDs available in this region."""
        client = boto3.client("bedrock", region_name=self.config.region_name)
        try:
            summaries = client.list_foundation_models()["modelSummaries"]
            return [m["modelId"] for m in summaries]
        except (ClientError, BotoCoreError) as e:
            logger.warning("Could not list models: %s", e)
            return []
