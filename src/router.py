from dataclasses import dataclass
from typing import Any, Final, Mapping, Optional
import json
import logging
import os
import time
import urllib.request
import urllib.error

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

# Cloudflare Workers AI model catalogue IDs for the Clef decision models.
# Config `router.model` accepts either the short selector ("clef-flash",
# "clef") or the full ID; anything else is passed through as a custom ID.
CLEF_MODEL_IDS: Final[dict] = {
    "clef-flash": "@cf/cloudflare/clef-flash",
    "clef": "@cf/cloudflare/clef",
}
DEFAULT_MODEL: Final[str] = "clef-flash"


def _env_first(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def resolve_model_id(model: str) -> tuple[str, str]:
    """Return (workers_ai_id, body_selector) for a configured model name."""
    name = (model or DEFAULT_MODEL).strip()
    if name in CLEF_MODEL_IDS:
        return CLEF_MODEL_IDS[name], name
    if name.startswith("@cf/"):
        # Full ID given: body selector is the trailing segment after the
        # last slash, e.g. "@cf/cloudflare/clef-flash" -> "clef-flash".
        selector = name.rsplit("/", 1)[-1].strip() or DEFAULT_MODEL
        return name, selector
    return name, name


def build_router_questions() -> dict:
    """The 2 batched decision questions. Single call, kept short for latency."""
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
    backend: str = "clef"
    model: str = DEFAULT_MODEL
    tev_model: str = "tev1:0.8b"
    timeout_s: float = 8.0
    route_confidence_min: float = 0.35
    remember_fire: float = 0.7
    remember_skip: float = 0.4
    account_id: Optional[str] = None
    api_key: Optional[str] = None
    # Ollama keep_alive for the tev backend; ignored by clef.
    keep_alive: str = "5m"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RouterConfig":
        r = d.get("router", {}) if isinstance(d, Mapping) else {}
        if not isinstance(r, Mapping):
            r = {}
        return cls(
            enabled=bool(r.get("enabled", True)),
            backend=str(r.get("backend", "clef")),
            model=str(r.get("model", DEFAULT_MODEL)),
            tev_model=str(r.get("tev_model", "tev1:0.8b")),
            timeout_s=float(r.get("timeout_s", 8.0)),
            route_confidence_min=float(r.get("route_confidence_min", 0.35)),
            remember_fire=float(r.get("remember_fire", 0.7)),
            remember_skip=float(r.get("remember_skip", 0.4)),
            account_id=r.get("account_id")
            or _env_first(
                "WORKERS_AI_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "ACCOUNT_ID"
            ),
            api_key=r.get("api_key")
            or _env_first(
                "WORKERS_AI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN"
            ),
            keep_alive=str(r.get("keep_alive", "5m")),
        )

    def credentials(self) -> tuple[Optional[str], Optional[str]]:
        """Account ID + API token, re-reading env so import order vs dotenv
        never leaves stale Nones behind."""
        account = self.account_id or _env_first(
            "WORKERS_AI_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "ACCOUNT_ID"
        )
        token = self.api_key or _env_first(
            "WORKERS_AI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN"
        )
        return account, token


@dataclass
class RouteDecision:
    """Outcome of the Clef pre-pass.

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
    """Pure function: Clef answers + thresholds -> tool decision. No I/O."""
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
    """Clef unavailable (bad credentials, HTTP error, timeout). Caller falls back."""


def _post_clef(
    account_id: str, api_key: str, model_id: str, selector: str, state: str, timeout_s: float
) -> dict:
    """One Workers AI run call. Returns the unwrapped result dict."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model_id}"
    payload = json.dumps(
        {
            "model": selector,
            "state": state,
            "questions": build_router_questions(),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        raise RouterError(f"Clef HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RouterError(f"Clef network error: {e}") from e

    # Workers AI wraps results: {"result": {...}, "success": bool, ...}.
    # Accept a bare result dict too, for forward compat.
    if isinstance(body, Mapping) and "result" in body:
        if not body.get("success", True):
            errors = body.get("errors", body.get("messages", "unknown error"))
            raise RouterError(f"Clef API error: {errors}")
        result = body["result"]
    else:
        result = body
    if not isinstance(result, Mapping) or "answers" not in result:
        raise RouterError(f"Clef bad response shape: {str(body)[:300]}")
    return result


def route_message(state: str, cfg: RouterConfig) -> RouteDecision:
    """Call the configured backend once (both questions batched) and decide.

    Raises RouterError on failure; the caller falls back to all-tools auto.
    """
    if not cfg.enabled:
        return RouteDecision(
            forced=[], fallback=True, remember_optional=True, reason="router disabled"
        )
    if not state or not state.strip():
        return RouteDecision(
            forced=[], fallback=True, remember_optional=True, reason="empty state"
        )
    if (cfg.backend or "clef").strip().lower() == "tev":
        return _route_via_tev(state, cfg)
    return _route_via_clef(state, cfg)


def _route_via_tev(state: str, cfg: RouterConfig) -> RouteDecision:
    """Local Tev decision model via the Ollama daemon (SystemOne API)."""
    try:
        from ollama import Client
    except ImportError as e:
        raise RouterError(f"ollama package not installed: {e}") from e
    try:
        client = Client(timeout=cfg.timeout_s)
        start = time.monotonic()
        resp = client.systemone(
            model=cfg.tev_model,
            state=state,
            questions=build_router_questions(),
            keep_alive=cfg.keep_alive,
        )
        elapsed = time.monotonic() - start
        decision = decide_route(resp.answers, cfg)
        logger.info(f"Tev route: {decision.reason} ({elapsed:.1f}s)")
        return decision
    except RouterError:
        raise
    except Exception as e:
        msg = str(e)
        logger.warning(f"Tev unavailable, falling back to LLM auto: {e}")
        raise RouterError(msg) from e


def _route_via_clef(state: str, cfg: RouterConfig) -> RouteDecision:
    account_id, api_key = cfg.credentials()
    if not account_id or not api_key:
        logger.warning("Clef unavailable (missing account ID or API key), falling back to LLM auto")
        raise RouterError("missing Cloudflare Workers AI credentials")
    model_id, selector = resolve_model_id(cfg.model)
    try:
        start = time.monotonic()
        result = _post_clef(account_id, api_key, model_id, selector, state, cfg.timeout_s)
        elapsed = time.monotonic() - start
        decision = decide_route(result.get("answers"), cfg)
        logger.info(f"Clef route: {decision.reason} ({elapsed:.1f}s)")
        return decision
    except RouterError:
        logger.warning("Clef unavailable, falling back to LLM auto", exc_info=True)
        raise
    except Exception as e:
        msg = str(e)
        logger.warning(f"Clef unavailable, falling back to LLM auto: {e}")
        raise RouterError(msg) from e
