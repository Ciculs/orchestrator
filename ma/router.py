from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class RouterError(RuntimeError):
    pass


class EmptyResponseError(RouterError):
    pass


@dataclass(frozen=True)
class ModelResult:
    model: str
    content: str
    latency_ms: int
    raw: dict
    prompt_chars: int = 0


class NineRouterClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120, attempts: int = 3, budget=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.attempts = attempts
        self.budget = budget

    def call(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        api: str = "chat",
        stream: bool = False,
        on_chunk=None,
        on_thinking=None,
    ) -> ModelResult:
        use_stream = bool(stream or on_chunk or on_thinking)
        if api == "responses":
            endpoint = "/v1/responses"
            payload = {"model": model, "input": prompt, "stream": False}
            if system:
                payload["instructions"] = system
            use_stream = False
        else:
            endpoint = "/v1/chat/completions"
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {"model": model, "messages": messages, "stream": use_stream}
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
        )
        started = time.monotonic()
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    header_ct = ""
                    if hasattr(response, "headers") and response.headers:
                        header_ct = str(response.headers.get("Content-Type") or response.headers.get("content-type") or "").lower()
                    is_sse = "text/event-stream" in header_ct and hasattr(response, "readline")

                    if use_stream and is_sse:
                        chunks: list[str] = []
                        reasoning_chunks: list[str] = []
                        raw: dict = {"choices": [{"message": {"content": ""}}]}
                        while True:
                            line_bytes = response.readline()
                            if not line_bytes:
                                break
                            line_str = line_bytes.decode("utf-8", "replace").strip()
                            if not line_str or not line_str.startswith("data:"):
                                continue
                            data_str = line_str[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data_json = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if "usage" in data_json:
                                raw["usage"] = data_json["usage"]
                            choices = data_json.get("choices")
                            if choices and isinstance(choices, list) and len(choices) > 0:
                                delta = choices[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                                if reasoning:
                                    reasoning_chunks.append(reasoning)
                                    if on_thinking:
                                        on_thinking(reasoning)
                                text = delta.get("content") or delta.get("text") or choices[0].get("text")
                                if text:
                                    chunks.append(text)
                                    if on_chunk:
                                        on_chunk(text)
                        content = "".join(chunks)
                        if not content.strip() and reasoning_chunks:
                            combined_r = "".join(reasoning_chunks)
                            if "diff --git" in combined_r:
                                content = combined_r
                        # If streaming produced empty output, fallback to non-streaming request
                        if not content.strip():
                            try:
                                non_stream_payload = dict(payload)
                                non_stream_payload["stream"] = False
                                req_ns = urllib.request.Request(
                                    self.base_url + endpoint,
                                    data=json.dumps(non_stream_payload).encode("utf-8"),
                                    headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
                                )
                                with urllib.request.urlopen(req_ns, timeout=self.timeout) as resp_ns:
                                    raw_bytes = resp_ns.read()
                                    raw = json.loads(raw_bytes.decode("utf-8", "replace"))
                                    content = self._extract(raw, api)
                                    if content.strip() and on_chunk:
                                        on_chunk(content)
                            except Exception:
                                pass
                        raw["choices"][0]["message"]["content"] = content
                        if reasoning_chunks:
                            raw["choices"][0]["message"]["reasoning_content"] = "".join(reasoning_chunks)
                    else:
                        raw_bytes = response.read()
                        try:
                            raw = json.loads(raw_bytes.decode("utf-8", "replace"))
                        except json.JSONDecodeError as exc:
                            raise RouterError(f"{model}: non-JSON response: {raw_bytes[:300]!r}") from exc
                        content = self._extract(raw, api)
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:1000]
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.attempts:
                    last_error = exc
                    time.sleep(min(2 * attempt, 10))
                    continue
                raise RouterError(f"{model}: HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, ConnectionError, TimeoutError, TimeoutError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    time.sleep(min(2 * attempt, 10))
                    continue
                raise RouterError(
                    f"{model}: failed after {self.attempts} attempts "
                    f"({self.timeout}s each): {type(exc).__name__}: {exc}"
                ) from exc
            except Exception as exc:
                last_error = exc
                if attempt == self.attempts:
                    raise RouterError(
                        f"{model}: failed after {self.attempts} attempts "
                        f"({self.timeout}s each): {type(exc).__name__}: {exc}"
                    ) from exc
        else:
            raise RouterError(f"{model}: failed after {self.attempts} attempts: {last_error}")
        if not content.strip():
            raise EmptyResponseError(f"{model}: HTTP success but empty model output")
        if self.budget is not None:
            self.budget.charge(prompt=system + "\n" + prompt, content=content, model=model)
        return ModelResult(
            model,
            content.strip(),
            int((time.monotonic() - started) * 1000),
            raw,
            prompt_chars=len(system) + len(prompt),
        )

    @staticmethod
    def _extract(raw: dict, api: str) -> str:
        if api == "chat":
            try:
                choice = raw["choices"][0]
                msg = choice.get("message", {})
                value = msg.get("content")
                if not value and msg.get("reasoning_content"):
                    r_val = msg.get("reasoning_content")
                    if "diff --git" in r_val:
                        value = r_val
                if not value:
                    value = choice.get("text") or ""
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            except (KeyError, IndexError, TypeError):
                return ""
        if isinstance(raw.get("output_text"), str):
            return raw["output_text"]
        parts = []
        for item in raw.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    def list_models(self) -> list[dict]:
        return fetch_available_models(self.base_url, self.api_key, timeout=self.timeout)


_MODEL_CACHE: dict = {"ts": 0, "data": []}


def fetch_available_models(base_url: str = "http://127.0.0.1:20128", api_key: str | None = None, timeout: int = 15, force_refresh: bool = False) -> list[dict]:
    global _MODEL_CACHE
    now = time.monotonic()
    if not force_refresh and _MODEL_CACHE["data"] and (now - _MODEL_CACHE["ts"]) < 30:
        return _MODEL_CACHE["data"]

    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        if _MODEL_CACHE["data"]:
            return _MODEL_CACHE["data"]
        raise RouterError(f"Failed to fetch models from 9Router at {url}: {exc}") from exc

    raw_list = data.get("data", [])
    parsed = []
    for item in raw_list:
        model_id = item.get("id", "")
        if not model_id:
            continue
        parts = model_id.split("/", 1)
        provider = parts[0] if len(parts) == 2 else "other"
        name = parts[1] if len(parts) == 2 else model_id
        caps = item.get("capabilities", {}) or {}
        context_len = caps.get("contextWindow") or item.get("context_length") or 0
        max_output = caps.get("maxOutput") or item.get("max_completion_tokens") or 0

        parsed.append({
            "id": model_id,
            "provider": provider,
            "name": name,
            "context_window": context_len,
            "max_output": max_output,
            "capabilities": {
                "vision": bool(caps.get("vision")),
                "tools": bool(caps.get("tools")),
                "reasoning": bool(caps.get("reasoning")),
                "audio": bool(caps.get("audioInput") or caps.get("audioOutput")),
            },
            "owned_by": item.get("owned_by", provider),
        })

    # Sort by provider then model name
    parsed.sort(key=lambda x: (x["provider"], x["name"]))
    _MODEL_CACHE = {"ts": now, "data": parsed}
    return parsed

