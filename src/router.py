from dataclasses import dataclass
from typing import Any, Final, Mapping, Optional
import logging
import time

import ollama
from ollama import Client

logger = logging.getLogger(__name__)

ROUTER_QUESTION_ROUTE: Final[str] = "route"
ROUTER_QUESTION_REMEMBER: Final[str] = "should_remember"

ALL_TOOL_NAMES: Final[list] = [
    "web.run",
    "web.fetch",
    "memory.lookup",
    "memory.remember",
]

ROUTE_TO_TOOL: Final[dict] = {
    "needs_search": "web.run",
    "needs_fetch": "web.fetch",
    "needs_lookup": "memory.lookup",
}


def build_router_questions() -> dict:
    """The 2 batched SystemOne questions. Single call, kept short for latency."""
    return {
        ROUTER_QUESTION_ROUTE: {
            "type": "choice",
            "instructions": (
                "Which outside capability does this Discord message need? "
                "Plain chat needs none."
            ),
            "criteria": {
                "needs_search": (
                    "User asks a technical or current-events question needing "
                    "web search (programming, Linux, computers, networking, AI, news)."
                ),
                "needs_fetch": (
                    "Message contains an http/https link the user wants opened or summarized."
                ),
                "needs_lookup": (
                    "User asks about a person, or what is known or remembered about someone."
                ),
                "none": (
                    "Plain chat, joke, opinion, roleplay, or question answerable "
                    "from persona and context. No outside fetch needed."
                ),
            },
        },
        ROUTER_QUESTION_REMEMBER: {
            "type": "noul",
            "instructions": (
                "Does the speaker state a personal fact about themselves worth "
                "saving (name, age, job, hobby, location, like or dislike)?"
            ),
            "criteria": {
                "true": "Speaker states their own attribute (I am / I like / I work as / my ...).",
                "false": (
                    "No self-fact. General chat, world facts, jokes, "
                    "or facts about someone else."
                ),
            },
        },
    }


@dataclass
class RouterConfig:
    enabled: bool = True
    model: str = "tev1:0.8b"
    timeout_s: float = 8.0
    keep_alive: str = "5m"
    route_confidence_min: float = 0.35
    remember_fire: float = 0.7
    remember_skip: float = 0.4

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RouterConfig":
        r = d.get("router", {}) if isinstance(d, Mapping) else {}
        if not isinstance(r, Mapping):
            r = {}
        return cls(
            enabled=bool(r.get("enabled", True)),
            model=str(r.get("model", "tev1:0.8b")),
            timeout_s=float(r.get("timeout_s", 8.0)),
            keep_alive=str(r.get("keep_alive", "5m")),
            route_confidence_min=float(r.get("route_confidence_min", 0.35)),
            remember_fire=float(r.get("remember_fire", 0.7)),
            remember_skip=float(r.get("remember_skip", 0.4)),
        )


@dataclass
class RouteDecision:
    """Outcome of the Tev pre-pass.

    forced: tool names the LLM MUST call (empty = answer directly, no tools).
    fallback: True means ignore forced and use all tools with auto (today's behavior).
    remember_optional: include memory.remember as an optional tool when not forced.
    """

    forced: list
    fallback: bool
    remember_optional: bool
    reason: str


def _field(answer: Any, name: str, default: Any = None) -> Any:
    if answer is None:
        return default
    if isinstance(answer, Mapping):
        return answer.get(name, default)
    return getattr(answer, name, default)


def decide_route(answers: Optional[Mapping[str, Any]], cfg: RouterConfig) -> RouteDecision:
    """Pure function: Tev answers + thresholds -> tool decision. No I/O."""
    if not answers:
        return RouteDecision(
            forced=[], fallback=True, remember_optional=True, reason="no answers"
        )

    route = answers.get(ROUTER_QUESTION_ROUTE)
    choice = _field(route, "choice")
    confidence = _field(route, "confidence", 0.0) or 0.0
    score = _field(answers.get(ROUTER_QUESTION_REMEMBER), "noul")

    remember_forced = score is not None and score >= cfg.remember_fire
    remember_optional = score is None or score >= cfg.remember_skip

    if choice is None or choice not in ("needs_search", "needs_fetch", "needs_lookup", "none"):
        return RouteDecision(
            forced=[],
            fallback=True,
            remember_optional=True,
            reason=f"unknown route choice={choice!r}",
        )

    if confidence < cfg.route_confidence_min:
        return RouteDecision(
            forced=[],
            fallback=True,
            remember_optional=True,
            reason=f"low route confidence={confidence:.2f}",
        )

    if choice == "none":
        if remember_forced:
            return RouteDecision(
                forced=["memory.remember"],
                fallback=False,
                remember_optional=False,
                reason="plain chat + self-fact",
            )
        return RouteDecision(
            forced=[],
            fallback=False,
            remember_optional=remember_optional,
            reason="plain chat",
        )

    forced = [ROUTE_TO_TOOL[choice]]
    if remember_forced:
        forced.append("memory.remember")
    return RouteDecision(
        forced=forced,
        fallback=False,
        remember_optional=remember_optional and not remember_forced,
        reason=f"route={choice} conf={confidence:.2f} remember={score}",
    )


class RouterError(Exception):
    """Tev unavailable (daemon down, timeout, model missing). Caller falls back."""


def route_message(state: str, cfg: RouterConfig) -> RouteDecision:
    """Call Tev once (both questions batched) and decide. Raises RouterError on failure."""
    if not cfg.enabled:
        return RouteDecision(
            forced=[], fallback=True, remember_optional=True, reason="router disabled"
        )
    if not state or not state.strip():
        return RouteDecision(
            forced=[], fallback=True, remember_optional=True, reason="empty state"
        )
    try:
        client = Client(timeout=cfg.timeout_s)
        start = time.monotonic()
        resp = client.systemone(
            model=cfg.model,
            state=state,
            questions=build_router_questions(),
            keep_alive=cfg.keep_alive,
        )
        elapsed = time.monotonic() - start
        decision = decide_route(resp.answers, cfg)
        logger.info(f"Tev route: {decision.reason} ({elapsed:.1f}s)")
        return decision
    except Exception as e:
        msg = str(e)
        logger.warning(f"Tev unavailable, falling back to LLM auto: {e}")
        raise RouterError(msg) from e
