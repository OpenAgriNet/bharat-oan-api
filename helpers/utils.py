# sva/helpers/utils.py

import os
import re
import copy
from typing import List, Dict, Any
import logging
import boto3
from dotenv import load_dotenv
import base64
import unicodedata as ud
from datetime import datetime, timedelta
import simplejson as json
from jinja2 import Environment, FileSystemLoader
import pytz

load_dotenv()


def get_today_date_str() -> str:
    """Get today's date as a string in the format Monday, 23rd May 2025."""
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist)
    return today.strftime('%A, %d %B %Y')


def get_last_weekday_table() -> str:
    """Return pre-computed 'last [weekday]' dates so the model never has to calculate."""
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist)
    days = [('Mon', 0), ('Tue', 1), ('Wed', 2), ('Thu', 3), ('Fri', 4), ('Sat', 5), ('Sun', 6)]
    entries = []
    for name, target_num in days:
        days_back = (today.weekday() - target_num) % 7
        if days_back == 0:
            days_back = 7
        date = today - timedelta(days=days_back)
        entries.append(f"Last {name}={date.strftime('%d-%m-%Y')}")
    return ' | '.join(entries)


def get_crop_season(dt: datetime = None) -> str:
    """Classify a date into an Indian agricultural season.

    - Kharif (monsoon): June–September (sowing Jun-Jul, harvest Sep-Oct)
    - Rabi (winter): October–February (sowing Oct-Nov, harvest Mar-Apr)
    - Zaid (summer): March–May (short filler season between Rabi and Kharif)

    Args:
        dt: The date to classify. Defaults to current IST date.

    Returns:
        One of 'Kharif (Monsoon)', 'Rabi (Winter)', or 'Zaid (Summer)'.
    """
    if dt is None:
        ist = pytz.timezone('Asia/Kolkata')
        dt = datetime.now(ist)
    month = dt.month
    if 6 <= month <= 9:
        return "Kharif (Monsoon)"
    elif month >= 10 or month <= 2:
        return "Rabi (Winter)"
    else:  # 3, 4, 5
        return "Zaid (Summer)"


def get_logger(name):
    """Get logger object. Does not add a handler so logs propagate to root and are not duplicated."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    return logger


def curl_escape_single_quoted(payload_str: str) -> str:
    """Escape payload for use inside single-quoted bash -d argument (' -> '\\''). Used by TTS and transcription for runnable curl logs."""
    return payload_str.replace("'", "'\\''")


BASE64_LOG_KEYS = {"audioContent", "audio_data", "audio_content"}


def _base64_placeholder(value: str) -> str:
    return f"<base64 len={len(value)}>"


def redact_base64_fields(value: Any, base64_keys: set[str] = BASE64_LOG_KEYS) -> Any:
    """Return a deep-copied value with known base64 audio fields replaced by one-line placeholders."""
    if isinstance(value, dict):
        return {
            key: _base64_placeholder(item) if key in base64_keys and isinstance(item, str)
            else redact_base64_fields(item, base64_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_base64_fields(item, base64_keys) for item in value]
    return copy.deepcopy(value)


def payload_for_log(data: Dict[str, Any], audio_content_key: str = "audioContent") -> Dict[str, Any]:
    """Build a request payload copy safe for logging, replacing audio base64 with placeholders."""
    return redact_base64_fields(data, BASE64_LOG_KEYS | {audio_content_key})


def text_for_log(text: str, max_chars: int = 500) -> str:
    """Redact JSON-ish response text before logging and keep non-JSON text compact."""
    try:
        parsed = json.loads(text)
    except Exception:
        return text[:max_chars]

    redacted = json.dumps(redact_base64_fields(parsed), ensure_ascii=False)
    return redacted[:max_chars]


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


def to_ascii_digits(value: Any) -> str:
    """Convert any Unicode decimal digits (Devanagari, Bengali, Tamil, etc.) to ASCII 0-9.

    `str.isdigit()` returns True for native-script digits too, so code that filters
    with `c.isdigit()` alone silently lets them through unconverted. Use this before
    any digit-only validation/filtering on farmer-provided IDs (OTP, phone, reg/application
    numbers), since the LLM's own script-to-English conversion is a soft prompt instruction
    and not guaranteed.
    """
    if value is None:
        return ""
    out_chars = []
    for ch in str(value):
        try:
            out_chars.append(str(ud.digit(ch)))
        except (TypeError, ValueError):
            out_chars.append(ch)
    return "".join(out_chars)


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



def load_json_data(filename: str) -> List[Dict]:
    """Load JSON data from assets directory.
    
    Args:
        filename (str): Name of the JSON file in the assets directory
        
    Returns:
        List[Dict]: List of dictionaries loaded from the JSON file
    """
    try:
        file_path = os.path.join(os.path.dirname(__file__), "..", "assets", filename)
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger = get_logger(__name__)
        logger.error(f"JSON file not found: {filename}")
        return []
    except json.JSONDecodeError as e:
        logger = get_logger(__name__)
        logger.error(f"Error parsing JSON file {filename}: {e}")
        return 
