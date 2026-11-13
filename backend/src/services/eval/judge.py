"""Groundedness: does an answer stay within the passages it was given?

Judged by the configured chat model, so the score is only as good as that
model. A 1B model gives noisy scores; use a larger one for numbers you rely on
(``--judge-model``). A judge that cannot produce a usable verdict raises
``JudgeError`` and the case is reported as unscored -- never as a pass.
"""

import json
import re
from typing import Any, List, Protocol

PROMPT = """You are checking whether an ANSWER is supported by the PASSAGES it was written from.

PASSAGES:
{passages}

QUESTION: {question}

ANSWER: {answer}

Rate how well the passages support the answer, from 1 to 5:
5 = every claim in the answer is stated in the passages
3 = some claims are supported, others are not
1 = the answer is not supported by the passages (or contradicts them)

Reply with only JSON: {{"score": <1-5>, "reason": "<one sentence>"}}"""

MAX_PASSAGE_CHARS = 1500


class JudgeError(RuntimeError):
    """The judge could not produce a usable score."""


class GroundednessJudge(Protocol):
    async def judge(self, question: str, answer: str, passages: List[str]) -> float:
        """A score from 0.0 (unsupported) to 1.0 (fully supported); raises on failure."""
        ...


def _parse_score(reply: str) -> int:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(0)).get("score")
        except (json.JSONDecodeError, AttributeError):
            raw = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 1 <= raw <= 5:
            return round(raw)
        if raw is not None:
            raise JudgeError(f"the judge's score {raw!r} is not between 1 and 5")
    # Small models often ignore the JSON instruction: take the first standalone 1-5.
    digit = re.search(r"(?<![\d.])([1-5])(?![\d.])", reply)
    if digit:
        return int(digit.group(1))
    raise JudgeError(f"no score found in the judge's reply: {reply[:80]!r}")


class LLMGroundednessJudge:
    """Judges with a chat model reached through an ``LLMProvider``."""

    def __init__(self, provider: Any, model: str):
        self.provider = provider
        self.model = model

    async def judge(self, question: str, answer: str, passages: List[str]) -> float:
        prompt = PROMPT.format(
            passages="\n\n".join(f"[{i}] {text[:MAX_PASSAGE_CHARS]}" for i, text in enumerate(passages, 1)),
            question=question,
            answer=answer,
        )
        response = await self.provider.generate(model=self.model, prompt=prompt, temperature=0.0)
        reply = response.get("response", "") if isinstance(response, dict) else str(response or "")
        return (_parse_score(reply) - 1) / 4
