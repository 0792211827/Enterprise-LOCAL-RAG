import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from src.schemas.ollama import RAGResponse


class RAGPromptBuilder:
    """Builder class for creating RAG prompts."""

    def __init__(self):
        """Initialize the prompt builder."""
        self.prompts_dir = Path(__file__).parent / "prompts"
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """Load the system prompt from the text file.

        Returns:
            System prompt string
        """
        prompt_file = self.prompts_dir / "rag_system.txt"
        if not prompt_file.exists():
            # Fallback to default prompt if file doesn't exist
            return (
                "You are an AI assistant that answers questions using an organisation's "
                "own documents. Base your answer STRICTLY on the provided document "
                "excerpts and cite them by their bracketed number, e.g. [1]."
            )
        return prompt_file.read_text().strip()

    def create_rag_prompt(self, query: str, chunks: List[Dict[str, Any]], system_prompt: Optional[str] = None) -> str:
        """Create a RAG prompt with query and retrieved chunks.

        Args:
            query: User's question
            chunks: List of retrieved chunks with metadata from OpenSearch
            system_prompt: Overrides the default system prompt when provided

        Returns:
            Formatted prompt string
        """
        prompt = f"{system_prompt or self.system_prompt}\n\n"
        prompt += "### Context from Documents:\n\n"

        for i, chunk in enumerate(chunks, 1):
            # Get the actual chunk text
            chunk_text = chunk.get("chunk_text", chunk.get("content", ""))

            # Label each excerpt with its ordinal and, when known, the document
            # title and section. The ordinal is what the model is told to cite,
            # so it can never invent an identifier it was not given.
            title = chunk.get("document_title") or chunk.get("title") or ""
            section = chunk.get("section_title") or ""
            label = f"{i}"
            if title:
                label += f". {title}"
            if section:
                label += f" - {section}"

            prompt += f"[{label}]\n"
            prompt += f"{chunk_text}\n\n"

        prompt += f"### Question:\n{query}\n\n"
        prompt += (
            "### Answer:\nProvide a natural, conversational response (not JSON). Cite the "
            "excerpts you used by their bracketed number, e.g. [1]. Refer to documents by "
            "title only; never invent an identifier, URL or reference number.\n\n"
        )

        return prompt

    def create_structured_prompt(
        self, query: str, chunks: List[Dict[str, Any]], system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a prompt for Ollama with structured output format.

        Args:
            query: User's question
            chunks: List of retrieved chunks

        Returns:
            Dictionary with prompt and format schema for Ollama
        """
        prompt_text = self.create_rag_prompt(query, chunks, system_prompt=system_prompt)

        # Return prompt with Pydantic model schema for structured output
        return {
            "prompt": prompt_text,
            "format": RAGResponse.model_json_schema(),
        }


class ResponseParser:
    """Parser for LLM responses."""

    @staticmethod
    def parse_structured_response(response: str) -> Dict[str, Any]:
        """Parse a structured response from Ollama.

        Args:
            response: Raw LLM response string

        Returns:
            Dictionary with parsed response
        """
        try:
            # Try to parse as JSON and validate with Pydantic
            parsed_json = json.loads(response)
            validated_response = RAGResponse(**parsed_json)
            return validated_response.model_dump()
        except (json.JSONDecodeError, ValidationError):
            # Fallback: try to extract JSON from the response
            return ResponseParser._extract_json_fallback(response)

    @staticmethod
    def _extract_json_fallback(response: str) -> Dict[str, Any]:
        """Extract JSON from response text as fallback.

        Args:
            response: Raw response text

        Returns:
            Dictionary with extracted content or fallback
        """
        # Try to find JSON in the response
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                # Validate with Pydantic, using defaults for missing fields
                validated = RAGResponse(**parsed)
                return validated.model_dump()
            except (json.JSONDecodeError, ValidationError):
                pass

        # Final fallback: return response as plain text
        return {
            "answer": response,
            "sources": [],
            "confidence": "low",
            "citations": [],
        }
