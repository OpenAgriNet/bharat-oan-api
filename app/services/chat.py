from fastapi import BackgroundTasks
from agents.agrinet import agrinet_agent
from agents.moderation import moderation_agent
from helpers.utils import get_logger
from app.utils import (
    update_message_history, 
    trim_history, 
    format_message_pairs
)
# from app.tasks.suggestions import create_suggestions  # Commented out: suggestion agent disabled
from agents.deps import FarmerContext

logger = get_logger(__name__)

async def get_chat_response(
    query: str,
    session_id: str,
    source_lang: str,
    target_lang: str,
    user_id: str,
    history: list,
    background_tasks: BackgroundTasks
) -> str:
    """Get direct chat response (non-streaming)."""
    # Generate a unique content ID for this query
    content_id = f"query_{session_id}_{len(history)//2 + 1}"
       
    deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

    message_pairs = "\n\n".join(format_message_pairs(history, 3))
    logger.info(f"Message pairs: {message_pairs}")
    if message_pairs:
        last_response = f"**Conversation**\n\n{message_pairs}\n\n---\n\n"
    else:
        last_response = ""
    
    user_message    = f"{last_response}{deps.get_user_message()}"
    moderation_run  = await moderation_agent.run(user_message)
    moderation_data = moderation_run.output
    logger.info(f"Moderation data: {moderation_data}")

    
    # Generate suggestions after moderation passes
    # Commented out: suggestion agent disabled
    # if moderation_data.category == "valid_schemes":
    #     logger.info(f"Triggering suggestions generation for session {session_id}")
    #     try:
    #         background_tasks.add_task(create_suggestions, session_id, target_lang)
    #         logger.info("Successfully added suggestions task")
    #     except Exception as e:
    #         logger.error(f"Error adding suggestions task: {str(e)}")

    deps.update_moderation_str(str(moderation_data))

    user_message = deps.get_user_message()
    logger.info(f"Running agent with user message: {user_message}")

    # Run the main agent
    trimmed_history = trim_history(
        history,
        max_tokens=80_000
    )
    
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")

    # Run agent and get direct response
    logger.info(f"Running agrinet agent for session {session_id}")
    agent_run = await agrinet_agent.run(
        user_prompt=user_message,
        message_history=trimmed_history,
        deps=deps
    )
    
    # Get the result and new messages
    # For output_type=str, the result is in agent_run.data
    response_text = agent_run.data if agent_run and hasattr(agent_run, 'data') else ""
    if not response_text and agent_run and hasattr(agent_run, 'output'):
        response_text = str(agent_run.output) if agent_run.output else ""
    
    new_messages = agent_run.new_messages() if agent_run else []
    logger.info(f"Agent run complete for session {session_id}, response length: {len(response_text)}")
    
    # Post-processing
    messages = [
        *history,
        *new_messages
    ]

    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)
    
    # Return the response
    return response_text


async def stream_chat_response(
    query: str,
    session_id: str,
    source_lang: str,
    target_lang: str,
    user_id: str,
    history: list,
    background_tasks: BackgroundTasks,
    chunk_size: int = 50
):
    """Stream chat response in chunks using Server-Sent Events format.
    
    Args:
        chunk_size: Number of characters per chunk (default: 50)
    """
    # Generate a unique content ID for this query
    content_id = f"query_{session_id}_{len(history)//2 + 1}"
       
    deps = FarmerContext(query=query, lang_code=target_lang, session_id=session_id)

    message_pairs = "\n\n".join(format_message_pairs(history, 3))
    logger.info(f"Message pairs: {message_pairs}")
    if message_pairs:
        last_response = f"**Conversation**\n\n{message_pairs}\n\n---\n\n"
    else:
        last_response = ""
    
    user_message    = f"{last_response}{deps.get_user_message()}"
    
    # Send moderation status
    yield {'type': 'status', 'content': 'Checking moderation...'}
    
    moderation_run  = await moderation_agent.run(user_message)
    moderation_data = moderation_run.output
    logger.info(f"Moderation data: {moderation_data}")

    # Generate suggestions after moderation passes
    # Commented out: suggestion agent disabled
    # if moderation_data.category == "valid_schemes":
    #     logger.info(f"Triggering suggestions generation for session {session_id}")
    #     try:
    #         background_tasks.add_task(create_suggestions, session_id, target_lang)
    #         logger.info("Successfully added suggestions task")
    #     except Exception as e:
    #         logger.error(f"Error adding suggestions task: {str(e)}")

    deps.update_moderation_str(str(moderation_data))

    user_message = deps.get_user_message()
    logger.info(f"Running agent with user message: {user_message}")

    # Run the main agent
    trimmed_history = trim_history(
        history,
        max_tokens=80_000
    )
    
    logger.info(f"Trimmed history length: {len(trimmed_history)} messages")

    # Send processing status
    yield {'type': 'status', 'content': 'Processing your query...'}

    # Run agent and get direct response
    logger.info(f"Running agrinet agent for session {session_id}")
    agent_run = await agrinet_agent.run(
        user_prompt=user_message,
        message_history=trimmed_history,
        deps=deps
    )
    
    # Get the result and new messages
    # For output_type=str, the result is in agent_run.data
    response_text = agent_run.data if agent_run and hasattr(agent_run, 'data') else ""
    if not response_text and agent_run and hasattr(agent_run, 'output'):
        response_text = str(agent_run.output) if agent_run.output else ""
    
    new_messages = agent_run.new_messages() if agent_run else []
    logger.info(f"Agent run complete for session {session_id}, response length: {len(response_text)}")
    
    # Stream the response in chunks
    if response_text:
        # Stream response in character chunks for smoother streaming
        # This provides a better user experience than word-by-word
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i:i + chunk_size]
            yield {'type': 'content', 'content': chunk}
    
    # Post-processing
    messages = [
        *history,
        *new_messages
    ]

    logger.info(f"Updating message history for session {session_id} with {len(messages)} messages")
    await update_message_history(session_id, messages)
