"""
Tasks for logging operations.
"""
from fastapi import BackgroundTasks
from helpers.utils import get_logger
from dotenv import load_dotenv
from datetime import datetime
from typing import Optional

load_dotenv()

logger = get_logger(__name__)
