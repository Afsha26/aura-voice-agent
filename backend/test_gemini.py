import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values
from google import genai
from google.genai import errors


MODEL_ID = "gemini-3.5-flash-lite"
LIGHTWEIGHT_ALTERNATIVES = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
PROMPT = "Reply with HELLO. This is a connectivity test for the Aura Skincare AI Voice Agent."
ENV_FILE = Path(__file__).with_name(".env")


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
        print(f"Unsupported model or invalid request (HTTP {status_code}): {message}")
    else:
        print(f"Gemini API error (HTTP {status_code}): {message}")


def main() -> int:
    api_key = dotenv_values(ENV_FILE).get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print(
            f"Missing API key: add GEMINI_API_KEY=your_key to {ENV_FILE}. "
            "The key is read locally and is never printed."
        )
        return 1

    api_key = api_key.strip()
    client = genai.Client(api_key=api_key)

    try:
        available_models = {
            model.name.removeprefix("models/"): model.supported_actions or []
            for model in client.models.list()
            if model.name
        }
    except errors.APIError as error:
        print_api_error(error, api_key)
        return 1
    except httpx.RequestError:
        print("Network error while checking available Gemini models. Check your connection and try again.")
        return 1

    if MODEL_ID not in available_models or "generateContent" not in available_models[MODEL_ID]:
        print(f"Unsupported model: {MODEL_ID} is not available for text generation with this API key.")
        alternative = next(
            (
                model_id
                for model_id in LIGHTWEIGHT_ALTERNATIVES
                if "generateContent" in available_models.get(model_id, [])
            ),
            None,
        )
        if alternative:
            print(f"Closest lightweight text model available to this API key: {alternative}.")
        else:
            print("Closest currently documented lightweight text model: gemini-3.5-flash-lite.")
            print("Its availability through this API key was not confirmed.")
        print("No fallback model was selected. Update MODEL_ID only after you approve a different model.")
        return 1

    print(f"Testing model: {MODEL_ID}")
    try:
        response = client.models.generate_content(model=MODEL_ID, contents=PROMPT)
    except errors.APIError as error:
        print_api_error(error, api_key)
        return 1
    except httpx.RequestError:
        print("Network error while sending the test prompt. Check your connection and try again.")
        return 1

    print("Connection succeeded. Model response:")
    print(response.text or "(The model returned no text.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
