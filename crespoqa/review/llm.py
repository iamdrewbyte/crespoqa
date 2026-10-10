"""Minimal OpenAI-compatible chat client (Ollama, llama.cpp server, vLLM) using only the stdlib."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlparse


class ReviewError(Exception):
    """User-presentable failure of the review (never contains report text)."""


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def assert_local(base_url: str, allow_remote: bool) -> None:
    host = urlparse(base_url).hostname
    if not host:
        raise ReviewError("AI_BASE_URL is not a valid URL.")
    if allow_remote:
        return
    try:
        addrs = {ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)}
    except (socket.gaierror, ValueError):
        raise ReviewError("The AI endpoint host could not be resolved.") from None
    if not all(a.is_loopback or a.is_private for a in addrs):
        raise ReviewError("AI_BASE_URL points outside this machine/network. Report text must stay local; "
                          "set AI_ALLOW_REMOTE=true only if you accept that.")


_THINK = re.compile(r"<think>.*?</think>", re.S)


def clean_output(text: str) -> str:
    return _THINK.sub("", text).strip()


class ChatClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 120, allow_remote: bool = False):
        assert_local(base_url, allow_remote)
        self.url, self.model, self.key, self.timeout = base_url.rstrip("/") + "/chat/completions", model, api_key, timeout

    def _post(self, payload: dict) -> str:
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.key}"} if self.key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"] or ""

    def complete(self, system: str, user: str) -> str:
        payload = {"model": self.model, "temperature": 0, "stream": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "response_format": {"type": "json_object"}}
        try:
            try:
                return clean_output(self._post(payload))
            except urllib.error.HTTPError as exc:
                if exc.code == 400:                       # server without JSON mode: retry plain
                    payload.pop("response_format")
                    return clean_output(self._post(payload))
                raise
        except urllib.error.HTTPError as exc:
            raise ReviewError(f"The AI service returned an error (HTTP {exc.code}). Check AI_MODEL.") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            raise ReviewError("The local AI service is not reachable or timed out.") from None
        except (KeyError, IndexError, json.JSONDecodeError):
            raise ReviewError("The AI service returned an unexpected response.") from None


def parse_json_object(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = (c == "\\" and not esc)
            if c == '"' and not esc:
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
