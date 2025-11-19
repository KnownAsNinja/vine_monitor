import sys
import time
import os
import logging
import logging.handlers
import json
import random
import dataclasses
from typing import Set, Tuple, Optional

from config import config
from models import VineItem
from notifications import send_discord_notification
from vine_client import VineClient, NotLoggedInError

def save_state(rfy_list: Set[VineItem], queue_list: Set[VineItem], afa_list: Set[VineItem]):
    """Saves the current sets of items to a JSON file."""
    logging.info("Saving current state to %s", config.STATE_FILE)
    try:
        state = {
            'rfy_list': [dataclasses.asdict(item) for item in rfy_list],
            'your_queue_list': [dataclasses.asdict(item) for item in queue_list],
            'vine_for_all_list': [dataclasses.asdict(item) for item in afa_list],
        }
        with open(config.STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logging.error("Could not save state to %s: %s", config.STATE_FILE, e)

def load_state() -> Tuple[Optional[Set[VineItem]], Optional[Set[VineItem]], Optional[Set[VineItem]]]:
    """Loads item sets from the JSON state file if it exists."""
    if not os.path.exists(config.STATE_FILE):
        logging.info("State file not found. Starting fresh.")
        return None, None, None

    logging.info("Loading previous state from %s", config.STATE_FILE)
    try:
        with open(config.STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            rfy_list = {VineItem(**item) for item in state.get('rfy_list', [])}
            queue_list = {VineItem(**item) for item in state.get('your_queue_list', [])}
            afa_list = {VineItem(**item) for item in state.get('vine_for_all_list', [])}
            return rfy_list, queue_list, afa_list
    except (json.JSONDecodeError, TypeError) as e:
        logging.error("Could not load or parse state file %s: %s", config.STATE_FILE, e)
        logging.error("Starting with a fresh state.")
        return None, None, None

def load_priority_terms() -> Set[str]:
    """Loads priority search terms from the JSON file."""
    if not os.path.exists(config.PRIORITY_TERMS_FILE):
        logging.warning("Priority terms file not found: %s. No priority matching will occur.", config.PRIORITY_TERMS_FILE)
        try:
            with open(config.PRIORITY_TERMS_FILE, 'w', encoding='utf-8') as f:
                json.dump({"terms": ["example term 1", "example phrase 2"]}, f, indent=4)
            logging.info("Created a sample priority_terms.json file to guide you.")
        except Exception as e: 
            logging.error("Could not create sample priority terms file: %s", e)
        return set()

    logging.info("Loading priority terms from %s", config.PRIORITY_TERMS_FILE)
    try:
        with open(config.PRIORITY_TERMS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            terms = data.get('terms', [])
            if not isinstance(terms, list):
                logging.error("Priority terms file is malformed: 'terms' should be a list.")
                return set()
            # Return terms as a set of lowercased strings for case-insensitive matching.
            return {term.lower() for term in terms if isinstance(term, str)}
    except (json.JSONDecodeError, TypeError) as e:
        logging.error("Could not load or parse priority terms file %s: %s", config.PRIORITY_TERMS_FILE, e)
        return set()

def check_for_priority_match(item: VineItem, priority_terms: Set[str]) -> bool:
    """
    Checks if an item's title contains ALL of the words from any single
    priority term (case-insensitive).
    """
    if not priority_terms or not item.title:
        return False

    # Create a set of unique words from the item's title for efficient lookup
    item_title_words = set(item.title.lower().split())

    # Check each priority phrase
    for phrase in priority_terms:
        # Create a set of words for the current priority phrase
        phrase_words = set(phrase.lower().split())

        # Check if all words in the priority phrase are present in the title
        if phrase_words.issubset(item_title_words):
            logging.info("Priority match found for '%s' on words from '%s'", item.title, phrase)
            return True
    return False

def setup_logging():
    """Configure logging to file and console with rotation."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # File Handler with Rotation
    # Max size 5MB, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=5*1024*1024, backupCount=3
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console Handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
     
    # Load priority terms
    priority_terms = load_priority_terms()
    if priority_terms:
        logging.info("Loaded %d priority terms.", len(priority_terms))

    if config.DISCORD_WEBHOOK_RFY:

        wait_seconds = random.randint(240, 400) # Wait between 4 and 6 minutes before the next check
        logging.info("Waiting for %d seconds (%.1f minutes) for the next check.",
                     wait_seconds, wait_seconds / 60.0)
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()