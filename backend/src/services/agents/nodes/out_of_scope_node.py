import logging
from typing import Dict, List

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from ..context import Context
from ..state import AgentState
from .utils import get_latest_query

logger = logging.getLogger(__name__)


async def ainvoke_out_of_scope_step(
    state: AgentState,
    runtime: Runtime[Context],
) -> Dict[str, List[AIMessage]]:
    """Handle out-of-scope queries with a helpful message.

    This node responds to queries that are outside the domain of
    CS/AI/ML research papers with a polite, informative message.

    :param state: Current agent state
    :param runtime: Runtime context (not used in this node)
    :returns: Dictionary with messages containing the out-of-scope response
    """
    logger.info("NODE: out_of_scope")

    question = get_latest_query(state["messages"])

    # Generate helpful response message
    response_text = (
        "I can only answer questions using the documents in this knowledge base.\n\n"
        f"Your question: '{question}'\n\n"
        "I could not match it to anything in the indexed documents. You might want to try:\n"
        "- Rephrasing the question using wording closer to the source documents\n"
        "- Checking that the relevant document has been uploaded and finished indexing\n"
        "- A general-purpose assistant, if this is a general knowledge question\n\n"
        "If you have a question about the indexed documents, I'd be happy to help!"
    )

    logger.info("Responding with out-of-scope message")

    return {"messages": [AIMessage(content=response_text)]}
