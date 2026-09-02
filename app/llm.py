from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    """Raised when the model provider fails to return a response."""


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str) -> str:
        """Return raw model text. The caller is responsible for parsing/validating it."""
        ...


# Naive keyword tables used only by the fake's default classification.
_BILLING = {
    "billing", "charge", "charged", "charges", "refund", "refunded", "invoice",
    "invoices", "payment", "card", "statement", "subscription", "overcharged",
    "overcharge", "price", "pricing", "receipt",
}
_TECHNICAL = {
    "technical", "error", "errors", "500", "500s", "503", "timeout", "bug", "broken",
    "crash", "crashing", "integration", "export", "api", "http", "upload", "uploading",
    "endpoint", "server", "latency", "exception", "failing",
}
_ACCOUNT = {
    "account", "password", "login", "credentials", "reset", "signin", "email",
    "username", "verification", "mfa", "2fa", "locked", "access",
}

# High priority comes from concrete incident signals, not from a caller's adjective
# such as "urgent", which untrusted content can trivially assert.
_HIGH = {"production", "blocking", "outage", "down", "500", "500s", "503"}
_LOW = {
    "nice", "feature", "request", "dark", "cosmetic", "minor", "someday",
    "whenever", "eventually", "later",
}

# Tie-break order when several categories score equally.
_CATEGORY_ORDER = ("technical", "account", "billing", "other")


@dataclass
class FakeLLMClient:
    """Deterministic stand-in for a real provider.

    With no ``responses`` it classifies by simple keyword rules and returns valid
    JSON. Pass ``responses`` to script behaviour in tests: strings are returned as
    raw output, exceptions are raised. The last element repeats once exhausted, so
    a single malformed string or error applies to every attempt.
    """

    responses: list | None = None
    calls: int = field(default=0, init=False)

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        if self.responses is not None:
            if not self.responses:
                raise LLMError("no scripted responses left")
            item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
            if isinstance(item, Exception):
                raise item
            return item
        return _heuristic_response(user)


def _heuristic_response(user: str) -> str:
    tokens = set(re.findall(r"[a-z0-9]+", user.lower()))
    return json.dumps(
        {
            "category": _pick_category(tokens),
            "priority": _pick_priority(tokens),
            "summary": _make_summary(user),
        }
    )


def _pick_category(tokens: set[str]) -> str:
    scores = {
        "billing": len(tokens & _BILLING),
        "technical": len(tokens & _TECHNICAL),
        "account": len(tokens & _ACCOUNT),
    }
    best = max(scores.values())
    if best == 0:
        return "other"
    return next(name for name in _CATEGORY_ORDER if scores.get(name) == best)


def _pick_priority(tokens: set[str]) -> str:
    if tokens & _HIGH:
        return "high"
    if tokens & _LOW:
        return "low"
    return "medium"


def _make_summary(user: str) -> str:
    section = user.split("body:", 1)[1] if "body:" in user else user
    section = section.replace("</ticket>", " ").strip()
    if not section and "subject:" in user:
        section = user.split("subject:", 1)[1].split("body:", 1)[0]
        section = section.replace("</ticket>", " ").strip()

    sentence = re.split(r"(?<=[.!?])\s+", section)[0] if section else ""
    sentence = " ".join(sentence.split())
    if not sentence:
        return "No description provided"
    if len(sentence) > 150:
        sentence = sentence[:147].rstrip() + "..."
    return sentence
