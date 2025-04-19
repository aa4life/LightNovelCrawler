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
    """Fetches and extracts the text content of a single chapter using Playwright."""
    logging.info(f"Fetching chapter content using Playwright from: {chapter_url}")
    try:
        # Navigate to the chapter page
        page.goto(chapter_url, timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 2) # Increased timeout for playwright nav

        # Wait for the specific content element to be present
        # Use a longer timeout here as JS loading might take time
        content_locator = page.locator('#TextContent')
        content_locator.wait_for(state='visible', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 2) # Wait up to 30s

        # Get the HTML content of the div
        content_html = content_locator.inner_html()

        # Parse the extracted HTML with BeautifulSoup
        soup = BeautifulSoup(content_html, 'html.parser')

        # Extract text primarily from <p> tags within the content div
        paragraphs = soup.find_all('p')
        if paragraphs:
            # Join text from all paragraphs, stripping extra whitespace from each
            content = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        else:
            # Fallback: get all text from the div if no <p> tags found
            logging.warning(f"No <p> tags found in content div for {chapter_url}. Using fallback text extraction.")
            # Get text directly from the Playwright locator if soup fails
            content = content_locator.text_content(timeout=5000) # 5s timeout for text extraction
            if content:
                 content = "\n\n".join(line.strip() for line in content.splitlines() if line.strip())


        if not content:
             logging.warning(f"Extracted content is empty for {chapter_url}")
             return None

        # Basic cleaning (less critical now as we target specific div)
        content = re.sub(r'<script.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<style.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)

        return content.strip() # Return cleaned, stripped content

    except PlaywrightTimeoutError:
        logging.error(f"Playwright timeout waiting for content or navigation for {chapter_url}")
        return None
    except Exception as e:
        logging.error(f"Error processing chapter {chapter_url} with Playwright: {e}", exc_info=True)
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
            # headless=True runs without opening a visible browser window
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Add headers to Playwright requests too
            page.set_extra_http_headers(HEADERS)

            logging.info("Playwright browser launched.")

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

            browser.close()
            logging.info("Playwright browser closed.")

        except Exception as e:
            logging.error(f"An unexpected error occurred during Playwright processing: {e}", exc_info=True)
            if 'browser' in locals() and browser.is_connected():
                 browser.close() # Ensure browser is closed on error

    logging.info("--- Scraping finished ---")


if __name__ == "__main__":
    main()
