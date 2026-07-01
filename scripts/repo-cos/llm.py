#!/usr/bin/env python3
"""LLM synthesis — cluster deterministic candidates into ranked, shippable proposals.

Stage-2 of repo-cos (survivors only). Mirrors `scripts/mail-actions/llm.py`: the
network call is isolated in `_call_openrouter` so the parser/validator
(`parse_proposals`) is unit-testable with no key/network, and there is a single
malformed-output retry.

The anti-slop mandate is enforced STRUCTURALLY here as well as in the prompt:
  * output is HARD-CAPPED to `top` proposals (we truncate even if the model returns more);
  * every proposal MUST carry >=1 concrete `file:line` evidence ref drawn from the
    candidate set — proposals with no evidence are DROPPED in the parser;
  * proposals are re-sorted to put `ci_verifiable` first (bias toward CI/test-verifiable
    fixes), the model's order broken only as a tie-breaker.

JSON contract per proposal:
  {title, repo, evidence:[ref...], why, effort (S|M|L), approach, ci_verifiable (bool)}
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a pragmatic engineering chief-of-staff. You are given a list of RAW, "
    "deterministic signals mined from a developer's git repos (TODO/FIXME markers, "
    "skipped/xfail tests, high-churn files, oversized files, stale lockfiles), each with "
    "a concrete repo/file:line reference. Your job: CLUSTER these into a small set of "
    "BOUNDED, SHIPPABLE improvement PROPOSALS that an agent could implement AND verify in "
    "one sitting.\n\n"
    "HARD RULES:\n"
    "1. Return AT MOST {top} proposals, ranked best-first by leverage "
    "(productivity gain OR making the repo/product better).\n"
    "2. Every proposal MUST cite at least one exact evidence ref taken verbatim from the "
    "input candidates (the 'ref' field). Never invent a file or line.\n"
    "3. Strongly PREFER proposals whose value is CI/test-verifiable (fix a skipped/flaky "
    "test, add a missing test, remove dead code, fix a concrete bug). Set ci_verifiable=true "
    "for these and rank them above vague 'nice idea' items.\n"
    "4. DROP vague, speculative, or unbounded proposals. A proposal must be a specific, "
    "finishable change — not 'consider refactoring X someday'.\n"
    "5. Keep each proposal tight: one clear change with a 1-2 line approach.\n\n"
    "Return ONLY a JSON object: {\"proposals\": [ {title, repo, evidence (array of ref "
    "strings), why (string: 1 line, productivity or repo/product-better), effort (one of "
    "\"S\",\"M\",\"L\"), approach (1-2 lines), ci_verifiable (bool)} ] }. No prose."
)

VALID_EFFORT = {"S", "M", "L"}


@dataclass(frozen=True)
class Proposal:
    title: str
    repo: str
    evidence: list[str]
    why: str
    effort: str
    approach: str
    ci_verifiable: bool

    def as_dict(self) -> dict:
        return {
            "title": self.title, "repo": self.repo, "evidence": list(self.evidence),
            "why": self.why, "effort": self.effort, "approach": self.approach,
            "ci_verifiable": self.ci_verifiable,
        }


@dataclass
class Synthesis:
    proposals: list[Proposal] = field(default_factory=list)
    approx_prompt_tokens: int = 0
    model: str = DEFAULT_MODEL


class SynthesisError(ValueError):
    """Raised when the model output cannot be parsed into valid proposals."""


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


def _strip_to_json(text: str) -> str:
    """Pull the first {...} block out of a possibly-fenced model reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise SynthesisError("no JSON object found in model output")
    return text[start : end + 1]


def parse_proposals(text: str, *, top: int, valid_refs: set[str] | None = None) -> list[Proposal]:
    """Parse + validate model output into ranked Proposals. Raises SynthesisError on
    unparseable JSON or a missing `proposals` array.

    Structural anti-slop enforcement (does NOT raise — just filters):
      * a proposal with no non-empty evidence ref is DROPPED;
      * if `valid_refs` is given, evidence refs not present in the candidate set are
        stripped, and a proposal left with zero valid refs is DROPPED (stops the model
        inventing files);
      * effort is normalized to S/M/L (default M);
      * the list is re-sorted ci_verifiable-first (stable within each group) and then
        HARD-CAPPED to `top`.
    """
    try:
        obj = json.loads(_strip_to_json(text))
    except json.JSONDecodeError as exc:
        raise SynthesisError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict) or "proposals" not in obj:
        raise SynthesisError("missing 'proposals' array")
    raw = obj["proposals"]
    if not isinstance(raw, list):
        raise SynthesisError("'proposals' is not a list")

    out: list[Proposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ev_in = item.get("evidence") or []
        if isinstance(ev_in, str):
            ev_in = [ev_in]
        evidence = [str(e).strip() for e in ev_in if str(e).strip()]
        if valid_refs is not None:
            evidence = [e for e in evidence if _ref_known(e, valid_refs)]
        if not evidence:  # anti-slop: no concrete evidence → drop
            continue
        effort = str(item.get("effort") or "M").strip().upper()
        if effort not in VALID_EFFORT:
            effort = "M"
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(Proposal(
            title=title,
            repo=str(item.get("repo") or "").strip(),
            evidence=evidence,
            why=str(item.get("why") or "").strip(),
            effort=effort,
            approach=str(item.get("approach") or "").strip(),
            ci_verifiable=_coerce_bool(item.get("ci_verifiable")),
        ))

    # ci_verifiable-first, preserving the model's within-group ranking (stable sort).
    out.sort(key=lambda p: 0 if p.ci_verifiable else 1)
    return out[:top]


def _ref_known(ref: str, valid_refs: set[str]) -> bool:
    """A model ref is valid ONLY if it exactly matches a candidate ref, or matches a
    candidate's file PATH exactly (the model may drop/alter the `:line`). No prefix
    matching — a bare repo name or truncated path must NOT pass, or the "drop invented
    refs" anti-slop guarantee is hollow (a model could cite `devrc` and survive)."""
    if ref in valid_refs:
        return True
    ref_path = ref.split(":", 1)[0]
    return any(v.split(":", 1)[0] == ref_path for v in valid_refs)


def build_feedback_block(feedback) -> str:
    """Render the REPLY-FEEDBACK context block prepended to the user prompt.

    `feedback` is a `feedback.Feedback` (duck-typed: needs `.prev_summary()` and
    `.reply_text`). It carries LAST week's proposals + Zach's emailed reply so the model
    can drop what he rejected and honor his steering — WITHOUT bypassing the evidence
    requirement (the block is context, the HARD RULES below still apply to new output).
    """
    prev = feedback.prev_summary()
    lines = ["=== LAST WEEK'S FEEDBACK (context — read before the new signals) ==="]
    if prev:
        lines.append("Previous proposals you sent the user:")
        lines.extend(f"  - {p}" for p in prev)
    else:
        lines.append("(previous proposals unavailable)")
    lines.append("")
    lines.append(f'USER\'S REPLY: "{feedback.reply_text.strip()}"')
    lines.append("")
    lines.append(
        "The user reviewed last week's proposals and replied above. Take their feedback "
        "into account — do NOT re-propose what they rejected or dismissed, honor any "
        "steering (what to focus on / ignore), and reflect their stated preferences — "
        "while still surfacing genuinely NEW high-value candidates from the signals below. "
        "This feedback is CONTEXT only: every new proposal must STILL cite concrete "
        "file:line evidence from the candidates per the rules."
    )
    lines.append("=== END FEEDBACK ===")
    lines.append("")
    return "\n".join(lines)


def build_user_prompt(candidates: list[dict], *, top: int, feedback=None) -> str:
    """Render the capped candidate evidence compactly (one line each) for the model.

    When `feedback` is present, the REPLY-FEEDBACK block is prepended (before the new
    candidates) so the model steers off last week's reply."""
    lines: list[str] = []
    if feedback is not None:
        lines.append(build_feedback_block(feedback))
    lines.append(
        f"Cluster these {len(candidates)} raw signals into at most {top} bounded, "
        "shippable, ranked proposals per the rules. Candidates (kind | ref | detail):")
    lines.append("")
    for c in candidates:
        detail = (c.get("text") or "").replace("\n", " ")[:160]
        lines.append(f"- {c['kind']:<12} {c['ref']}  {detail}")
    return "\n".join(lines)


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for the cost log. Order-of-magnitude only."""
    return max(1, len(text) // 4)


def _call_openrouter(model: str, system: str, user: str, api_key: str,
                     timeout: float = 90.0) -> str:
    """POST to OpenRouter; return the assistant message content. Network-only — mocked in tests."""
    import requests

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "devrc-repo-cos",
        },
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def synthesize(
    candidates: list[dict],
    *,
    top: int = 5,
    model: str | None = None,
    api_key: str | None = None,
    feedback=None,
    _caller=_call_openrouter,
) -> Synthesis:
    """Synthesize proposals, retrying on malformed OR empty output. `_caller` injectable.

    DeepSeek rotates its output even at temperature=0, so an identical candidate set
    can yield 4-5 good proposals one call and 0 the next. Retrying up to 3× when there
    ARE candidates kills that empty-tail (a weekly digest should not be blank while real
    signals exist); we still emit an honest empty result if the model surfaces nothing
    across every attempt. `candidates` are candidate dicts (kind/ref/text).

    `feedback` (optional `feedback.Feedback`): when present, LAST week's proposals + Zach's
    emailed reply are prepended to the user prompt so the model drops what he rejected and
    honors his steering. It's CONTEXT only — the structural evidence/anti-slop rules still
    fully apply to new output.
    """
    model = model or os.environ.get("REPO_COS_MODEL", DEFAULT_MODEL)
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    system = SYSTEM_PROMPT.replace("{top}", str(top))
    user = build_user_prompt(candidates, top=top, feedback=feedback)
    if feedback is not None:
        when = getattr(feedback, "replied_at", "") or "unknown"
        print(f"  feedback: applied reply from {when} "
              f"({len(feedback.reply_text)} chars) into synthesis", file=sys.stderr)
    valid_refs = {c["ref"] for c in candidates if c.get("ref")}
    approx = _approx_tokens(system) + _approx_tokens(user)

    last_err: Exception | None = None
    last_ok: list | None = None
    attempts = 3 if valid_refs else 1  # only worth re-rolling when candidates exist
    for _ in range(attempts):
        raw = _caller(model, system, user, api_key)
        try:
            props = parse_proposals(raw, top=top, valid_refs=valid_refs)
        except SynthesisError as exc:
            last_err = exc
            continue
        if props:  # non-empty — done
            return Synthesis(proposals=props, approx_prompt_tokens=approx, model=model)
        last_ok = props  # valid but empty — re-roll, but remember it
    if last_ok is not None:  # genuinely nothing surfaced after every attempt
        return Synthesis(proposals=last_ok, approx_prompt_tokens=approx, model=model)
    raise SynthesisError(f"model output invalid after {attempts} attempt(s): {last_err}")
