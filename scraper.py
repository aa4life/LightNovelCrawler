import requests
from bs4 import BeautifulSoup
import os
import time
import re
import logging

# --- Configuration ---
BASE_URL = "https://tw.linovelib.com"
CATALOG_URL = f"{BASE_URL}/novel/4519/catalog"
OUTPUT_DIR = "novel_chapters"
REQUEST_DELAY_SECONDS = 1 # Delay between requests to be polite to the server
REQUEST_TIMEOUT_SECONDS = 15 # Timeout for network requests

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

def get_chapter_content(chapter_url):
    """Fetches and extracts the text content of a single chapter."""
    logging.info(f"Fetching chapter content from: {chapter_url}")
    try:
        response = requests.get(chapter_url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        # Use apparent_encoding for better guessing, fallback to utf-8
        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the main content div (inspected from the website)
        content_div = soup.find('div', id='content')
        if not content_div:
            logging.warning(f"Content div ('#content') not found for {chapter_url}")
            return None

        # Extract text primarily from <p> tags within the content div
        paragraphs = content_div.find_all('p')
        if paragraphs:
            # Join text from all paragraphs, stripping extra whitespace from each
            content = "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        else:
            # Fallback: get all text from the div if no <p> tags found
            logging.warning(f"No <p> tags found in content div for {chapter_url}. Using fallback text extraction.")
            content = content_div.get_text(strip=True, separator='\n\n')

        # Basic cleaning: remove potential leftover script/style tags if any were inside #content
        content = re.sub(r'<script.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r'<style.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)

        return content.strip() # Return cleaned, stripped content

    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred while fetching {chapter_url}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching chapter {chapter_url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Error parsing chapter {chapter_url}: {e}")
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

    try:
        logging.info("Fetching catalog page...")
        response = requests.get(CATALOG_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
        logging.info("Catalog page fetched successfully.")

        soup = BeautifulSoup(response.text, 'html.parser')

        chapter_links = get_chapter_links(soup)

        if not chapter_links:
            logging.error("Failed to find any valid chapter links. Exiting.")
            return

        logging.info(f"Found {len(chapter_links)} chapters to download.")

        # Create output directory if it doesn't exist
        try:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            logging.info(f"Output directory '{OUTPUT_DIR}' ensured.")
        except OSError as e:
            logging.error(f"Failed to create output directory '{OUTPUT_DIR}': {e}")
            return

        # Process each chapter
        for i, chapter in enumerate(chapter_links):
            title = chapter['title']
            url = chapter['url']
            logging.info(f"--- Processing chapter {i+1}/{len(chapter_links)}: {title} ---")

            content = get_chapter_content(url)

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

            # Polite delay
            logging.debug(f"Waiting for {REQUEST_DELAY_SECONDS} second(s)...")
            time.sleep(REQUEST_DELAY_SECONDS)

        logging.info("--- Scraping finished ---")

    except requests.exceptions.Timeout:
        logging.error(f"Timeout occurred while fetching the catalog page: {CATALOG_URL}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching catalog page {CATALOG_URL}: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during the scraping process: {e}", exc_info=True) # Log traceback

if __name__ == "__main__":
    main()
