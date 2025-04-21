import os
import re
import logging
from ebooklib import epub
from bs4 import BeautifulSoup # To parse text into paragraphs
from natsort import natsorted # For natural sorting of chapter filenames

# --- Configuration ---
# Directory containing the downloaded .txt chapter files
INPUT_DIR = "novel_chapters"
# Desired name for the output EPUB file
OUTPUT_EPUB_FILENAME = "novel_output.epub"
# EPUB Metadata (Customize as needed)
EPUB_TITLE = "小說匯出" # Default title, consider making this dynamic or an argument
EPUB_LANGUAGE = "zh-TW" # Language code (Traditional Chinese)
EPUB_AUTHOR = "Unknown Author" # Default author

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_chapter_files(directory):
    """Finds and naturally sorts all .txt files in the specified directory."""
    txt_files = []
    try:
        if not os.path.isdir(directory):
            logging.error(f"Input directory not found: {directory}")
            return []
        for filename in os.listdir(directory):
            if filename.lower().endswith(".txt"):
                txt_files.append(os.path.join(directory, filename))

        # Sort files naturally based on filename
        sorted_files = natsorted(txt_files)
        logging.info(f"Found and sorted {len(sorted_files)} chapter files.")
        return sorted_files
    except Exception as e:
        logging.error(f"Error accessing or listing directory {directory}: {e}")
        return []

def read_chapter_content(filepath):
    """Reads content from a chapter file, skipping the first line if it's a title."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Assume the first line is the title header (e.g., "# Chapter Title")
            # and skip it if it starts with '#'
            if lines and lines[0].startswith('#'):
                content = "".join(lines[1:])
                # Extract title from the first line for potential use (though we use filename now)
                title_from_header = lines[0].lstrip('# ').strip()
            else:
                content = "".join(lines)
                title_from_header = None # No header found

            return content.strip(), title_from_header
    except IOError as e:
        logging.error(f"Error reading file {filepath}: {e}")
        return None, None
    except Exception as e:
        logging.error(f"Unexpected error reading file {filepath}: {e}")
        return None, None

def text_to_html(text_content):
    """Converts plain text to simple HTML with paragraphs."""
    if not text_content:
        return ""
    # Split into paragraphs based on double newlines, then wrap each in <p> tags
    paragraphs = re.split(r'\n\s*\n', text_content.strip())
    html_content = "".join(f"<p>{p.strip()}</p>\n" for p in paragraphs if p.strip())
    return html_content

def create_epub(chapter_files, output_filename):
    """Creates an EPUB file from a list of chapter text files."""
    if not chapter_files:
        logging.warning("No chapter files provided to create EPUB.")
        return

    book = epub.EpubBook()

    # --- Set Metadata ---
    book.set_identifier(f"urn:uuid:{os.path.basename(output_filename)}-{hash(output_filename)}") # Basic unique ID
    book.set_title(EPUB_TITLE)
    book.set_language(EPUB_LANGUAGE)
    book.add_author(EPUB_AUTHOR)

    chapters = []
    toc = []

    logging.info("Starting EPUB chapter creation...")
    for i, filepath in enumerate(chapter_files):
        # Extract chapter title from filename (remove path and .txt extension)
        filename = os.path.basename(filepath)
        chapter_title = os.path.splitext(filename)[0]
        # Replace underscores potentially added by sanitize_filename back to spaces for readability
        chapter_title = chapter_title.replace('_', ' ')
        logging.info(f"Processing chapter {i+1}: {chapter_title}")

        plain_content, _ = read_chapter_content(filepath) # Ignore title from header for now

        if plain_content is None:
            logging.warning(f"Skipping chapter due to read error: {filepath}")
            continue

        # Convert plain text to HTML
        html_content = text_to_html(plain_content)
        if not html_content:
             logging.warning(f"Skipping chapter due to empty content after HTML conversion: {filepath}")
             continue

        # Create EPUB chapter object
        # Use a simple filename based on index to avoid issues with special chars in titles
        epub_chapter_filename = f'chapter_{i+1:04d}.xhtml'
        epub_chapter = epub.EpubHtml(title=chapter_title,
                                     file_name=epub_chapter_filename,
                                     lang=EPUB_LANGUAGE)
        # Add basic CSS for paragraph spacing
        css = """
        p {
            text-indent: 2em; /* Indent first line of paragraphs */
            margin-top: 0;
            margin-bottom: 1em; /* Space between paragraphs */
            line-height: 1.5; /* Adjust line spacing */
        }
        """
        style = epub.EpubItem(uid="style_default", file_name="style/default.css", media_type="text/css", content=css)
        book.add_item(style)
        epub_chapter.add_item(style) # Link CSS to the chapter

        # Set chapter content (ensure it's bytes)
        epub_chapter.content = f'<h1>{chapter_title}</h1>\n{html_content}'.encode('utf-8')

        book.add_item(epub_chapter)
        chapters.append(epub_chapter)
        toc.append(epub.Link(epub_chapter_filename, chapter_title, f'chap_{i+1:04d}'))

    if not chapters:
        logging.error("No valid chapters could be processed. EPUB creation aborted.")
        return

    # Define Table of Contents and Spine (order of chapters)
    book.toc = tuple(toc)
    book.spine = ['nav'] + chapters # Add nav file first, then all chapters

    # Add default NCX and Nav file
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Write EPUB file ---
    try:
        epub.write_epub(output_filename, book, {})
        logging.info(f"Successfully created EPUB: {output_filename}")
    except Exception as e:
        logging.error(f"Error writing EPUB file {output_filename}: {e}")

def main():
    """Main function to find chapters and create the EPUB."""
    logging.info(f"Looking for chapter files in: {INPUT_DIR}")
    chapter_files = get_chapter_files(INPUT_DIR)

    if chapter_files:
        create_epub(chapter_files, OUTPUT_EPUB_FILENAME)
    else:
        logging.warning("No chapter files found or error occurred. EPUB not created.")

    logging.info("--- EPUB creation process finished ---")

if __name__ == "__main__":
    main()
