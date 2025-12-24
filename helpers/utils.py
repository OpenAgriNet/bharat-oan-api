# sva/helpers/utils.py

import os
import re
from typing import List, Dict
import logging
import boto3
from dotenv import load_dotenv
import base64
import tiktoken
import unicodedata as ud
from datetime import datetime
import simplejson as json
from jinja2 import Environment, FileSystemLoader
import pytz
from copy import deepcopy

load_dotenv()

ENCODER = tiktoken.get_encoding(os.getenv("TIKTOKEN_ENCODING", "cl100k_base"))

def get_today_date_str() -> str:
    """Get today's date as a string in the format Monday, 23rd May 2025."""
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist)
    return today.strftime('%A, %d %B %Y')


def get_logger(name):
    """Get logger object."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger

def count_tokens_str(doc: str) -> int:
    """Count tokens in a string.

    Args:
        doc (str): String to count tokens for.
    Returns:
        int: number of tokens in the string

    """
    return len(ENCODER.encode(doc, disallowed_special=()))


def count_tokens_for_part(part) -> int:
    """Count tokens for a message part, handling different part types appropriately.
    
    Args:
        part: A message part (TextPart, ToolCallPart, etc.)
    Returns:
        int: number of tokens in the part
    """
    if hasattr(part, 'content'):
        return count_tokens_str(str(part.content))
    elif hasattr(part, 'part_kind') and part.part_kind == 'tool-call':
        # For tool calls, create a string representation of the tool name and args
        tool_str = f"tool: {part.tool_name}, args: {json.dumps(part.args)}"
        return count_tokens_str(tool_str)
    elif hasattr(part, 'part_kind') and part.part_kind == 'tool-return':
        # For tool returns, use the result content
        return count_tokens_str(str(part.content))
    else:
        # For unknown part types, return 0 tokens
        return 0



def is_sentence_complete(text: str) -> bool:
    """Check if the text is a complete sentence.
    
    Args:
        text (str): Text to check.

    Returns:
        bool: True if the text is a complete sentence, False otherwise.
    """
    # Check if text ends with a sentence terminator (., !, ?) possibly followed by whitespace or newlines
    return text.endswith('\n')

def split_text(text: str) -> List[str]:
    """Split text into chunks based on newlines.
    
    Args:
        text (str): Text to split.

    Returns:
        list: List of chunks, split by newlines.
    """
    # Split on newlines and filter out empty strings
    chunks = [chunk + "\n" for chunk in text.split('\n')]
    return chunks


def remove_redundant_parenthetical(text: str) -> str:
    """
    Collapse "X (X)" → "X" for any Unicode text.

    * Works with Devanagari and other non-Latin scripts.
    * Keeps bullets, punctuation, spacing, etc. unchanged.
    * Normalises both copies of the term to NFC first so that
      visually-identical strings made of different code-point
      sequences (e.g., decomposed vowel signs) are still caught.
    """
    # Optional but helps when the same glyph can be encoded two ways
    text = ud.normalize("NFC", text)

    pattern = re.compile(
        r'''
        (?P<term>                 # 1st copy
            [^\s()]+              #   – at least one non-space, non-paren char
            (?:\s+[^\s()]+)*      #   – then zero-or-more <space + word>
        )
        \s*                       # spaces before '('
        \(\s*
        (?P=term)                 # identical 2nd copy
        \s*\)                     # closing ')'
        ''',
        flags=re.UNICODE | re.VERBOSE,
    )

    return pattern.sub(lambda m: m.group('term'), text)

def remove_redundant_angle_brackets(text: str) -> str:
    """
    Collapse "X <X>" → "X" for any Unicode text.

    * Works with Devanagari and other non-Latin scripts.
    * Keeps bullets, punctuation, spacing, etc. unchanged.
    * Normalises both copies of the term to NFC first so that
      visually-identical strings made of different code-point
      sequences (e.g., decomposed vowel signs) are still caught.
    """
    # Optional but helps when the same glyph can be encoded two ways
    text = ud.normalize("NFC", text)

    pattern = re.compile(
        r'''
        (?P<term>                 # 1st copy
            [^\s<>]+              #   – at least one non-space, non-angle-bracket char
            (?:\s+[^\s<>]+)*      #   – then zero-or-more <space + word>
        )
        \s*                       # spaces before '<'
        <\s*
        (?P=term)                 # identical 2nd copy
        \s*>                      # closing '>'
        ''',
        flags=re.UNICODE | re.VERBOSE,
    )

    return pattern.sub(lambda m: m.group('term'), text)

def post_process_translation(translation: str) -> str:
    """Post process translation.
    
    Args:
        translation (str): Translation to post process.

    Returns:
        str: Post processed translation.
    """
    # 1. Remove trailing `:` from text from each line
    lines = translation.split('\n')
    processed_lines = [line.rstrip(':') for line in lines]
    translation = '\n'.join(processed_lines)    
    # 2. Remove redundant parentheticals.
    translation = remove_redundant_parenthetical(translation)
    # 3. Remove redundant angle brackets.
    translation = remove_redundant_angle_brackets(translation)
    # 4. Remove double `::`
    translation = re.sub(r'::', ':', translation)
    translation = translation.replace(':**:', ':**')
    return translation



def get_prompt(prompt_file: str, context: Dict = {}, prompt_dir: str = "assets/prompts") -> str:
    """Load a prompt from a file and format it with a context using Jinja2 templating.

    Args:
        prompt_file (str): Name of the prompt file.
        context (dict, optional): Context to format the prompt with. Defaults to {}.
        prompt_dir (str, optional): Path to the prompt directory. Defaults to 'assets/prompts'.

    Returns:
        str: prompt
    """
    # if extension is not .md, add it
    if not prompt_file.endswith(".md"):
        prompt_file += ".md"

    # Create Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(prompt_dir),
        autoescape=False  # We don't want HTML escaping for our prompts
    )

    # Get the template
    template = env.get_template(prompt_file)

    # Render the template with the context
    prompt = template.render(**context) if context else template.render()
    
    return prompt


# Grouped conversation history trimming functions

def group_convos(history: List) -> List[List]:
    """Group messages into conversations. A new conversation starts at each user message.
    
    Args:
        history: List of ModelMessage objects
        
    Returns:
        List of conversation groups, where each group is a list of messages
    """
    convos = []
    current = []

    for msg in history:
        has_user = any(getattr(p, "part_kind", "") == "user-prompt" for p in msg.parts)

        if has_user and current:
            # close previous convo
            convos.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        convos.append(current)

    return convos


def convo_token_usage(convo: List) -> int:
    """Calculate token usage for a conversation by summing usage from response messages.
    
    Args:
        convo: List of ModelMessage objects representing a conversation
        
    Returns:
        Total token count for the conversation
    """
    tokens = 0
    for msg in convo:
        if getattr(msg, "kind", "") == "response" and getattr(msg, "usage", None):
            tokens += msg.usage.total_tokens
    return tokens


def trim_history(
    history: List,
    max_tokens: int = 28_000,
) -> List:
    """Trim message history using grouped conversations approach.
    
    Groups messages into conversations (starting at each user message),
    calculates token usage per conversation from response messages,
    and keeps the first conversation plus as many recent conversations
    as fit within the token limit.
    
    Args:
        history: List of ModelMessage objects
        max_tokens: Maximum number of tokens to keep (default: 28,000)
        
    Returns:
        Trimmed list of ModelMessage objects
    """
    if not history:
        return []

    convos = group_convos(history)
    if not convos:
        return []

    # Build list of (messages, tokens)
    convo_infos = []
    for convo in convos:
        tokens = convo_token_usage(convo)
        convo_infos.append({"messages": convo, "tokens": tokens})

    # Always keep convo 0 (system + first interaction)
    first = convo_infos[0]
    rest = convo_infos[1:]

    total_tokens = first["tokens"]
    selected = []

    # Walk from newest convo backwards
    for info in reversed(rest):
        if total_tokens + info["tokens"] <= max_tokens:
            selected.insert(0, info)  # maintain chronological order
            total_tokens += info["tokens"]
        else:
            break

    final_convos = [first] + selected

    trimmed: List = []
    for info in final_convos:
        trimmed.extend(info["messages"])

    logger = get_logger(__name__)
    logger.info(f"Trimmed history: {total_tokens} tokens (max: {max_tokens})")

    return trimmed
