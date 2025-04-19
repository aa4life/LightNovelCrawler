import requests
from bs4 import BeautifulSoup
import os
import time
import re
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Configuration ---
BASE_URL = "https://tw.linovelib.com"
CATALOG_URL = f"{BASE_URL}/novel/4519/catalog"
OUTPUT_DIR = "novel_chapters"
REQUEST_DELAY_SECONDS = 3 # Increased delay between requests
REQUEST_TIMEOUT_SECONDS = 15 # Timeout for network requests
MAX_RETRIES = 3 # Maximum number of retries for failed requests
RETRY_DELAY_SECONDS = 5 # Initial delay before retrying failed requests

# --- Headers ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def sanitize_filename(filename):
    """Removes characters invalid for filenames and appends .txt."""
    # Remove control characters and characters not allowed in Windows/Linux filenames
    sanitized = re.sub(r'[\x00-\x1f\\/*?:"<>|]', "", filename)
    # Replace sequences of dots or spaces with a single underscore
    sanitized = re.sub(r'[.\s]+', '_', sanitized)
    # Ensure filename is not empty after sanitization
    if not sanitized:
        sanitized = "untitled"
    return sanitized + ".txt"

# Keep requests for catalog fetching, use Playwright for chapter content
def get_chapter_content(page, chapter_url):
    """Fetches and extracts the text content of a single chapter using Playwright, with reload on warning."""
    logging.info(f"Fetching chapter content using Playwright from: {chapter_url}")
    content = None
    MAX_RELOADS = 1 # Allow one reload attempt
    reload_count = 0

    try:
        # Initial navigation - wait for network idle
        page.goto(chapter_url, timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle') # Wait up to 60s

        while reload_count <= MAX_RELOADS:
            # Check for the mobile compatibility warning message
            warning_text = "手機版頁面由於相容性問題暫不支持電腦端閱讀"
            warning_locator = page.locator(f'text="{warning_text}"')
            try:
                # Use a short timeout for the warning check
                if warning_locator.is_visible(timeout=2000): # Check for 2 seconds
                    logging.warning(f"Detected mobile compatibility warning on {chapter_url}. Reloading page (Attempt {reload_count + 1}/{MAX_RELOADS})...")
                    reload_count += 1
                    if reload_count > MAX_RELOADS:
                        logging.error(f"Max reloads reached for {chapter_url} after detecting warning. Skipping.")
                        break
                    # Reload the page - wait for network idle
                    page.reload(timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle') # Wait up to 60s for reload
                    continue # Go back to the start of the loop to check again/extract
                else:
                     logging.debug("Mobile compatibility warning not found.")

            except PlaywrightTimeoutError:
                 logging.debug("Mobile compatibility warning check timed out (warning likely not present).")
            except Exception as e:
                 logging.warning(f"Error checking for warning message: {e}")


            # Try to locate and extract content
            try:
                # Wait for the first paragraph inside #TextContent as a signal
                content_paragraph_locator = page.locator('#TextContent p')
                logging.info(f"Waiting for first paragraph inside #TextContent ('#TextContent p') to be attached...")
                # Wait for the first paragraph element to be in the DOM
                content_paragraph_locator.first.wait_for(state='attached', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4) # Wait up to 60s
                logging.info(f"First paragraph is attached. Extracting inner text from #TextContent...")

                # Now that a paragraph exists, try extracting text from the parent container
                content_container_locator = page.locator('#TextContent')
                extracted_text = content_container_locator.inner_text(timeout=10000) # 10s timeout for text extraction

                if extracted_text:
                    # Basic processing: join lines, remove extra whitespace
                    lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                    # Filter out potential leftover warning lines if needed, though inner_text should be specific
                    lines = [line for line in lines if warning_text not in line]
                    content = "\n\n".join(lines)
                    logging.info(f"Successfully extracted content for {chapter_url}.")
                    break # Exit the loop on successful extraction
                else:
                    logging.warning(f"Extracted text from #TextContent is empty for {chapter_url}.")
                    # Decide if you want to retry or break here. Let's break.
                    break

            except PlaywrightTimeoutError:
                logging.error(f"Playwright timeout waiting for #TextContent or extracting text for {chapter_url}.")
                # Break the loop on timeout, no point retrying immediately
                break
            except Exception as e:
                logging.error(f"Error locating/extracting content from #TextContent: {e}", exc_info=True)
                # Break the loop on other errors
                break

        # --- End of while loop ---

        if not content:
             logging.warning(f"Failed to extract content for {chapter_url} after {reload_count} reloads.")
             return None

        # No need for BeautifulSoup parsing or further regex cleaning if inner_text worked well
        return content.strip()

    except PlaywrightTimeoutError:
        logging.error(f"Playwright timeout during initial navigation or reload for {chapter_url}")
        return None
    except Exception as e:
        logging.error(f"General error processing chapter {chapter_url} with Playwright: {e}", exc_info=True)
        return None


def get_chapter_links(catalog_soup):
    """Extracts chapter links starting from '第1章' from the parsed catalog page."""
    chapter_links = []
    # Find all links within the catalog page that potentially link to chapters
    # The relevant links have hrefs like /novel/4519/xxxxx.html
    all_links = catalog_soup.find_all('a', href=re.compile(r'^/novel/4519/\d+\.html$'))

    start_collecting = False
    for link in all_links:
        title = link.get_text(strip=True)
        href = link.get('href')

        # Use regex to robustly check for "第X章" format
        is_chapter_link = re.match(r'^第\s*\d+\s*章', title)

        if not start_collecting and re.match(r'^第\s*1\s*章', title):
            start_collecting = True
            logging.info("Found starting chapter (第1章).")

        if start_collecting and is_chapter_link and href:
            full_url = BASE_URL + href
            chapter_links.append({'title': title, 'url': full_url})
            logging.debug(f"Collected chapter link: {title} ({full_url})")
        elif start_collecting and title and href and not is_chapter_link:
             # Log if we encounter a link after starting that doesn't match the chapter pattern
             logging.debug(f"Skipping non-chapter link after start: {title} ({href})")

    if not chapter_links:
         logging.warning("No chapter links found starting from '第1章'. Check catalog page structure.")

    return chapter_links


def main():
    """Main function to orchestrate the scraping process."""
    logging.info(f"Starting scraper for catalog: {CATALOG_URL}")

    # --- Fetch Catalog using Requests (usually faster and less resource intensive) ---
    try:
        logging.info("Fetching catalog page using Requests...")
        response = requests.get(CATALOG_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
        logging.info("Catalog page fetched successfully.")
        catalog_soup = BeautifulSoup(response.text, 'html.parser')
        chapter_links = get_chapter_links(catalog_soup)

        if not chapter_links:
            logging.error("Failed to find any valid chapter links from catalog. Exiting.")
            return

        logging.info(f"Found {len(chapter_links)} chapters to download.")

    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred while fetching the catalog page: {CATALOG_URL}")
        return
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching catalog page {CATALOG_URL}: {e}")
        return
    except Exception as e:
        logging.error(f"An unexpected error occurred during catalog fetching: {e}", exc_info=True)
        return

    # --- Create output directory ---
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logging.info(f"Output directory '{OUTPUT_DIR}' ensured.")
    except OSError as e:
        logging.error(f"Failed to create output directory '{OUTPUT_DIR}': {e}")
        return

    # --- Process Chapters using Playwright ---
    with sync_playwright() as p:
        try:
            # Launch browser (consider chromium, firefox, or webkit)
            browser = p.chromium.launch(headless=False) # Set to False to see the browser window

            # Define mobile device emulation
            context = browser.new_context(**p.devices['Pixel 5']) # Emulate Pixel 5
            page = context.new_page()
            # No need to set extra headers manually, device emulation handles User-Agent

            logging.info("Playwright browser launched with mobile emulation (Pixel 5).")

            # Process each chapter
            for i, chapter in enumerate(chapter_links):
                title = chapter['title']
                url = chapter['url']
                logging.info(f"--- Processing chapter {i+1}/{len(chapter_links)}: {title} ---")

                # Use the Playwright page object
                content = get_chapter_content(page, url)

                if content:
                    filename = sanitize_filename(title)
                    filepath = os.path.join(OUTPUT_DIR, filename)
                    try:
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(f"# {title}\n\n") # Add title as header in the file
                            f.write(content)
                        logging.info(f"Successfully saved: {filepath}")
                    except IOError as e:
                        logging.error(f"Error writing file {filepath}: {e}")
                    except Exception as e:
                        logging.error(f"An unexpected error occurred while writing file {filepath}: {e}")
                else:
                    logging.warning(f"Skipping chapter due to content fetch/parse error: {title}")

                # Polite delay (still important)
                logging.debug(f"Waiting for {REQUEST_DELAY_SECONDS} second(s)...")
                time.sleep(REQUEST_DELAY_SECONDS)

            context.close() # Close the context first
            browser.close()
            logging.info("Playwright browser and context closed.")

        except Exception as e:
            logging.error(f"An unexpected error occurred during Playwright processing: {e}", exc_info=True)
            if 'context' in locals():
                 context.close() # Ensure context is closed on error
            if 'browser' in locals() and browser.is_connected():
                 browser.close() # Ensure browser is closed on error

    logging.info("--- Scraping finished ---")


if __name__ == "__main__":
    main()
