import sys
import re
import time
import os
import logging
import copy
import json
import dataclasses
import datetime
import random
from dataclasses import dataclass
from typing import Set, Tuple, Optional
from typing_extensions import Final
import urllib.request
import urllib.error
import urllib.parse
# import webbrowser # This is now only used inside a function, can be moved for clarity

import http.cookiejar

import browsercookie
import bs4
import fake_useragent
import mechanize

import getpass
from optparse import OptionParser


INITIAL_PAGE: Final = 'https://www.amazon.co.uk/vine/'
RFY_URL: Final = 'https://www.amazon.co.uk/vine/vine-items?queue=potluck'
ADDITIONAL_ITEMS_URL: Final = 'https://www.amazon.co.uk/vine/vine-items?queue=encore'
AFA_URL: Final = 'https://www.amazon.co.uk/vine/vine-items?queue=last_chance'
STATE_FILE: Final = 'vine_monitor_state.json'
USER_AGENT: Final[str] = fake_useragent.UserAgent().ff

# Discord Webhooks
DISCORD_WEBHOOK_RFY: Final = "https://discord.com/api/webhooks/1397327066713559161/-AUIU28c1GtV3M9tBwMjzRWC-T7uw373fdOuyqVgtuUg61ocvLWWddGWazxzUf6Kech5"   # Recommended for You and Available for all]
DISCORD_WEBHOOK_AFA: Final = "https://discord.com/api/webhooks/1399343029092876308/oE_E4zafV-Lwbm6K8opVvY8QCNFgyoMbyV1x-39WmfSyh53LIPd_hkqI94DM1v30FO6r"
DISCORD_WEBHOOK_AI: Final = "https://discord.com/api/webhooks/1397333337105760346/7OftTTqDWUbG0f1FpoS88vGg9kdknYqAoaSz_mEDDwVPZ8j3b2MY6nL3tS5aegvY9Npn"   # Additional Items
DISCORD_WEBHOOK_PRIORITY: Final = "https://discord.com/api/webhooks/1399343172437413938/EZ-qn-gP_ka5jPaiheMHu_ubFPNXQScC3DUFbIjl7EtZahuPeNLIasDf1puL6AayFl2e"  # Priority Items - SET YOUR WEBHOOK URL HERE
PRIORITY_TERMS_FILE: Final = 'priority_terms.json'

@dataclass(frozen=True, eq=True)
class VineItem:
    """A class to hold information about a Vine item."""
    asin: str
    title: str
    url: str
    image_url: str
    queue_url: str

class NotLoggedInError(Exception):
    """Custom exception for when the session is no longer valid."""
    pass

def save_state(rfy_list: Set[VineItem], queue_list: Set[VineItem], afa_list: Set[VineItem]):
    """Saves the current sets of items to a JSON file."""
    logging.info("Saving current state to %s", STATE_FILE)
    try:
        state = {
            'rfy_list': [dataclasses.asdict(item) for item in rfy_list],
            'your_queue_list': [dataclasses.asdict(item) for item in queue_list],
            'vine_for_all_list': [dataclasses.asdict(item) for item in afa_list],
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logging.error("Could not save state to %s: %s", STATE_FILE, e)

def load_state() -> Tuple[Optional[Set[VineItem]], Optional[Set[VineItem]], Optional[Set[VineItem]]]:
    """Loads item sets from the JSON state file if it exists."""
    if not os.path.exists(STATE_FILE):
        logging.info("State file not found. Starting fresh.")
        return None, None, None

    logging.info("Loading previous state from %s", STATE_FILE)
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            rfy_list = {VineItem(**item) for item in state.get('rfy_list', [])}
            queue_list = {VineItem(**item) for item in state.get('your_queue_list', [])}
            afa_list = {VineItem(**item) for item in state.get('vine_for_all_list', [])}
            return rfy_list, queue_list, afa_list
    except (json.JSONDecodeError, TypeError) as e:
        logging.error("Could not load or parse state file %s: %s", STATE_FILE, e)
        logging.error("Starting with a fresh state.")
        return None, None, None

def load_priority_terms() -> Set[str]:
    """Loads priority search terms from the JSON file."""
    if not os.path.exists(PRIORITY_TERMS_FILE):
        logging.warning("Priority terms file not found: %s. No priority matching will occur.", PRIORITY_TERMS_FILE)
        try:
            with open(PRIORITY_TERMS_FILE, 'w', encoding='utf-8') as f:
                json.dump({"terms": ["example term 1", "example phrase 2"]}, f, indent=4)
            logging.info("Created a sample priority_terms.json file to guide you.")
        except Exception as e: 
            logging.error("Could not create sample priority terms file: %s", e)
        return set()

    logging.info("Loading priority terms from %s", PRIORITY_TERMS_FILE)
    try:
        with open(PRIORITY_TERMS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            terms = data.get('terms', [])
            if not isinstance(terms, list):
                logging.error("Priority terms file is malformed: 'terms' should be a list.")
                return set()
            # Return terms as a set of lowercased strings for case-insensitive matching.
            return {term.lower() for term in terms if isinstance(term, str)}
    except (json.JSONDecodeError, TypeError) as e:
        logging.error("Could not load or parse priority terms file %s: %s", PRIORITY_TERMS_FILE, e)
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

def send_discord_notification(webhook_url, item, queue_name):
    """Sends a notification to a Discord webhook using an embed."""
    logging.info("Sending Discord notification for: %s", item.title)

    # Use a placeholder if the title is empty, as Discord requires a non-empty title.vp
    notification_title = item.title if item.title else f"New Item (ASIN: {item.asin})"
    try:
        data = {
            "embeds": [
                {
                    "title": notification_title,
                    "url": item.url,
                    "description": f"<@312951812401659905> - New item found in **{queue_name}**!",
                    "color": 5814783,  # Hex color #58D68D (a nice green)
                    "thumbnail": {"url": item.image_url},
                    "fields": [
                        {"name": "QUEUE URL", "value": item.queue_url, "inline": True},                         
                    ],
                    "footer": {"text": "Vine Monitor"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            ]
        }
        payload = json.dumps(data).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': USER_AGENT
        }
        req = urllib.request.Request(webhook_url, data=payload, headers=headers)
        with urllib.request.urlopen(req) as response:
            if response.status not in [200, 204]:
                logging.error("Discord webhook failed with status: %d", response.status)
                time.sleep(2)
            else:
                time.sleep(2)  # Wait a bit to avoid hitting rate limits
    except Exception as e:
        logging.error("Failed to send Discord notification: %s", e)

def setup_logging():
    """Configure logging to file and console."""
    # Using basicConfig for simplicity. For more complex needs, you could
    # create logger objects and add handlers manually.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("vine_monitor.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )

def create_browser() -> mechanize.Browser:
    browser = mechanize.Browser()
    firefox = getattr(browsercookie, OPTIONS.browser)()

    # Create a new cookie jar for Mechanize
    cj = http.cookiejar.CookieJar()
    for cookie in firefox:
        cj.set_cookie(copy.copy(cookie))
    browser.set_cookiejar(cj)

    # Necessary for Amazon.com
    browser.set_handle_robots(False)
    browser.addheaders = [('User-agent', USER_AGENT)]

    try:
        logging.info('Connecting to Amazon Vine...')
        response = browser.open(INITIAL_PAGE)
        if "ap/signin" in response.geturl():
            raise NotLoggedInError("Redirected to sign-in page on initial connection")
        html = response.read()

        # Are we already logged in?
        if b'Vine Help' in html:
            logging.info("Successfully logged in with a browser cookie.")
            return browser

        raise NotLoggedInError('Could not log in with a cookie. "Vine Help" not found.')
    except urllib.error.HTTPError as e:
        raise NotLoggedInError(f"HTTP Error during login: {e}") from e
    except urllib.error.URLError as e:
        raise NotLoggedInError(f"URL Error during login: {e}") from e
    except Exception as e:
        logging.critical("An unexpected error occurred during login.", exc_info=True)
        # Re-raise as NotLoggedInError to be handled by the main loop
        raise NotLoggedInError("An unexpected error occurred during login.") from e


def download_vine_page(br, url, name=None):
    if name:
        logging.info("Checking %s...", name)
    try:
        logging.debug("Downloading page: %s", url)
        response = br.open(url)
        # Check if we've been redirected to a login page
        if "ap/signin" in response.geturl():
            raise NotLoggedInError(f"Redirected to sign-in page when accessing {url}")
        html = response.read()
        logging.debug("Parsing page...")
        return bs4.BeautifulSoup(html, features="lxml")
    except mechanize.HTTPError as e:
        # Some HTTP errors might also indicate a login issue
        if e.code in {401, 403, 404}: # Unauthorized, Forbidden, or Not Found
            logging.warning("Received HTTP %d for %s. Assuming session expired.", e.code, url)
            raise NotLoggedInError(f"HTTP {e.code} error") from e
        logging.error("Failed to download or parse page %s: %s", url, e)
        return None
    except NotLoggedInError:
        raise  # Propagate login errors to the main recovery loop
    except Exception as e:
        logging.error("Failed to download or parse page %s: %s", url, e)
        return None



def get_list(br, url, name) -> Optional[Set[VineItem]]:
    soup = download_vine_page(br, url, name)
    if not soup:
        logging.error("Could not get soup object for %s, returning None.", name)
        return None

    items: Set[VineItem] = set()
    base_url = "https://www.amazon.co.uk"

    for tile in soup.select("div.vvp-item-tile"):
        asin_element = tile.select_one("input[data-asin]")
        asin = asin_element['data-asin'] if asin_element else None

        link_element = tile.select_one("a.a-link-normal")
        relative_url = link_element['href'] if link_element else None
        full_url = urllib.parse.urljoin(base_url, relative_url) if relative_url else "URL_NOT_FOUND"

        img_element = tile.select_one("img")
        img_url = img_element['src'] if img_element else "IMG_NOT_FOUND"
        q_url = None

        # The title is inside a specific span. The 'a-offscreen' class might be
        # dynamically added, so we look for the more stable 'a-truncate-full'.
        title_element = tile.select_one("span.a-truncate-full")
        if title_element:
            title = title_element.text.strip()
        else:
            # Fallback to the image alt text if the span isn't found.
            title = (img_element['alt'].strip() if img_element and 'alt' in img_element.attrs else "TITLE_NOT_FOUND")

        if not all([asin, relative_url]):
            logging.warning("Could not parse a tile completely in %s. Tile: %s", name, tile)
            continue
        
        if name == "Recommended for You":
            q_url = RFY_URL
        elif name == "Available for All":
            q_url = AFA_URL
        else:
            if title and title != "TITLE_NOT_FOUND":
                        search_words = title.split()[:3]
                        search_term = ' '.join(search_words)
                        q_url = (
                            "https://www.amazon.co.uk/vine/vine-items?search=" +
                            urllib.parse.quote_plus(search_term)
                        )
            else:
                q_url = ADDITIONAL_ITEMS_URL


        # Create the VineItem object
        item = VineItem(
            asin=asin,
            title=title,
            url=full_url,
            image_url=img_url,
            queue_url=q_url
        )
        
        if any(existing_item.asin == item.asin for existing_item in items):
            logging.warning('Duplicate in-stock item found in %s: %s', name, item.asin)
        items.add(item)

    logging.info('Found %u in-stock items in %s.', len(items), name)
    return items


def open_product_page(br, item: VineItem) -> bool:
    import webbrowser
    logging.debug("Attempting to open product page for ASIN: %s", item.asin)
    # We already have the URL, but we can re-download to check for tax value or validity
    soup = download_vine_page(br, item.url)
    # Make sure we don't get a 404 or some other error
    if soup:
        logging.debug('New item found: %s - %s', item.asin, item.title)
        # Display how much tax it costs
        # This part might need updating based on the new product page structure
        # For now, we just open the page.
        webbrowser.open_new_tab(item.url)
        time.sleep(1)
        return True
    else:
        logging.warning('Invalid item page or error for ASIN: %s', item.asin)
        return False

def get_full_additional_items_list(browser):
    """Fetches all pages for the 'Additional Items' queue and aggregates them."""
    full_list = set()
    any_page_fetched = False
    for page_num in range(1, 6):
        if page_num == 1:
            page_url = ADDITIONAL_ITEMS_URL
        else:
            page_url = f"{ADDITIONAL_ITEMS_URL}&pn=&cn=&page={page_num}"

        page_items = get_list(browser, page_url, f"Additional Items (Page {page_num})")
        if page_items is not None:
            any_page_fetched = True
            full_list.update(page_items)
        else:
            logging.warning("Could not retrieve Additional Items page %d, skipping.", page_num)

    # Return the list if any page was fetched, otherwise return None to indicate failure.
    return full_list if any_page_fetched else None


def check_and_update_queues(browser, rfy_list, your_queue_list, vine_for_all_list, priority_terms):
    """
    Checks all item queues, logs new items, and returns the updated lists.
    """
    # Check the RFY list
    rfy_list2 = get_list(browser, RFY_URL, "Recommended for You")
    if rfy_list2 is not None:
        for item in rfy_list2.copy():
            if item not in rfy_list:
                logging.debug("Found new item in 'Recommended for You': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                if DISCORD_WEBHOOK_RFY:
                    send_discord_notification(DISCORD_WEBHOOK_RFY, item, "Recommended for You")
                if DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(DISCORD_WEBHOOK_PRIORITY, item, "PRIORITY: Recommended for You")
        rfy_list = rfy_list2

    # Check Available for All list
    vine_for_all_list2 = get_list(browser, AFA_URL, "Available for All")
    if vine_for_all_list2 is not None:
        for item in vine_for_all_list2.copy():
            if item not in vine_for_all_list:
                logging.debug("Found new item in 'Available for All': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                if DISCORD_WEBHOOK_AFA:
                    send_discord_notification(DISCORD_WEBHOOK_AFA, item, "Available for All")
                if DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(DISCORD_WEBHOOK_PRIORITY, item, "PRIORITY: Available for All")
        vine_for_all_list = vine_for_all_list2

    # Check the Additional Items list
    your_queue_list2 = get_full_additional_items_list(browser)
    if your_queue_list2 is not None:
        for item in your_queue_list2.copy():
            if item not in your_queue_list:
                logging.debug("Found new item in 'Additional Items': %s", item.title)
                logging.debug("  ASIN: %s", item.asin)
                logging.debug("  URL: %s", item.url)
                logging.debug("  Image: %s", item.image_url)
                logging.debug("  Search URL: %s", item.queue_url)

                if DISCORD_WEBHOOK_AI:
                    send_discord_notification(DISCORD_WEBHOOK_AI, item, "Additional Items")
                if DISCORD_WEBHOOK_PRIORITY and check_for_priority_match(item, priority_terms):
                    send_discord_notification(DISCORD_WEBHOOK_PRIORITY, item, f"PRIORITY: Additional Items")

        your_queue_list = your_queue_list2

    save_state(rfy_list, your_queue_list, vine_for_all_list)
    return rfy_list, your_queue_list, vine_for_all_list


parser = OptionParser(usage="usage: %prog [options]")
parser.add_option('--browser', dest='browser',
                  help='Which browser to use ("firefox" or "chrome") from which to load the session cookies (default is "%default")',
                  type="string", default='firefox')

(OPTIONS, _args) = parser.parse_args()

setup_logging()
logging.info("Vine Monitor starting up.")
logging.info("Using browser: %s", OPTIONS.browser)
 
# Load priority terms
priority_terms = load_priority_terms()
if priority_terms:
    logging.info("Loaded %d priority terms.", len(priority_terms))

if DISCORD_WEBHOOK_RFY:
    logging.debug("Discord notifications enabled for Recommended for You and Available for All.")

if DISCORD_WEBHOOK_AI:
    logging.debug("Discord notifications enabled for Additional Items.")

if DISCORD_WEBHOOK_PRIORITY:
    logging.debug("Discord notifications enabled for Priority Items.")

try:
    BROWSER = create_browser()
except NotLoggedInError as e:
    logging.critical("Could not establish initial session: %s", e)
    logging.critical("Please log in to Amazon in your browser and restart the script.")
    sys.exit(1)

# Try to load previous state
rfy_list, your_queue_list, vine_for_all_list = load_state()

if rfy_list is None:  # No state file found or it was empty/invalid
    logging.info("No previous state found. Performing initial scan.")
    rfy_list = get_list(BROWSER, RFY_URL, "Recommended for You")
    your_queue_list = get_full_additional_items_list(BROWSER)
    vine_for_all_list = get_list(BROWSER, AFA_URL, "Available for all")

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
            BROWSER, rfy_list, your_queue_list, vine_for_all_list, priority_terms
        )
    except NotLoggedInError as e:
        logging.error("Session expired or login failed: %s", e)
        logging.info("Attempting to re-establish session...")
        while True:
            try:
                BROWSER = create_browser()
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