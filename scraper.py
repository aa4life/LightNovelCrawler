import requests
from bs4 import BeautifulSoup
import os
import time
import re
import logging
import argparse
import random # Added for randomized delays
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Configuration ---
BASE_URL = "https://tw.linovelib.com"
OUTPUT_DIR_BASE = "novel_chapters" # Base directory for all novels
REQUEST_DELAY_SECONDS = 10 # Increased delay between chapter requests (was 5)
REQUEST_TIMEOUT_SECONDS = 45 # Timeout for network requests
MAX_RETRIES = 3 # Max retries for catalog/volume fetch
RETRY_DELAY_SECONDS = 5 # Delay before retrying failed catalog/volume fetch
MAX_PAGE_RELOADS = 3 # Max reload attempts per chapter page

# --- Headers ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- Constants for Warning/Error Messages ---
MOBILE_WARNING_TEXT = "手機版頁面由於相容性問題暫不支持電腦端閱讀"
LOAD_FAILURE_TEXT = "內容加載失敗！請重載或更換瀏覽器"
NEXT_PAGE_TEXT = "下一頁"

# --- Selectors ---
NEXT_BUTTON_SELECTOR = f'//a[contains(., "{NEXT_PAGE_TEXT}")] | //button[contains(., "{NEXT_PAGE_TEXT}")]' # XPath version
CONTENT_XPATH_SELECTOR = '/html/body/div[1]/div[1]/div/div[2]' # Main content area
NOVEL_TITLE_SELECTOR = 'h1' # Novel title on main page
CATALOG_LINK_SELECTOR = 'a[href*="/catalog"]' # Catalog link on main page
VOLUME_LINK_SELECTOR = 'a[href*="/vol_"]' # Volume links on main page

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def sanitize_filename(filename):
    """Removes characters invalid for filenames."""
    sanitized = re.sub(r'[\x00-\x1f\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'[.\s]+', '_', sanitized)
    if not sanitized:
        sanitized = "untitled"
    name, ext = os.path.splitext(sanitized)
    name = re.sub(r'[.\s]+', '_', name)
    if not name:
        name = "untitled"
    return name + ext # Re-add original extension if it existed

def download_image(url, save_path, referer_url=None):
    """Downloads an image from a URL and saves it, adding Referer header."""
    try:
        local_headers = HEADERS.copy()
        if referer_url:
            local_headers['Referer'] = referer_url
            logging.debug(f"Using Referer: {referer_url} for downloading {url}")
        else:
            logging.debug(f"No Referer provided for downloading {url}")

        # Add a small random delay before image download request
        time.sleep(random.uniform(0.5, 1.5))

        response = requests.get(url, stream=True, headers=local_headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        content_type = response.headers.get('content-type')
        if content_type and not content_type.startswith('image/'):
            logging.warning(f"Skipping download for {url}, content-type '{content_type}' is not an image.")
            if 'svg' in content_type:
                 logging.warning(f"Downloaded content from {url} appears to be an SVG placeholder.")
            return False

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        logging.debug(f"Successfully downloaded image: {url} to {save_path}")
        return True
    except requests.exceptions.HTTPError as e:
        logging.error(f"Failed to download image {url} due to HTTP Error: {e}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to download image {url}: {e}")
        return False
    except IOError as e:
        logging.error(f"Failed to save image {save_path}: {e}")
        return False

def get_chapter_content(page, initial_chapter_url, image_dir, chapter_filename_base):
    """
    Fetches and extracts the TEXT content of a single chapter using Playwright,
    handling pagination, extracting text, and downloading images.
    Returns the extracted plain text content or None on failure.
    """
    logging.info(f"Fetching chapter content using Playwright starting from: {initial_chapter_url}")
    all_text_parts = []
    current_url = initial_chapter_url
    page_num = 1
    chapter_fetch_successful = True
    img_counter = 0

    try:
        # Add delay before initial navigation
        delay_before_nav = random.uniform(1, 3)
        logging.debug(f"Waiting {delay_before_nav:.2f}s before navigating to initial page: {current_url}")
        time.sleep(delay_before_nav)
        page.goto(current_url, timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle')

        while True:
            page_base_url = page.url
            logging.info(f"Processing page {page_num} of chapter at URL: {page_base_url}")
            page_html = None
            page_text_content = None
            page_fetch_successful = False
            page_reload_count = 0

            # --- Page Load/Extraction Loop (with reloads) ---
            while page_reload_count <= MAX_PAGE_RELOADS:
                current_attempt = page_reload_count + 1
                # Add delay before processing/reloading a page
                delay_before_page_process = random.uniform(1, 3)
                logging.debug(f"Waiting {delay_before_page_process:.2f}s before processing page {page_num} (Attempt {current_attempt})")
                time.sleep(delay_before_page_process)

                logging.debug(f"Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1} for page {page_num} at {page_base_url}")
                try:
                    # --- Attempt to close potential overlays ---
                    logging.debug("Attempting to close potential overlays...")
                    close_buttons = [
                        page.locator('xpath=//button[contains(., "關閉")]'),
                        page.locator('xpath=//button[contains(., "Close")]'),
                        page.locator('[class*="close"]'),
                        page.locator('[id*="close"]')
                    ]
                    for i, button in enumerate(close_buttons):
                        try:
                            if button.first.is_visible(timeout=500):
                                logging.info(f"Found potential close button (pattern {i+1}). Clicking it...")
                                button.first.click(timeout=1000)
                                time.sleep(0.5)
                                logging.info("Clicked potential close button.")
                        except PlaywrightTimeoutError:
                             logging.debug(f"Close button pattern {i+1} not visible or timed out.")
                             continue
                        except Exception as e_click:
                             logging.warning(f"Error clicking close button pattern {i+1}: {e_click}")
                             continue
                except Exception as e:
                    logging.warning(f"Error trying to close overlay: {e}")

                # --- Check for mobile warning ---
                mobile_warning_locator = page.locator(f'text="{MOBILE_WARNING_TEXT}"')
                try:
                    if mobile_warning_locator.is_visible(timeout=2000):
                        logging.warning(f"Detected mobile compatibility warning on {page_base_url}. Reloading page (Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1})...")
                        page_reload_count += 1
                        if page_reload_count > MAX_PAGE_RELOADS:
                            logging.error(f"Max reloads reached for {page_base_url} after detecting mobile warning.")
                            break
                        page.reload(timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle')
                        continue
                    else:
                        logging.debug("Mobile compatibility warning not found.")
                except PlaywrightTimeoutError:
                     logging.debug("Mobile compatibility warning check timed out.")
                except Exception as e:
                     logging.warning(f"Error checking for warning message: {e}")

                # --- Extract Content ---
                try:
                    content_locator = page.locator(f'xpath={CONTENT_XPATH_SELECTOR}')
                    logging.info(f"Waiting for element with XPath '{CONTENT_XPATH_SELECTOR}' to be attached...")
                    content_locator.wait_for(state='attached', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 2)
                    logging.info(f"Element attached. Attempting HTML extraction...")
                    # Add small delay before extraction
                    time.sleep(random.uniform(0.5, 1.5))
                    content_html = content_locator.inner_html(timeout=20000)

                    if not content_html or content_html.strip() == "":
                        logging.warning(f"Extracted HTML is empty for page {page_num} at {page_base_url} on attempt {current_attempt}.")
                        # Fallback using evaluate
                        try:
                            logging.debug("Falling back to page.evaluate() for HTML...")
                            time.sleep(random.uniform(0.5, 1.5)) # Delay before fallback too
                            content_html = page.evaluate(
                                """(xpath) => {
                                    const element = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                                    return element ? element.innerHTML : null;
                                }""", CONTENT_XPATH_SELECTOR
                            )
                            if content_html: logging.info("Successfully extracted HTML using page.evaluate().")
                            else: logging.warning("page.evaluate() also returned empty HTML.")
                        except Exception as eval_e:
                            logging.warning(f"Error during page.evaluate() HTML fallback: {eval_e}")
                            content_html = None

                    # --- Check for Load Failure Message ---
                    if content_html and LOAD_FAILURE_TEXT in content_html:
                        logging.warning(f"Detected load failure message in HTML for {page_base_url}. Reloading page (Attempt {current_attempt}/{MAX_PAGE_RELOADS + 1})...")
                        page_reload_count += 1
                        if page_reload_count > MAX_PAGE_RELOADS:
                            logging.error(f"Max reloads reached for {page_base_url} after detecting load failure message.")
                            break
                        page.reload(timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4, wait_until='networkidle')
                        continue

                    # --- Process HTML ---
                    elif content_html and content_html.strip() != "":
                        logging.info(f"Successfully extracted HTML for page {page_num}. Processing...")
                        soup = BeautifulSoup(content_html, 'html.parser')

                        # --- Image Downloading ---
                        images = soup.find_all('img')
                        logging.info(f"Found {len(images)} image tag(s) on page {page_num}.")
                        for img in images:
                            try:
                                data_src = img.get('data-src')
                                src = img.get('src')
                                img_url_to_download = None

                                if data_src:
                                    img_url_to_download = data_src
                                elif src and 'sloading.svg' not in src.lower():
                                    img_url_to_download = src
                                elif src:
                                    logging.warning(f"Skipping likely placeholder image: {src}")
                                    continue
                                else:
                                    logging.warning("Found img tag with no src or data-src attribute.")
                                    continue

                                absolute_img_url = urljoin(page_base_url, img_url_to_download)
                                logging.debug(f"Processing image URL: {absolute_img_url}")

                                _, potential_ext = os.path.splitext(absolute_img_url.split('?')[0])
                                img_ext = potential_ext if potential_ext else '.jpg'
                                image_filename = f"{chapter_filename_base}_{img_counter}{img_ext}"
                                image_save_path = os.path.join(image_dir, image_filename)

                                if download_image(absolute_img_url, image_save_path, referer_url=page_base_url):
                                    logging.info(f"Successfully downloaded image to: {image_save_path}")
                                    img_counter += 1
                                else:
                                    logging.warning(f"Failed to download image {absolute_img_url}. Skipping.")
                            except Exception as img_e:
                                logging.error(f"Error processing image tag: {img_e}", exc_info=True)

                        # --- Text Extraction ---
                        page_text_content = soup.get_text(separator='\n', strip=True)
                        page_text_content = page_text_content.replace(MOBILE_WARNING_TEXT, "").replace(LOAD_FAILURE_TEXT, "")

                        page_fetch_successful = True
                        logging.info(f"Successfully processed page {page_num} at {page_base_url}.")
                        break # Exit the inner retry loop

                    else:
                        logging.warning(f"Extracted HTML is empty for page {page_num} at {page_base_url} on attempt {current_attempt}.")
                        break # Break inner loop

                except PlaywrightTimeoutError as e:
                    logging.warning(f"Playwright timeout during HTML extraction on attempt {current_attempt} for page {page_num}: {e}")
                    page_reload_count += 1
                    if page_reload_count > MAX_PAGE_RELOADS:
                        logging.error(f"Max reloads reached due to content extraction timeout for page {page_num}.")
                        break
                    time.sleep(RETRY_DELAY_SECONDS)
                except Exception as e:
                    logging.warning(f"Error during content extraction on attempt {current_attempt} for page {page_num}: {e}", exc_info=True)
                    page_reload_count += 1
                    if page_reload_count > MAX_PAGE_RELOADS:
                        logging.error(f"Max reloads reached due to content extraction error for page {page_num}.")
                        break
                    time.sleep(RETRY_DELAY_SECONDS)

            # --- End of Inner Page Load/Extraction Retry Loop ---

            if not page_fetch_successful:
                logging.error(f"Failed to fetch content for page {page_num} at {page_base_url} after {MAX_PAGE_RELOADS + 1} attempts. Aborting chapter.")
                chapter_fetch_successful = False
                break # Break the outer pagination loop

            if page_text_content and page_text_content.strip():
                all_text_parts.append(page_text_content)
                logging.debug(f"Appended text for page {page_num}. Total parts now: {len(all_text_parts)}")
            else:
                logging.warning(f"Page {page_num} fetch reported successful, but extracted text is empty/None. Not appending.")

            # --- Check for Next Page Link ---
            logging.debug(f"Checking for '{NEXT_PAGE_TEXT}' button...")
            try:
                next_button = page.locator(f'xpath={NEXT_BUTTON_SELECTOR}').first
                logging.debug("Checking visibility...")
                is_visible = next_button.is_visible(timeout=2000)

                if not is_visible:
                    logging.info(f"'{NEXT_PAGE_TEXT}' button not visible. Assuming end of chapter (page {page_num}).")
                    break

                logging.debug("Next button is visible. Checking text...")
                button_text = next_button.text_content(timeout=7000)
                logging.debug(f"Potential next button text: '{button_text}'")

                if NEXT_PAGE_TEXT in button_text:
                    # Add delay before clicking next page
                    delay_before_click = random.uniform(1, 3)
                    logging.info(f"Found visible button containing '{NEXT_PAGE_TEXT}'. Waiting {delay_before_click:.2f}s before clicking...")
                    time.sleep(delay_before_click)
                    next_button.click(timeout=10000)

                    logging.debug("Waiting for network idle after click...")
                    page.wait_for_load_state('networkidle', timeout=REQUEST_TIMEOUT_SECONDS * 1000 * 4)

                    # Increase and randomize delay after page load
                    delay_after_load = random.uniform(2, 4)
                    logging.debug(f"Network idle detected. Pausing for {delay_after_load:.2f} second(s)...")
                    time.sleep(delay_after_load)

                    page_num += 1
                    page_base_url = page.url # Update URL for next iteration's referer
                    continue
                else:
                    logging.warning(f"Visible button text '{button_text}' doesn't contain '{NEXT_PAGE_TEXT}'. Assuming end of chapter (page {page_num}).")
                    break

            except PlaywrightTimeoutError:
                logging.info(f"Timeout checking for/interacting with '{NEXT_PAGE_TEXT}' button. Assuming end of chapter (page {page_num}).")
                break
            except Exception as e:
                logging.error(f"Error finding/interacting with '{NEXT_PAGE_TEXT}' button: {e}. Assuming end of chapter (page {page_num}).", exc_info=True)
                break

        # --- End of Outer Pagination Loop ---

        logging.info(f"Exited pagination loop for chapter {initial_chapter_url}. Parts collected: {len(all_text_parts)} for {page_num} pages.")

        if not chapter_fetch_successful:
            logging.error(f"Chapter fetch failed for {initial_chapter_url}.")
            return None

        if not all_text_parts:
             logging.warning(f"No text parts collected for chapter {initial_chapter_url}.")
             return "" # Return empty string if no text but process was ok

        full_chapter_text = "\n\n".join(all_text_parts) # Join with double newline
        logging.info(f"Successfully finished processing chapter {initial_chapter_url}.")
        return full_chapter_text.strip()

    except PlaywrightTimeoutError as e:
        logging.error(f"Playwright timeout during critical operation for chapter {initial_chapter_url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error processing chapter {initial_chapter_url}: {e}", exc_info=True)
        return None

def get_novel_info(novel_id):
    """Fetches novel title and catalog/volume links."""
    novel_url = f"{BASE_URL}/novel/{novel_id}.html"
    logging.info(f"Fetching novel info page: {novel_url}")
    try:
        # Add delay before fetching novel info
        time.sleep(random.uniform(1, 2))
        response = requests.get(novel_url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        title_tag = soup.select_one(NOVEL_TITLE_SELECTOR)
        novel_title = title_tag.get_text(strip=True) if title_tag else f"Novel_{novel_id}"
        logging.info(f"Found novel title: {novel_title}")

        catalog_link_tag = soup.select_one(CATALOG_LINK_SELECTOR)
        if catalog_link_tag and catalog_link_tag.get('href'):
            catalog_url = urljoin(BASE_URL, catalog_link_tag['href'])
            logging.info(f"Found main catalog link: {catalog_url}")
            return novel_title, catalog_url, []

        volume_links = []
        volume_tags = soup.select(VOLUME_LINK_SELECTOR)
        for tag in volume_tags:
            href = tag.get('href')
            if href and f"/novel/{novel_id}/vol_" in href:
                 volume_url = urljoin(BASE_URL, href)
                 volume_title = tag.get_text(strip=True)
                 volume_links.append({'title': volume_title, 'url': volume_url})

        if volume_links:
            logging.info(f"Found {len(volume_links)} volume links.")
            return novel_title, None, volume_links
        else:
            logging.warning(f"Could not find catalog link or volume links for novel {novel_id}.")
            return novel_title, None, []

    except requests.exceptions.Timeout:
        logging.error(f"Timeout fetching novel info page: {novel_url}")
        return f"Novel_{novel_id}", None, []
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching novel info page {novel_url}: {e}")
        return f"Novel_{novel_id}", None, []
    except Exception as e:
        logging.error(f"Unexpected error fetching novel info: {e}", exc_info=True)
        return f"Novel_{novel_id}", None, []


def get_chapter_links_from_soup(page_soup, novel_id):
    """Extracts chapter links from a parsed catalog or volume page soup."""
    chapter_links = []
    chapter_link_pattern = re.compile(rf'^/novel/{novel_id}/\d+\.html$')
    potential_links = page_soup.find_all('a', href=chapter_link_pattern)

    # Simple collection - relies on later sorting/filtering if needed
    for link in potential_links:
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and href:
            full_url = urljoin(BASE_URL, href)
            chapter_links.append({'title': title, 'url': full_url})
            logging.debug(f"Found potential chapter link: {title} ({full_url})")

    if not chapter_links:
         logging.warning("No chapter links found using the defined patterns.")

    return chapter_links


def main():
    """Main function to orchestrate the scraping process."""
    parser = argparse.ArgumentParser(description="Scrape novel chapters from tw.linovelib.com")
    parser.add_argument("--novel-id", required=True, help="The numeric ID of the novel (e.g., 4519)")
    args = parser.parse_args()
    novel_id = args.novel_id

    logging.info(f"--- Starting scraper for Novel ID: {novel_id} ---")

    novel_title, catalog_url, volume_links = get_novel_info(novel_id)

    if not novel_title:
        logging.error("Failed to retrieve novel title. Exiting.")
        return

    sanitized_novel_title = sanitize_filename(novel_title)
    actual_output_dir = os.path.join(OUTPUT_DIR_BASE, f"{novel_id}-{sanitized_novel_title}")
    image_dir = os.path.join(actual_output_dir, "images")

    logging.info(f"Novel Title: {novel_title}")
    logging.info(f"Output Directory: {actual_output_dir}")

    try:
        os.makedirs(actual_output_dir, exist_ok=True)
        os.makedirs(image_dir, exist_ok=True)
        logging.info(f"Output directories ensured.")
    except OSError as e:
        logging.error(f"Failed to create output directories: {e}")
        return

    # --- Fetch Chapter Links ---
    all_chapter_links = []
    if catalog_url:
        logging.info(f"Fetching chapter links from main catalog: {catalog_url}")
        try:
            # Retry logic for catalog fetch
            for attempt in range(MAX_RETRIES + 1):
                try:
                    # Add delay before fetching catalog/volume
                    time.sleep(random.uniform(1, 2))
                    response = requests.get(catalog_url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
                    response.raise_for_status()
                    response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
                    catalog_soup = BeautifulSoup(response.text, 'html.parser')
                    all_chapter_links = get_chapter_links_from_soup(catalog_soup, novel_id)
                    break # Success
                except requests.exceptions.RequestException as e_req:
                    if attempt < MAX_RETRIES:
                        logging.warning(f"Attempt {attempt+1} failed fetching catalog {catalog_url}: {e_req}. Retrying in {RETRY_DELAY_SECONDS}s...")
                        time.sleep(RETRY_DELAY_SECONDS)
                    else:
                        logging.error(f"Failed to fetch catalog {catalog_url} after {MAX_RETRIES+1} attempts: {e_req}")
                        raise # Re-raise the last exception
        except Exception as e:
            logging.error(f"An unexpected error occurred during catalog processing: {e}", exc_info=True)

    elif volume_links:
        logging.info("Fetching chapter links from volume pages...")
        for volume in volume_links:
            v_title = volume['title']
            v_url = volume['url']
            logging.info(f"Processing Volume: {v_title} ({v_url})")
            try:
                 # Retry logic for volume fetch
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        # Add delay before fetching catalog/volume
                        time.sleep(random.uniform(1, 2))
                        response = requests.get(v_url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
                        response.raise_for_status()
                        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
                        volume_soup = BeautifulSoup(response.text, 'html.parser')
                        volume_chapters = get_chapter_links_from_soup(volume_soup, novel_id)
                        all_chapter_links.extend(volume_chapters)
                        break # Success
                    except requests.exceptions.RequestException as e_req:
                        if attempt < MAX_RETRIES:
                            logging.warning(f"Attempt {attempt+1} failed fetching volume {v_url}: {e_req}. Retrying in {RETRY_DELAY_SECONDS}s...")
                            time.sleep(RETRY_DELAY_SECONDS)
                        else:
                            logging.error(f"Failed to fetch volume {v_url} after {MAX_RETRIES+1} attempts: {e_req}")
                            raise # Re-raise the last exception

                # Use main delay between volumes too, maybe slightly randomized
                volume_delay = REQUEST_DELAY_SECONDS + random.uniform(-1, 1)
                logging.debug(f"Waiting {volume_delay:.2f}s after processing volume...")
                time.sleep(max(1, volume_delay)) # Ensure delay is at least 1s
            except Exception as e:
                logging.error(f"An unexpected error occurred processing volume {v_url}: {e}", exc_info=True)
                continue # Skip to next volume on error

    else:
        logging.error(f"No catalog or volume links found for novel {novel_id}. Cannot proceed.")
        return

    if not all_chapter_links:
        logging.error("Failed to find any chapter links. Exiting.")
        return

    # --- Deduplicate chapter links ---
    seen_urls = set()
    unique_chapter_links = []
    for chapter in all_chapter_links:
        if chapter['url'] not in seen_urls:
            unique_chapter_links.append(chapter)
            seen_urls.add(chapter['url'])
    logging.info(f"Found {len(unique_chapter_links)} unique chapters to download.")
    all_chapter_links = unique_chapter_links

    # --- Process Chapters using Playwright ---
    with sync_playwright() as p:
        browser = None
        context = None
        try:
            browser = p.chromium.launch(headless=False) # Set headless=True for production
            context = browser.new_context(
                locale='zh-TW', # Set locale
            )
            page = context.new_page()
            logging.info("Playwright browser launched (locale: zh-TW).")

            total_chapters = len(all_chapter_links)
            for i, chapter in enumerate(all_chapter_links):
                title = chapter['title']
                url = chapter['url']
                logging.info(f"--- Processing chapter {i+1}/{total_chapters}: {title} ---")

                base_filename_no_ext = sanitize_filename(f"{i+1:03d}_{title}")
                chapter_filename = base_filename_no_ext + ".txt"
                filepath = os.path.join(actual_output_dir, chapter_filename)

                if os.path.exists(filepath):
                    logging.info(f"Skipping already downloaded chapter: {filepath}")
                    # Add a small delay even when skipping to avoid rapid checks
                    time.sleep(random.uniform(0.2, 0.5))
                    continue

                text_content = get_chapter_content(page, url, image_dir, base_filename_no_ext)

                if text_content is not None:
                    if LOAD_FAILURE_TEXT in text_content: # Final check
                         logging.error(f"Text content for {title} still contains load failure message. Skipping save.")
                    else:
                        try:
                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(f"{title}\n\n") # Write title first
                                f.write(text_content) # Write extracted text
                            logging.info(f"Successfully saved: {filepath}")
                        except IOError as e:
                            logging.error(f"Error writing file {filepath}: {e}")
                        except Exception as e:
                            logging.error(f"Unexpected error writing file {filepath}: {e}")
                else:
                    logging.warning(f"Skipping chapter due to content fetch/parse error: {title}")

                # Main delay between chapters, slightly randomized
                chapter_delay = REQUEST_DELAY_SECONDS + random.uniform(-2, 2)
                logging.debug(f"Waiting for {chapter_delay:.2f} second(s) before next chapter...")
                time.sleep(max(2, chapter_delay)) # Ensure delay is at least 2s

            logging.info("Finished processing all chapters.")

        except Exception as e:
            logging.error(f"An unexpected error occurred during Playwright processing: {e}", exc_info=True)
        finally:
            # Ensure browser and context are closed
            if context:
                try:
                    context.close()
                    logging.info("Playwright context closed.")
                except Exception as ce:
                    logging.error(f"Error closing context: {ce}")
            if browser:
                try:
                    browser.close()
                    logging.info("Playwright browser closed.")
                except Exception as be:
                    logging.error(f"Error closing browser: {be}")

    logging.info("--- Scraping finished ---")


if __name__ == "__main__":
    main()
