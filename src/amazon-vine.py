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
    logger.addHandler(stream_handler)

def check_and_update_queues(client: VineClient, rfy_list, your_queue_list, vine_for_all_list, priority_terms):
    """
    Checks all item queues, logs new items, and returns the updated lists.
    """
    # Check the RFY list
    rfy_list2 = client.get_list(config.RFY_URL, "Recommended for You")
    if rfy_list2 is not None:
        for item in rfy_list2.copy():
            if item not in rfy_list:
                logging.debug("Found new item in 'Recommended for You': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                if config.DISCORD_WEBHOOK_RFY:
                    send_discord_notification(config.DISCORD_WEBHOOK_RFY, item, "Recommended for You")
                if config.DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(config.DISCORD_WEBHOOK_PRIORITY, item, "PRIORITY: Recommended for You")
        rfy_list = rfy_list2

    # Check Available for All list
    vine_for_all_list2 = client.get_list(config.AFA_URL, "Available for All")
    if vine_for_all_list2 is not None:
        for item in vine_for_all_list2.copy():
            if item not in vine_for_all_list:
                logging.debug("Found new item in 'Available for All': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                if config.DISCORD_WEBHOOK_AFA:
                    send_discord_notification(config.DISCORD_WEBHOOK_AFA, item, "Available for All")
                if config.DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(config.DISCORD_WEBHOOK_PRIORITY, item, "PRIORITY: Available for All")
        vine_for_all_list = vine_for_all_list2

    # Check the Additional Items list
    your_queue_list2 = client.get_full_additional_items_list()
    if your_queue_list2 is not None:
        for item in your_queue_list2.copy():
            if item not in your_queue_list:
                logging.debug("Found new item in 'Additional Items': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                logging.debug("  Search URL: %s", item.queue_url)

                if config.DISCORD_WEBHOOK_AI:
                    send_discord_notification(config.DISCORD_WEBHOOK_AI, item, "Additional Items")
                if config.DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(config.DISCORD_WEBHOOK_PRIORITY, item, f"PRIORITY: Additional Items")

        your_queue_list = your_queue_list2

    save_state(rfy_list, your_queue_list, vine_for_all_list)
    return rfy_list, your_queue_list, vine_for_all_list

def main():
    setup_logging()
    logging.info("Vine Monitor starting up.")
    logging.info("Using browser: %s", config.BROWSER_TYPE)
     
    # Load priority terms
    priority_terms = load_priority_terms()
    if priority_terms:
        logging.info("Loaded %d priority terms.", len(priority_terms))

    if config.DISCORD_WEBHOOK_RFY:
        logging.debug("Discord notifications enabled for Recommended for You and Available for All.")

    if config.DISCORD_WEBHOOK_AI:
        logging.debug("Discord notifications enabled for Additional Items.")

    if config.DISCORD_WEBHOOK_PRIORITY:
        logging.debug("Discord notifications enabled for Priority Items.")

    client = VineClient()
    
    try:
        client.create_browser()
    except NotLoggedInError as e:
        logging.critical("Could not establish initial session: %s", e)
        logging.critical("Please log in to Amazon in your browser and restart the script.")
        sys.exit(1)

    # Try to load previous state
    rfy_list, your_queue_list, vine_for_all_list = load_state()

    if rfy_list is None:  # No state file found or it was empty/invalid
        logging.info("No previous state found. Performing initial scan.")
        rfy_list = client.get_list(config.RFY_URL, "Recommended for You")
        your_queue_list = client.get_full_additional_items_list()
        vine_for_all_list = client.get_list(config.AFA_URL, "Available for all")

        if not rfy_list and not your_queue_list and not vine_for_all_list:
            logging.critical('Cannot get initial item lists on first run. Exiting.')
            sys.exit(1)
        else:
            # Save the initial state so we have a baseline for the next run
            save_state(rfy_list, your_queue_list, vine_for_all_list)
    else:
        logging.info(
            f"Loaded previous state: {len(rfy_list)} RFY, "
            f"{len(your_queue_list)} Additional, {len(vine_for_all_list)} AFA items."
        )

    while True:
        try:
            rfy_list, your_queue_list, vine_for_all_list = check_and_update_queues(
                client, rfy_list, your_queue_list, vine_for_all_list, priority_terms
            )
        except NotLoggedInError as e:
            logging.error("Session expired or login failed: %s", e)
            logging.info("Attempting to re-establish session...")
            while True:
                try:
                    client.create_browser()
                    logging.info("Session re-established successfully.")
                    # After re-establishing, continue to the next main loop iteration
                    break
                except NotLoggedInError as retry_e:
                    logging.error("Failed to re-establish session: %s", retry_e)
                    logging.info("Please log in to Amazon in your browser.")
                    slp_time = random.randint(120, 180)  # Wait between 3 and 5 minutes before retrying
                    logging.info("Retrying in %d seconds...", slp_time)
                    time.sleep(slp_time)  # Wait between 3 and 5 minutes before retrying
            continue  # Go back to the top of the loop to check immediately
        except Exception:
            logging.critical("An unexpected error occurred in the main loop.", exc_info=True)

        wait_seconds = random.randint(240, 400) # Wait between 4 and 6 minutes before the next check
        logging.info("Waiting for %d seconds (%.1f minutes) for the next check.",
                     wait_seconds, wait_seconds / 60.0)
        time.sleep(wait_seconds)

if __name__ == "__main__":
    main()