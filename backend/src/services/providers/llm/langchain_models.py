"""LangChain chat model for OpenAI-compatible backends (used by the agentic graph)."""

from langchain_openai import ChatOpenAI

STRUCTURED_OUTPUT_METHODS = ("json_schema", "json_mode", "function_calling")


class LocalChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` whose structured-output method comes from configuration.

    The agent nodes call ``with_structured_output(Schema)`` without choosing a
    method. LangChain's default (``json_schema``) is not honoured identically by
    every local server, so the platform picks one per deployment
    (``LLM__STRUCTURED_OUTPUT_METHOD``) instead of each node knowing about it.
    """

    structured_output_method: str = "json_schema"

    def with_structured_output(self, schema=None, *, method=None, **kwargs):
        return super().with_structured_output(schema, method=method or self.structured_output_method, **kwargs)
