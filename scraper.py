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
REQUEST_TIMEOUT_SECONDS = 30 # Timeout for network requests (Increased to 30s)
MAX_RETRIES = 3 # Maximum number of retries for failed requests (For catalog fetch)
RETRY_DELAY_SECONDS = 5 # Initial delay before retrying failed requests (For catalog fetch)
MAX_PAGE_RELOADS = 3 # Max reload attempts *per page* within a chapter

# --- Headers ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- Constants for Warning/Error Messages ---
MOBILE_WARNING_TEXT = "手機版頁面由於相容性問題暫不支持電腦端閱讀"
LOAD_FAILURE_TEXT = "內容加載失敗！請重載或更換瀏覽器"
NEXT_PAGE_TEXT = "下一頁"
NEXT_CHAPTER_TEXT = "下一章" # To explicitly identify the end

# --- Selectors ---
# Using XPath for text matching as :has-text() caused issues
# NEXT_BUTTON_SELECTOR = f'a:has-text("{NEXT_PAGE_TEXT}"), button:has_text("{NEXT_PAGE_TEXT}")' # Original problematic selector
NEXT_BUTTON_SELECTOR = f'//a[contains(., "{NEXT_PAGE_TEXT}")] | //button[contains(., "{NEXT_PAGE_TEXT}")]' # XPath version
# Selector for the main content area (adjust if needed based on inspection)
CONTENT_XPATH_SELECTOR = '/html/body/div[1]/div[1]/div/div[2]'


# --- Logging Setup ---
# Change level to DEBUG to see more detailed logs if needed
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
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
def get_chapter_content(page, initial_chapter_url):
    """
    Fetches and extracts the text content of a single chapter using Playwright,
    handling pagination within the chapter.
    """
    logging.info(f"Fetching chapter content using Playwright starting from: {initial_chapter_url}")
    all_content_parts = []
    current_url = initial_chapter_url
    page_num = 1
    chapter_fetch_successful = True # Flag to track if all pages in the chapter were successful

    try:
        # --- Initial Navigation ---
        logging.debug(f"Navigating to initial page: {current_url}")
        # Use increased timeout for navigation
        page.goto(current_url, timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle') # Wait up to 120s

        # --- Pagination Loop ---
        while True:
            logging.info(f"Processing page {page_num} of chapter at URL: {page.url}") # Use page.url as it might change
            page_content = None
            page_fetch_successful = False # Flag for the current page attempt
            page_reload_count = 0

            # --- Page Load/Extraction Loop (with reloads) ---
            while page_reload_count <= MAX_PAGE_RELOADS:
                current_attempt = page_reload_count + 1
                logging.debug(f"Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1} for page {page_num} at {page.url}")
                try:
                    # --- Attempt to close potential overlays/ads ---
                    logging.debug("Attempting to close potential overlays...")
                    # Try common close button patterns (add more specific selectors if identified)
                    # Using XPath for text matching
                    close_buttons = [
                        page.locator('xpath=//button[contains(., "關閉")]'), # XPath for "關閉"
                        page.locator('xpath=//button[contains(., "Close")]'), # XPath for "Close"
                        page.locator('[class*="close"]'), # Keep CSS selectors for class/id
                        page.locator('[id*="close"]')
                        # Add more specific selectors here if you identify them
                    ]
                    for i, button in enumerate(close_buttons):
                        try:
                            # Check if the button exists and is visible within a short timeout
                            if button.first.is_visible(timeout=500): # Quick check
                                logging.info(f"Found potential close button (pattern {i+1}). Clicking it...")
                                button.first.click(timeout=1000) # Click with short timeout
                                time.sleep(0.5) # Brief pause after clicking
                                logging.info("Clicked potential close button.")
                                # Optional: break after finding and clicking one, or try all
                                # break
                        except PlaywrightTimeoutError:
                             # This specific button wasn't visible or click timed out, try next
                             logging.debug(f"Close button pattern {i+1} not visible or timed out.")
                             continue
                        except Exception as e_click:
                             # Handle potential errors during the click itself for this button
                             logging.warning(f"Error clicking close button pattern {i+1}: {e_click}")
                             continue # Try the next button pattern

                except Exception as e:
                    # General error during the overlay closing attempt phase
                    logging.warning(f"Error trying to close overlay: {e}")
                # --- End of overlay closing attempt ---

                # Check for the mobile compatibility warning message before trying to extract
                mobile_warning_locator = page.locator(f'text="{MOBILE_WARNING_TEXT}"')
                try:
                    # Use a short timeout for the warning check
                    if mobile_warning_locator.is_visible(timeout=2000):
                        logging.warning(f"Detected mobile compatibility warning on {page.url}. Reloading page (Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1})...")
                        page_reload_count += 1
                        if page_reload_count > MAX_PAGE_RELOADS:
                            logging.error(f"Max reloads reached for {page.url} after detecting mobile warning.")
                            break # Break inner loop, page fetch failed
                        page.reload(timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle') # Wait up to 120s
                        continue # Retry this page
                    else:
                        logging.debug("Mobile compatibility warning not found.")

                except PlaywrightTimeoutError:
                     logging.debug("Mobile compatibility warning check timed out (warning likely not present).")
                except Exception as e:
                     logging.warning(f"Error checking for warning message: {e}")


                # Try to locate and extract content using the provided XPath
                try:
                    # Use the XPath provided by the user
                    content_locator = page.locator(f'xpath={CONTENT_XPATH_SELECTOR}')
                    logging.info(f"Waiting for element with XPath '{CONTENT_XPATH_SELECTOR}' to be attached (Timeout: {REQUEST_TIMEOUT_SECONDS * 2}s)...")
                    # Wait for the main container element to be in the DOM (Increased timeout)
                    content_locator.wait_for(state='attached', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 2) # Wait up to 60s
                    logging.info(f"Element with XPath '{CONTENT_XPATH_SELECTOR}' is attached. Attempting extraction...")

                    # Attempt 1: Use inner_text() (Increased timeout)
                    logging.debug("Attempting extraction with inner_text() (Timeout: 20s)...")
                    extracted_text = content_locator.inner_text(timeout=20000) # 20s timeout

                    # Attempt 2: Use page.evaluate() with XPath as fallback
                    if not extracted_text or extracted_text.strip() == "":
                        logging.warning(f"XPath '{CONTENT_XPATH_SELECTOR}' inner_text() was empty. Falling back to page.evaluate() with XPath...")
                        extracted_text = page.evaluate(
                            """(xpath) => {
                                const element = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                return element ? element.innerText : null;
                            }""", CONTENT_XPATH_SELECTOR # Pass the xpath selector to the function
                        )
                        if extracted_text:
                             logging.info("Successfully extracted text using page.evaluate() with XPath.")
                        else:
                             logging.warning("page.evaluate() with XPath also returned empty text.")

                    # --- Check for Load Failure Message AFTER extraction attempt ---
                    if extracted_text and LOAD_FAILURE_TEXT in extracted_text:
                        logging.warning(f"Detected load failure message ('{LOAD_FAILURE_TEXT}') in extracted content for {page.url}. Reloading page (Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1})...")
                        page_reload_count += 1
                        if page_reload_count > MAX_PAGE_RELOADS:
                            logging.error(f"Max reloads reached for {page.url} after detecting load failure message.")
                            break # Break inner loop, page fetch failed
                        page.reload(timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle') # Wait up to 120s
                        continue # Retry this page

                    # --- Check if extraction was successful and valid ---
                    elif extracted_text and extracted_text.strip() != "":
                        # Content seems valid
                        lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                        lines = [line for line in lines if MOBILE_WARNING_TEXT not in line and LOAD_FAILURE_TEXT not in line]
                        page_content = "\n\n".join(lines)
                        page_fetch_successful = True # Mark page as successful
                        logging.info(f"Successfully extracted valid content for page {page_num} at {page.url}.")
                        break # Exit the inner retry loop for this page
                    else:
                        # Extracted text was empty or None
                        logging.warning(f"Extracted text is empty for page {page_num} at {page.url} on attempt {current_attempt}.")
                        # Don't increment reload count here, just break inner loop if it's consistently empty
                        # If it breaks here, page_fetch_successful remains False
                        break # Break inner loop, page fetch failed for this attempt cycle

                except PlaywrightTimeoutError as e:
                    logging.warning(f"Playwright timeout during content extraction on attempt {current_attempt}/{MAX_PAGE_RELOADS + 1} for page {page_num} at {page.url}: {e}")
                    page_reload_count += 1
                    if page_reload_count > MAX_PAGE_RELOADS:
                        logging.error(f"Max reloads reached due to content extraction timeout for page {page_num} at {page.url}.")
                        break # Break inner loop, page fetch failed
                    time.sleep(RETRY_DELAY_SECONDS) # Wait before retrying
                    # No 'continue' here, let it fall through to the end of the inner loop check
                except Exception as e:
                    logging.warning(f"Error during content extraction on attempt {current_attempt}/{MAX_PAGE_RELOADS + 1} for page {page_num} at {page.url}: {e}", exc_info=True)
                    page_reload_count += 1
                    if page_reload_count > MAX_PAGE_RELOADS:
                        logging.error(f"Max reloads reached due to content extraction error for page {page_num} at {page.url}.")
                        break # Break inner loop, page fetch failed
                    time.sleep(RETRY_DELAY_SECONDS) # Wait before retrying
                    # No 'continue' here, let it fall through to the end of the inner loop check

            # --- End of Inner Page Load/Extraction Retry Loop ---

            if not page_fetch_successful:
                logging.error(f"Failed to fetch content for page {page_num} at {page.url} after {MAX_PAGE_RELOADS + 1} attempts. Aborting chapter.")
                chapter_fetch_successful = False # Mark the whole chapter as failed
                break # Break the *outer* pagination loop

            # Page fetch was successful, add content
            # --- Add logging before append ---
            logging.debug(f"Page {page_num} fetch successful. Content to append (first 100 chars): '{str(page_content)[:100]}...'") # Ensure page_content is string for slicing
            if page_content and page_content.strip(): # Ensure content is not None or empty/whitespace before appending
                all_content_parts.append(page_content)
                logging.debug(f"Appended content for page {page_num}. Total parts now: {len(all_content_parts)}")
            else:
                logging.warning(f"Page {page_num} fetch reported successful, but page_content is empty/None or whitespace only. Not appending.")


            # --- Check for Next Page Link ---
            logging.debug(f"Checking for '{NEXT_PAGE_TEXT}' button using XPath: {NEXT_BUTTON_SELECTOR}")
            try:
                # Explicitly use xpath=
                next_button = page.locator(f'xpath={NEXT_BUTTON_SELECTOR}').first # Use .first to avoid ambiguity if selector matches multiple

                # Check visibility first (quick check)
                logging.debug("Checking visibility of potential next button...")
                is_visible = next_button.is_visible(timeout=2000) # 2s timeout for visibility check

                if not is_visible:
                    logging.info(f"'{NEXT_PAGE_TEXT}' button XPath found, but element is not visible. Assuming end of chapter (page {page_num}).")
                    break # Exit the outer pagination loop

                # If visible, check text content (still useful to log the exact text)
                logging.debug("Next button is visible. Checking text content...")
                button_text = next_button.text_content(timeout=7000) # Increased timeout to 7s
                logging.debug(f"Potential next button text: '{button_text}'") # Log the actual text found

                # We already selected based on text containing NEXT_PAGE_TEXT with XPath,
                # so if it's visible, we assume it's the correct button.
                # The check below is slightly redundant but acts as a safeguard/confirmation.
                if NEXT_PAGE_TEXT in button_text: # Check if the expected text is part of the found text
                    logging.info(f"Found visible button containing '{NEXT_PAGE_TEXT}'. Clicking to navigate to page {page_num + 1}...")
                    next_button.click(timeout=10000) # 10s timeout for click
                    # Wait for navigation to complete after clicking
                    logging.debug("Waiting for network idle after click...")
                    page.wait_for_load_state('networkidle', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4) # Wait up to 120s
                    logging.debug("Network idle detected. Pausing briefly...")
                    time.sleep(0.5) # Short pause after network idle, just in case
                    page_num += 1
                    continue # Continue to the next iteration of the outer pagination loop
                else:
                    # This case should be less likely now with XPath, but good to keep
                    logging.warning(f"Found visible button via XPath, but text is '{button_text}' (unexpected, doesn't contain '{NEXT_PAGE_TEXT}'). Assuming end of chapter (page {page_num}).")
                    break # Exit the outer pagination loop

            except PlaywrightTimeoutError:
                # This timeout could happen during is_visible, text_content, or click
                logging.info(f"Timeout occurred while checking for or interacting with '{NEXT_PAGE_TEXT}' button (using XPath). Assuming end of chapter (page {page_num}).")
                break # Exit the outer pagination loop
            except Exception as e:
                # Catch other potential errors (e.g., element detached during interaction)
                logging.error(f"Error finding or interacting with '{NEXT_PAGE_TEXT}' button (using XPath): {e}. Assuming end of chapter (page {page_num}).", exc_info=True)
                break # Exit the outer pagination loop

        # --- End of Outer Pagination Loop ---

        # --- Add logging after loop ---
        logging.info(f"Exited pagination loop for chapter {initial_chapter_url}. Total content parts collected: {len(all_content_parts)} for {page_num} pages processed.")


        if not chapter_fetch_successful:
            # If any page failed, the chapter_fetch_successful flag will be False
            logging.error(f"Chapter fetch failed for {initial_chapter_url} due to page errors.")
            return None # Indicate chapter failure

        if not all_content_parts:
             logging.warning(f"Successfully processed pages, but no content parts were collected for chapter starting at {initial_chapter_url}.")
             return None # Treat as failure if no content collected

        # Join all parts and return
        logging.debug(f"Joining {len(all_content_parts)} content parts...")
        full_chapter_content = "\n\n<hr/>\n\n".join(all_content_parts) # Add a separator between page contents
        logging.info(f"Successfully finished processing chapter starting at {initial_chapter_url}.")
        return full_chapter_content.strip()

    except PlaywrightTimeoutError as e:
        logging.error(f"Playwright timeout during initial navigation or critical page transition for chapter {initial_chapter_url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected general error processing chapter {initial_chapter_url} with Playwright: {e}", exc_info=True)
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

                # --- Check if chapter already downloaded ---
                filename = sanitize_filename(title)
                filepath = os.path.join(OUTPUT_DIR, filename)
                if os.path.exists(filepath):
                    logging.info(f"Skipping already downloaded chapter: {filepath}")
                    continue # Move to the next chapter
                # --- End check ---

                # Use the Playwright page object, passing the initial URL
                content = get_chapter_content(page, url) # Function now handles pagination

                # Check if content was successfully retrieved (it might be None if errors occurred)
                if content:
                    # The LOAD_FAILURE_TEXT check should ideally be handled within get_chapter_content per page,
                    # but a final check here doesn't hurt.
                    if LOAD_FAILURE_TEXT in content:
                         logging.error(f"Content for {title} ({url}) still contains load failure message after processing. Skipping save.")
                    else:
                        try:
                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(f"# {title}\n\n") # Add title as header in the file
                                f.write(content)
                            logging.info(f"Successfully saved: {filepath}") # Correctly indented now
                        except IOError as e:
                            logging.error(f"Error writing file {filepath}: {e}")
                        except Exception as e:
                            logging.error(f"An unexpected error occurred while writing file {filepath}: {e}")
                else: # Simplified from elif: Handles cases where content is None or empty
                    logging.warning(f"Skipping chapter due to content fetch/parse error or load failure: {title}")

                # Polite delay (still important)
                logging.debug(f"Waiting for {REQUEST_DELAY_SECONDS} second(s)...")
                time.sleep(REQUEST_DELAY_SECONDS)

            context.close() # Close the context first
            browser.close()
            logging.info("Playwright browser and context closed.")

        except Exception as e:
            logging.error(f"An unexpected error occurred during Playwright processing: {e}", exc_info=True)
            if 'context' in locals() and hasattr(context, 'close') and not context.is_closed():
                 try:
                     context.close() # Ensure context is closed on error
                 except Exception as ce:
                     logging.error(f"Error closing context: {ce}")
            if 'browser' in locals() and browser.is_connected():
                 try:
                     browser.close() # Ensure browser is closed on error
                 except Exception as be:
                     logging.error(f"Error closing browser: {be}")


    logging.info("--- Scraping finished ---")


if __name__ == "__main__":
    main()
