import yaml
import os

import asyncio
import requests
import time

from tavily import TavilyClient, AsyncTavilyClient
from duckduckgo_search import DDGS
from langsmith import traceable

from dotenv import load_dotenv, find_dotenv

# Load the API keys from .env
load_dotenv(find_dotenv(), override=True)


def load_config(file_path=os.getenv("CONFIG_FILEPATH")):
    """Load configuration from a YAML file."""
    with open(file_path, "r") as file:
        config = yaml.safe_load(file)
    return config
