import logging
import copy
import time
import random
import urllib.parse
import urllib.error
import webbrowser
from typing import Optional, Set

import mechanize
import browsercookie
import bs4
import http.cookiejar

from config import config
from models import VineItem

class NotLoggedInError(Exception):
    """Custom exception for when the session is no longer valid."""
    pass

class VineClient:
    def __init__(self):
        self.browser = None

    def create_browser(self) -> mechanize.Browser:
        browser = mechanize.Browser()
        # Use the browser specified in config
        try:
            firefox = getattr(browsercookie, config.BROWSER_TYPE)()
        except AttributeError:
             logging.error(f"Browser type '{config.BROWSER_TYPE}' not supported by browsercookie. Defaulting to firefox.")
             firefox = browsercookie.firefox()
        except Exception as e:
             logging.error(f"Error loading cookies from {config.BROWSER_TYPE}: {e}")
             raise

        # Create a new cookie jar for Mechanize
        cj = http.cookiejar.CookieJar()
        for cookie in firefox:
            cj.set_cookie(copy.copy(cookie))
        browser.set_cookiejar(cj)

        # Necessary for Amazon.com
        browser.set_handle_robots(False)
        browser.addheaders = [('User-agent', config.USER_AGENT)]

        try:
            logging.info('Connecting to Amazon Vine...')
            response = browser.open(config.INITIAL_PAGE)
            if "ap/signin" in response.geturl():
                raise NotLoggedInError("Redirected to sign-in page on initial connection")
            html = response.read()

            # Are we already logged in?
            if b'Vine Help' in html:
                logging.info("Successfully logged in with a browser cookie.")
                self.browser = browser
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

    def download_vine_page(self, url, name=None):
        if not self.browser:
             raise NotLoggedInError("Browser not initialized.")
             
        if name:
            logging.info("Checking %s...", name)
        try:
            logging.debug("Downloading page: %s", url)
            response = self.browser.open(url)
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

    def get_list(self, url, name) -> Optional[Set[VineItem]]:
        soup = self.download_vine_page(url, name)
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
                q_url = config.RFY_URL
            elif name == "Available for All":
                q_url = config.AFA_URL
            else:
                if title and title != "TITLE_NOT_FOUND":
                            search_words = title.split()[:3]
                            search_term = ' '.join(search_words)
                            q_url = (
                                "https://www.amazon.co.uk/vine/vine-items?search=" +
                                urllib.parse.quote_plus(search_term)
                            )
                else:
                    q_url = config.ADDITIONAL_ITEMS_URL


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

    def get_full_additional_items_list(self):
        """Fetches all pages for the 'Additional Items' queue and aggregates them."""
        full_list = set()
        any_page_fetched = False
        for page_num in range(1, 6):
            if page_num == 1:
                page_url = config.ADDITIONAL_ITEMS_URL
            else:
                # Sleep between pages to avoid burst detection
                time.sleep(random.uniform(2, 4))
                page_url = f"{config.ADDITIONAL_ITEMS_URL}&pn=&cn=&page={page_num}"

            page_items = self.get_list(page_url, f"Additional Items (Page {page_num})")
            if page_items is not None:
                any_page_fetched = True
                full_list.update(page_items)
            else:
                logging.warning("Could not retrieve Additional Items page %d, skipping.", page_num)

        # Return the list if any page was fetched, otherwise return None to indicate failure.
        return full_list if any_page_fetched else None

    def open_product_page(self, item: VineItem) -> bool:
        logging.debug("Attempting to open product page for ASIN: %s", item.asin)
        # We already have the URL, but we can re-download to check for tax value or validity
        # Note: In the original code, this re-downloaded the page.
        # We will keep that logic but use our internal method.
        try:
            soup = self.download_vine_page(item.url)
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
        except Exception as e:
             logging.error(f"Error opening product page: {e}")
             return False
