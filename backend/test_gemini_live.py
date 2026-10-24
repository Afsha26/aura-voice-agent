import asyncio
import sys
import wave
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values
from google import genai
from google.genai import errors


MODEL_ID = "gemini-3.8-live"
PROMPT = "Say hello and introduce yourself as Aria, the Aura Skincare support assistant."
ENV_FILE = Path(__file__).with_name(".env")
AUDIO_FILE = Path(__file__).with_name("test_gemini_live.wav")
OUTPUT_SAMPLE_RATE = 24_000


def print_api_error(error: errors.APIError, api_key: str) -> None:
    status_code = error.code
    message = (error.message or error.status or "No additional details provided.").replace(
        api_key, "[redacted]"
    )

    if status_code in (401, 403):
        print(f"Authentication/access error (HTTP {status_code}): {message}")
    elif status_code == 429:
        print(f"Quota or rate-limit error (HTTP {status_code}): {message}")
    elif status_code in (400, 404):
        print(f"Model or Live API request error (HTTP {status_code}): {message}")
    else:
        print(f"Gemini Live API error (HTTP {status_code}): {message}")


async def test_live_api(api_key: str) -> int:
    client = genai.Client(api_key=api_key)
    audio_chunks: list[bytes] = []

    try:
        async with client.aio.live.connect(
            model=MODEL_ID,
            config={"response_modalities": ["AUDIO"]},
        ) as session:
            print(f"Live API connection succeeded with {MODEL_ID}.")
            await session.send_realtime_input(text=PROMPT)

            responses = session.receive()
            deadline = asyncio.get_running_loop().time() + 60
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError

                try:
                    response = await asyncio.wait_for(
                        responses.__anext__(),
                        timeout=remaining,
                    )
                except StopAsyncIteration:
                    break

                content = response.server_content
                if content and content.model_turn:
                    for part in content.model_turn.parts or []:
                        if part.inline_data and part.inline_data.data:
                            audio_chunks.append(part.inline_data.data)

                if content and content.turn_complete:
                    break
    except errors.APIError as error:
        print_api_error(error, api_key)
        return 1
    except asyncio.TimeoutError:
        print("Network/response timeout: no complete Live API response arrived within 60 seconds.")
        return 1
    except (httpx.RequestError, websockets.exceptions.WebSocketException, OSError) as error:
        message = str(error).replace(api_key, "[redacted]")
        print(f"Network/WebSocket error ({type(error).__name__}): {message}")
        return 1

    audio_data = b"".join(audio_chunks)
    print(f"Audio returned: {'yes' if audio_data else 'no'} ({len(audio_data)} bytes).")
    if not audio_data:
        print("The Live session ended without returning audio data.")
        return 1

    if len(audio_data) % 2:
        print("Audio format error: received PCM audio with an incomplete 16-bit sample.")
        return 1

    with wave.open(str(AUDIO_FILE), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(OUTPUT_SAMPLE_RATE)
        wav_file.writeframes(audio_data)

    print(f"Saved playable 24 kHz, 16-bit mono PCM audio to {AUDIO_FILE}.")
    return 0


def main() -> int:
    api_key = dotenv_values(ENV_FILE).get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print(
            f"Missing API key: add GEMINI_API_KEY=your_key to {ENV_FILE}. "
            "The key is read locally and is never printed."
        )
        return 1

    return asyncio.run(test_live_api(api_key.strip()))


if __name__ == "__main__":
    sys.exit(main())
