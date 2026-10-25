import asyncio
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import errors, types
from starlette.websockets import WebSocketState

from assistant_tools import LIVE_TOOLS, respond_to_tool_calls
from call_summary import (
    CallSummary,
    SummaryGenerationError,
    TranscriptEntry,
    generate_call_summary,
)
from orders import get_order_details
from policies import get_policy_details, is_cancellation_eligible

app = FastAPI(title="Aura Skincare Support API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aria-voice-agent-eight.vercel.app"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
LIVE_MODEL_ID = "gemini-3.8-live"
CALL_CONTEXT_TTL_SECONDS = 60 * 60
CALL_CONTEXTS: dict[str, dict[str, Any]] = {}
LIVE_SYSTEM_INSTRUCTION = (
    "You are Aria, a friendly, professional, helpful customer support assistant for "
    "Aura Skincare. Be warm and concise. Do not invent order status, tracking "
    "information, payment status, cancellations, refunds, or replacements. Always "
    "use the provided order lookup tool for order-specific information and configured "
    "policy tools for policy answers. When a customer asks about a specific predefined "
    "mock order or asks for all its details, accurately disclose every field returned "
    "by that order lookup, including customer_name when present. Do not refuse to "
    "provide mock record fields based on an authentication or privacy requirement that "
    "is not implemented. Normalize shorthand like 'order 101' to ORD-101. Treat tool "
    "results as the only source of order facts. If payment status is absent, explicitly "
    "say it is not available in the sample data. Do not invent accounts, "
    "email confirmations, websites, or other ways to check. For return questions, "
    "include the 7-day window, unopened/unused/original-packaging conditions, and the "
    "48-hour photo-reporting rule for damaged or defective products. Explain eligibility "
    "only; do not claim that a cancellation, return, refund, or replacement was processed "
    "or approved. For COD eligibility, use the policy tool: the maximum is inclusive "
    "(₹2,500 is eligible). If asked for all sample orders, use the sample-order tool and "
    "summarize only its returned fields. A general listing does not identify the caller's "
    "order; do not treat any listed order as theirs or select one arbitrarily. If a tool "
    "reports not found or an unspecified policy boundary, explain that clearly. Maintain "
    "the language the "
    "customer explicitly selected. Do not switch languages because of a brief "
    "acknowledgment such as “Sí”; switch only when the customer clearly requests another "
    "language. If Hindi is selected, continue in Hindi until an explicit language change."
)
BACKEND_ENV_FILE = Path(__file__).with_name(".env")
ALLOWED_VOICE_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://aria-voice-agent-eight.vercel.app",
}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/orders/{order_id}")
def read_order(order_id: str) -> dict[str, object]:
    order = get_order_details(order_id)
    if order is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Order not found", "order_id": order_id},
        )

    return {
        **order,
        "cancellation_eligible": is_cancellation_eligible(order),
    }


@app.get("/sample-orders")
def read_sample_orders() -> list[dict[str, object]]:
    sample_orders = []
    for order_id in ("ORD-101", "ORD-102", "ORD-103"):
        order = get_order_details(order_id)
        if order is None:
            continue
        status = str(order["status"])
        delivered_days_ago = order.get("delivered_days_ago")
        if status == "Delivered" and isinstance(delivered_days_ago, int):
            status = f"{status} {delivered_days_ago} days ago"
        sample_orders.append(
            {
                "order_id": order["order_id"],
                "product": order["product"],
                "total_inr": order["amount_inr"],
                "status": status,
            }
        )
    return sample_orders


@app.get("/policies")
def read_policies() -> dict[str, object]:
    return get_policy_details()


def _purge_expired_call_contexts() -> None:
    cutoff = time.monotonic() - CALL_CONTEXT_TTL_SECONDS
    expired = [
        call_id
        for call_id, context in CALL_CONTEXTS.items()
        if context["updated_at"] < cutoff
    ]
    for call_id in expired:
        CALL_CONTEXTS.pop(call_id, None)


@app.post("/calls/{call_id}/summary", response_model=CallSummary)
async def create_call_summary(call_id: str) -> CallSummary:
    _purge_expired_call_contexts()
    context = CALL_CONTEXTS.get(call_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Call transcript is no longer available.")
    if not context.get("finished"):
        raise HTTPException(status_code=409, detail="The voice call has not finished yet.")
    if context.get("summary_in_progress"):
        raise HTTPException(status_code=409, detail="The call summary is already being generated.")
    context["summary_in_progress"] = True

    try:
        summary = await asyncio.to_thread(
            generate_call_summary,
            [TranscriptEntry.model_validate(entry) for entry in context["transcript"]],
            context["tool_results"],
        )
    except SummaryGenerationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        context["summary_in_progress"] = False

    CALL_CONTEXTS.pop(call_id, None)
    return summary


def _merge_transcription_text(existing: str, fragment: str) -> str:
    incoming = fragment
    if not incoming.strip():
        return existing
    if not existing:
        return incoming
    if incoming == existing or existing.endswith(incoming):
        return existing
    if incoming.startswith(existing):
        return incoming
    if existing.startswith(incoming):
        return existing

    overlap_length = min(len(existing), len(incoming))
    while overlap_length > 0:
        if existing.endswith(incoming[:overlap_length]):
            return existing + incoming[overlap_length:]
        overlap_length -= 1

    return existing + incoming


async def _finish_transcript_turn(
    websocket: WebSocket,
    call_context: dict[str, Any],
    role: str,
) -> None:
    turn = call_context["active_turns"].pop(role, None)
    if turn is None or not turn["text"].strip():
        return
    call_context["transcript"].append(
        {
            "role": role,
            "text": turn["text"].strip(),
            "timestamp": turn["timestamp"],
        }
    )
    call_context["updated_at"] = time.monotonic()
    if websocket.client_state is WebSocketState.CONNECTED:
        await websocket.send_json(
            {
                "type": "transcript",
                "turn_id": turn["turn_id"],
                "role": role,
                "text": turn["text"],
                "timestamp": turn["timestamp"],
                "complete": True,
            }
        )


async def _record_transcript_fragment(
    websocket: WebSocket,
    call_context: dict[str, Any],
    role: str,
    fragment: str,
) -> None:
    if not fragment.strip():
        return
    if role == "assistant":
        await _finish_transcript_turn(websocket, call_context, "user")
    else:
        await _finish_transcript_turn(websocket, call_context, "assistant")

    active_turns = call_context["active_turns"]
    turn = active_turns.get(role)
    if turn is None:
        turn = {
            "turn_id": secrets.token_urlsafe(12),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": "",
        }
        active_turns[role] = turn
    turn["text"] = _merge_transcription_text(turn["text"], fragment)
    call_context["updated_at"] = time.monotonic()
    if websocket.client_state is WebSocketState.CONNECTED:
        await websocket.send_json(
            {
                "type": "transcript",
                "turn_id": turn["turn_id"],
                "role": role,
                "text": turn["text"],
                "timestamp": turn["timestamp"],
                "complete": False,
            }
        )


def _remove_customer_names(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_customer_names(item)
            for key, item in value.items()
            if key != "customer_name"
        }
    if isinstance(value, list):
        return [_remove_customer_names(item) for item in value]
    return value


def _safe_api_error(error: errors.APIError, api_key: str) -> dict[str, str]:
    if error.code in (401, 403):
        category = "Authentication or API access error"
    elif error.code == 429:
        category = "Gemini quota or rate-limit error"
    elif error.code in (400, 404):
        category = "Gemini Live model or request error"
    else:
        category = "Gemini Live API error"

    message = (error.message or error.status or "No additional details provided.").replace(
        api_key, "[redacted]"
    )
    return {"type": "error", "message": f"{category}: {message}"}


async def _send_voice_error(websocket: WebSocket, message: str) -> None:
    if websocket.client_state is not WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=1011)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/voice")
async def voice_session(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin not in ALLOWED_VOICE_ORIGINS:
        await websocket.close(code=1008, reason="Origin is not allowed.")
        return

    await websocket.accept()
    api_key = dotenv_values(BACKEND_ENV_FILE).get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        await websocket.send_json(
            {"type": "error", "message": "The backend GEMINI_API_KEY is not configured."}
        )
        await websocket.close(code=1011)
        return

    api_key = api_key.strip()
    client = genai.Client(api_key=api_key)

    try:
        async with client.aio.live.connect(
            model=LIVE_MODEL_ID,
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=LIVE_SYSTEM_INSTRUCTION,
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                tools=LIVE_TOOLS,
            ),
        ) as session:
            _purge_expired_call_contexts()
            call_id = secrets.token_urlsafe(24)
            call_context: dict[str, Any] = {
                "transcript": [],
                "active_turns": {},
                "tool_results": [],
                "updated_at": time.monotonic(),
                "end_requested": False,
                "finished": False,
            }
            CALL_CONTEXTS[call_id] = call_context
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "connected",
                    "model": LIVE_MODEL_ID,
                    "call_id": call_id,
                }
            )

            async def forward_microphone_audio() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return

                    if message.get("text") is not None:
                        try:
                            command = json.loads(message["text"])
                        except json.JSONDecodeError:
                            await websocket.send_json(
                                {"type": "error", "message": "Invalid WebSocket command."}
                            )
                            continue
                        if command.get("type") == "end":
                            call_context["end_requested"] = True
                            return
                        await websocket.send_json(
                            {"type": "error", "message": "Unsupported WebSocket command."}
                        )
                        continue

                    audio = message.get("bytes")
                    if audio is None:
                        continue
                    if not audio or len(audio) % 2:
                        await websocket.send_json(
                            {"type": "error", "message": "Invalid PCM audio packet."}
                        )
                        await websocket.close(code=1003, reason="Invalid PCM audio packet.")
                        return
                    if len(audio) > 64_000:
                        await websocket.send_json(
                            {"type": "error", "message": "Audio packet exceeds the allowed size."}
                        )
                        await websocket.close(code=1009, reason="Audio packet is too large.")
                        return

                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=audio,
                            mime_type="audio/pcm;rate=16000",
                        )
                    )

            async def forward_live_response() -> None:
                while True:
                    async for response in session.receive():
                        if response.tool_call:
                            results = await respond_to_tool_calls(session, response.tool_call)
                            call_context["tool_results"].extend(
                                _remove_customer_names(result) for result in results
                            )
                            call_context["updated_at"] = time.monotonic()
                        content = response.server_content
                        if content is None:
                            continue

                        if content.input_transcription and content.input_transcription.text:
                            await _record_transcript_fragment(
                                websocket,
                                call_context,
                                "user",
                                content.input_transcription.text,
                            )
                        if content.output_transcription and content.output_transcription.text:
                            await _record_transcript_fragment(
                                websocket,
                                call_context,
                                "assistant",
                                content.output_transcription.text,
                            )
                        if content.interrupted:
                            await _finish_transcript_turn(
                                websocket,
                                call_context,
                                "assistant",
                            )
                            await websocket.send_json(
                                {
                                    "type": "status",
                                    "status": "listening",
                                    "turn_complete": True,
                                    "interrupted": True,
                                }
                            )

                        if content.model_turn and not content.interrupted:
                            for part in content.model_turn.parts or []:
                                if part.inline_data and part.inline_data.data:
                                    await websocket.send_json(
                                        {"type": "status", "status": "speaking"}
                                    )
                                    await websocket.send_bytes(part.inline_data.data)

                        if content.turn_complete:
                            await _finish_transcript_turn(
                                websocket,
                                call_context,
                                "assistant",
                            )
                            await _finish_transcript_turn(
                                websocket,
                                call_context,
                                "user",
                            )
                            await websocket.send_json(
                                {
                                    "type": "status",
                                    "status": "listening",
                                    "turn_complete": True,
                                }
                            )

            microphone_task = asyncio.create_task(forward_microphone_audio())
            response_task = asyncio.create_task(forward_live_response())
            try:
                done, _ = await asyncio.wait(
                    {microphone_task, response_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    task_error = task.exception()
                    if task_error is not None:
                        raise task_error
                if response_task in done and not microphone_task.done():
                    await websocket.send_json(
                        {"type": "error", "message": "The Gemini Live session ended."}
                    )
            finally:
                for task in (microphone_task, response_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    microphone_task,
                    response_task,
                    return_exceptions=True,
                )
                await _finish_transcript_turn(websocket, call_context, "user")
                await _finish_transcript_turn(websocket, call_context, "assistant")
                call_context["finished"] = True
                if (
                    call_context["end_requested"]
                    and websocket.client_state is WebSocketState.CONNECTED
                ):
                    await websocket.send_json({"type": "ended", "call_id": call_id})
    except errors.APIError as error:
        message = _safe_api_error(error, api_key)["message"]
        await _send_voice_error(websocket, message)
    except asyncio.TimeoutError:
        await _send_voice_error(
            websocket,
            "Timed out while connecting to Gemini Live.",
        )
    except (httpx.RequestError, OSError):
        await _send_voice_error(
            websocket,
            "Network error connecting to Gemini Live.",
        )
    except websockets.exceptions.WebSocketException:
        await _send_voice_error(
            websocket,
            "The Gemini Live WebSocket connection failed.",
        )
    except ValueError as error:
        await _send_voice_error(websocket, f"Invalid Gemini Live tool call: {error}")
    except WebSocketDisconnect:
        return
