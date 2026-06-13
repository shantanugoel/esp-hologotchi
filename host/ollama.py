from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request

from .prompt import build_situation_prompt, load_personality_prompt, load_pet_name
from .protocol import (
    BehaviorCommand,
    BehaviorProposal,
    ValidationError,
    parse_behavior_proposal,
    parse_behavior_response,
)


class OllamaError(RuntimeError):
    """Raised when the Ollama request itself fails."""


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model_family: str = "qwen3.5"
    model_preset: str = "qwen3.5:4b"
    timeout_seconds: float = 30.0
    keep_alive: str = "30m"
    think: bool = False
    num_predict: int = 96


def build_generate_request(user_prompt: str, config: OllamaConfig) -> dict[str, object]:
    pet_name = load_pet_name()
    payload = {
        "model": config.model_preset,
        "system": load_personality_prompt(),
        "prompt": build_situation_prompt(user_prompt, pet_name=pet_name),
        "stream": False,
        "format": "json",
        "think": config.think,
        "options": {"num_predict": config.num_predict},
    }
    if config.keep_alive:
        payload["keep_alive"] = config.keep_alive
    return payload


def generate_behavior(user_prompt: str, config: OllamaConfig) -> BehaviorCommand:
    model_text = _request_model_text(user_prompt, config)
    try:
        return parse_behavior_response(model_text)
    except ValidationError as exc:
        raise OllamaError(f"Ollama returned invalid behavior JSON: {exc}") from exc


def generate_proposal(user_prompt: str, config: OllamaConfig) -> BehaviorProposal:
    """Like ``generate_behavior`` but keeps optional host-only ``intent``/``body_state``.

    The device still only ever receives the stripped behavior frame
    (``BehaviorProposal.to_behavior_command``); the extra fields drive host-side
    body continuity.
    """

    model_text = _request_model_text(user_prompt, config)
    try:
        return parse_behavior_proposal(model_text)
    except ValidationError as exc:
        raise OllamaError(f"Ollama returned invalid behavior JSON: {exc}") from exc


def _request_model_text(user_prompt: str, config: OllamaConfig) -> str:
    payload = build_generate_request(user_prompt, config)
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=f"{config.base_url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            raw_response = response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip()
        suffix = f": {details}" if details else ""
        raise OllamaError(
            f"Ollama request failed at {config.base_url}: HTTP {exc.code} {exc.reason}{suffix}"
        ) from exc
    except error.URLError as exc:
        raise OllamaError(f"failed to reach Ollama at {config.base_url}: {exc}") from exc
    except TimeoutError as exc:
        raise OllamaError(
            f"Ollama request timed out at {config.base_url} after "
            f"{config.timeout_seconds:g}s"
        ) from exc

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama returned invalid JSON") from exc

    model_text = parsed.get("response")
    if not isinstance(model_text, str):
        raise OllamaError("Ollama response did not contain a string 'response' field")
    return model_text
