"""Check an OpenAI-compatible LLM endpoint against what the platform needs.

Use this on the inference host (vLLM, TGI, llama.cpp, Ollama's /v1, ...) before
pointing LLM__PROVIDER at it, and to attach evidence to the support matrix in
docs/deployment/inference-backends.md.

    python -m src.cli.check_llm_backend http://vllm:8000/v1 my-served-model
    python -m src.cli.check_llm_backend http://host:8080/v1 model --api-key "$KEY"

or, against a running stack:

    docker exec rag-api python -m src.cli.check_llm_backend http://vllm:8000/v1 my-served-model

It runs the same code the API uses (OpenAICompatibleLLMProvider): health,
model listing, a plain completion, a grounded RAG answer, a streamed RAG answer,
and the failure paths. Exit status is non-zero if a required check fails.
"""

import argparse
import asyncio
import sys
from typing import Awaitable, Callable, List, Tuple

import httpx
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

CHUNKS = [
    {"chunk_text": "Customers may return products within 14 days of delivery for a full refund.", "title": "Returns Policy"},
    {"chunk_text": "Shipping within the EU takes 3 to 5 business days.", "title": "Shipping"},
]

Result = Tuple[str, str, str]  # (PASS | FAIL | INFO, name, detail)


async def run_checks(
    endpoint: str,
    model: str,
    api_key: str = "",
    max_tokens: int = 200,
    timeout: float = 300.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> List[Result]:
    provider = OpenAICompatibleLLMProvider(
        endpoint, model, api_key=api_key or None, timeout=timeout, max_tokens=max_tokens, transport=transport
    )
    results: List[Result] = []

    async def check(name: str, probe: Callable[[], Awaitable[Tuple[bool, str]]], required: bool = True) -> None:
        try:
            ok, detail = await probe()
        except Exception as exc:  # noqa: BLE001 - a failing probe is a result, not a crash
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(("PASS" if ok else ("FAIL" if required else "INFO"), name, detail))

    async def health():
        h = await provider.health_check()
        return h["status"] == "healthy", h["message"]

    async def models():
        listed = await provider.list_models()
        names = [m.get("id") or m.get("name") or m.get("model") for m in listed]
        return bool(listed), f"{names}"[:160]

    async def generate():
        r = await provider.generate(model, "Reply with the single word: pong", temperature=0)
        return bool(r["response"].strip()), f"{r['response'][:60]!r}, {r['usage_metadata']['total_tokens']} tokens"

    async def rag():
        r = await provider.generate_rag_answer("How many days do I have to return a product?", CHUNKS, model=model)
        return "14" in r["answer"], repr(r["answer"][:100])

    async def rag_stream():
        pieces = [c async for c in provider.generate_rag_answer_stream("How long does EU shipping take?", CHUNKS, model=model)]
        text = "".join(c["response"] for c in pieces)
        done = bool(pieces) and pieces[-1]["done"]
        return done and bool(text.strip()), f"{len(pieces)} chunks, {text[:80]!r}"

    async def unknown_model():
        try:
            await provider.generate("no-such-model-xyz", "hi")
        except Exception as exc:  # noqa: BLE001
            return True, f"rejected ({type(exc).__name__})"
        return False, "accepted an unknown model name (some servers, e.g. llama.cpp, serve one model and ignore the name)"

    await check("health", health)
    await check("list models", models, required=False)  # /models is optional on some servers
    await check("plain completion", generate)
    await check("grounded RAG answer", rag)
    await check("streamed RAG answer", rag_stream)
    await check("unknown model is rejected", unknown_model, required=False)
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("endpoint", help="base URL including /v1, e.g. http://vllm:8000/v1")
    parser.add_argument("model", help="served model name the endpoint advertises")
    parser.add_argument("--api-key", default="", help="bearer token, if the endpoint requires one")
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    results = asyncio.run(run_checks(args.endpoint, args.model, args.api_key, args.max_tokens, args.timeout))
    for status, name, detail in results:
        print(f"{status:<4}  {name}: {detail}")
    return 1 if any(status == "FAIL" for status, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
