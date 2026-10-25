import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orders import get_order_details


SUMMARY_MODEL_ID = "gemini-3.5-flash-lite"
SUMMARY_ENV_FILE = Path(__file__).with_name(".env")
ORDER_ID_PATTERN = re.compile(r"\bORD-\d+\b", re.IGNORECASE)


def _get_gemini_api_key() -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        api_key = dotenv_values(SUMMARY_ENV_FILE).get("GEMINI_API_KEY")
    return api_key.strip() if api_key and api_key.strip() else None


class TranscriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=10_000)
    timestamp: datetime


class CallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_intent: str = Field(min_length=1, max_length=500)
    order_id: str | None
    resolution_status: Literal["resolved", "partially_resolved", "unresolved"]
    call_summary: str = Field(min_length=1, max_length=2_000)


class SummaryGenerationError(Exception):
    """Raised when Gemini cannot return a valid structured call summary."""


def summary_response_json_schema() -> dict[str, object]:
    """Build the supported Gemini JSON schema while Pydantic validates full output."""
    schema = CallSummary.model_json_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    resolution_schema = properties["resolution_status"]
    assert isinstance(resolution_schema, dict)

    supported_schema: dict[str, object] = {
        "type": "OBJECT",
        "properties": {
            "customer_intent": {"type": "STRING"},
            "order_id": {"type": "STRING", "nullable": True},
            "resolution_status": {
                "type": "STRING",
                "enum": resolution_schema["enum"],
            },
            "call_summary": {"type": "STRING"},
        },
        "required": schema["required"],
    }
    return supported_schema


def _verified_order_ids(
    transcript: list[TranscriptEntry],
    tool_results: list[dict[str, object]],
) -> list[str]:
    verified: list[str] = []

    def add_order_ids(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "order_id" and isinstance(item, str):
                    normalized_id = item.upper()
                    if (
                        normalized_id not in verified
                        and get_order_details(normalized_id) is not None
                    ):
                        verified.append(normalized_id)
                else:
                    add_order_ids(item)
        elif isinstance(value, list):
            for item in value:
                add_order_ids(item)

    for entry in transcript:
        for match in ORDER_ID_PATTERN.findall(entry.text):
            order_id = match.upper()
            if order_id not in verified and get_order_details(order_id) is not None:
                verified.append(order_id)

    order_specific_tools = {
        "get_order_details",
        "check_payment_status",
        "check_cancellation_eligibility",
        "check_return_eligibility",
    }
    for tool_result in tool_results:
        if tool_result.get("name") in order_specific_tools:
            add_order_ids(tool_result.get("result"))
    return verified


def _summary_prompt(
    transcript: list[TranscriptEntry],
    tool_results: list[dict[str, object]],
    verified_order_ids: list[str],
) -> str:
    transcript_data = [entry.model_dump(mode="json") for entry in transcript]
    return (
        "Create a concise factual JSON summary of this Aura Skincare support call. "
        "Return only the requested JSON object. Do not invent facts. Use only the "
        "transcript and backend tool results below. The order_id must be one of "
        f"{verified_order_ids!r}, or null if none applies. For resolution_status, "
        "use resolved only when the customer's main request was actually addressed; "
        "use partially_resolved when useful information was given but the request "
        "remains incomplete; use unresolved when it was not addressed. Eligibility "
        "checks are not actions. Never state that an order was cancelled, refunded, "
        "or replaced unless the tool results explicitly confirm that action occurred. "
        "The JSON fields are customer_intent (string), order_id (string or null), "
        "resolution_status (resolved, partially_resolved, or unresolved), and "
        "call_summary (string).\n\n"
        f"Transcript: {json.dumps(transcript_data, ensure_ascii=False)}\n"
        f"Backend tool results: {json.dumps(tool_results, ensure_ascii=False)}"
    )


def _generate_summary_text(api_key: str, prompt: str) -> str:
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=SUMMARY_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=summary_response_json_schema(),
                temperature=0,
            ),
        )
    except errors.APIError as error:
        if error.code in (401, 403):
            category = "Gemini authentication or API access error"
        elif error.code == 429:
            category = "Gemini quota or rate-limit error"
        else:
            category = "Gemini summary generation error"
        detail = (error.message or error.status or "No additional details.").replace(
            api_key, "[redacted]"
        )
        raise SummaryGenerationError(f"{category}: {detail}") from error
    except (httpx.RequestError, OSError) as error:
        raise SummaryGenerationError(
            "Network error while generating the call summary."
        ) from error

    if not response.text:
        raise SummaryGenerationError("Gemini returned an empty call summary.")
    return response.text


def generate_call_summary(
    transcript: list[TranscriptEntry],
    tool_results: list[dict[str, object]],
) -> CallSummary:
    if not transcript:
        return CallSummary(
            customer_intent="No customer request was recorded.",
            order_id=None,
            resolution_status="unresolved",
            call_summary="No conversational transcript was captured during the call.",
        )

    api_key = _get_gemini_api_key()
    if not api_key:
        raise SummaryGenerationError(
            "Call summary is unavailable because the backend GEMINI_API_KEY is not configured."
        )

    verified_order_ids = _verified_order_ids(transcript, tool_results)
    prompt = _summary_prompt(transcript, tool_results, verified_order_ids)
    try:
        summary = CallSummary.model_validate_json(
            _generate_summary_text(api_key, prompt)
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise SummaryGenerationError(
            "Gemini did not return a valid call summary in the required JSON schema."
        ) from error

    if summary.order_id not in verified_order_ids:
        summary = summary.model_copy(
            update={"order_id": verified_order_ids[0] if verified_order_ids else None}
        )
    return summary
