import json

from google import genai
from google.genai import types


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(
        self, api_key: str, model: str = "gemini-2.5-flash-lite", timeout: int = 30
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._config = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

    async def call(self, prompt: str) -> dict:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=self._config,
            )
        except Exception as e:
            raise GeminiError(str(e)) from e
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            raise GeminiError(f"Invalid JSON from Gemini: {response.text[:200]}") from e
