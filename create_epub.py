import os
import re
import logging
import glob
import datetime
from ebooklib import epub
from natsort import natsorted # For natural sorting
from PIL import Image # To get image dimensions/format
import mimetypes # To guess image mime types

# --- Configuration ---
BASE_INPUT_DIR = "novel_chapters" # Base directory containing novel subdirectories
BASE_OUTPUT_DIR = "epub_output" # Directory to save generated EPUBs
EPUB_LANGUAGE = "zh-TW" # Language code (Traditional Chinese)
EPUB_AUTHOR = "Unknown Author" # Default author

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def sanitize_filename(filename):
    """Removes characters invalid for filenames."""
    sanitized = re.sub(r'[\x00-\x1f\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'[.\s]+', '_', sanitized)
    if not sanitized:
        sanitized = "untitled"
    return sanitized

def get_chapter_files(input_dir):
    """Finds and naturally sorts all chapter .txt files (e.g., 001_*.txt) in the directory."""
    chapter_pattern = os.path.join(input_dir, "[0-9][0-9][0-9]_*.txt")
    files = glob.glob(chapter_pattern)

    if not files:
        # Fallback: find any .txt file if the numbered pattern fails
        logging.warning(f"No files found matching pattern '{chapter_pattern}' in '{input_dir}'. Falling back to any '.txt' file.")
        files = glob.glob(os.path.join(input_dir, "*.txt"))

    if not files:
        logging.warning(f"No .txt files found at all in '{input_dir}'.")
        return []

    try:
        sorted_files = natsorted(files)
        logging.info(f"Found and sorted {len(sorted_files)} chapter files in '{input_dir}'.")
        return sorted_files
    except Exception as e:
        logging.error(f"Error sorting files in directory {input_dir}: {e}")
        return []

def read_chapter_content(filepath):
    """Reads content from a chapter file (UTF-8)."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error reading file {filepath}: {e}")
        return None

def find_and_map_images(image_dir):
    """Scans the image directory, maps images to chapter numbers based on filename prefix."""
    images_by_chapter = {}
    if not os.path.isdir(image_dir):
        logging.info(f"Image directory not found: {image_dir}")
        return images_by_chapter, [] # Return empty map and list

    logging.info(f"Scanning for images in: {image_dir}")
    image_files = []
    try:
        # List all files, then filter for images and extract chapter number
        all_files = [f for f in os.listdir(image_dir) if os.path.isfile(os.path.join(image_dir, f))]
        # Naturally sort all potential image files first
        sorted_potential_images = natsorted(all_files)

        for filename in sorted_potential_images:
            match = re.match(r'^(\d+)_', filename) # Match '001_', '002_', etc.
            if match:
                chapter_num_str = match.group(1) # e.g., "001"
                full_image_path = os.path.join(image_dir, filename)
                if chapter_num_str not in images_by_chapter:
                    images_by_chapter[chapter_num_str] = []
                images_by_chapter[chapter_num_str].append(full_image_path)
                image_files.append(full_image_path) # Keep track of all valid image files found
            else:
                logging.debug(f"Ignoring file in images directory (no chapter prefix): {filename}")

        logging.info(f"Found {len(image_files)} images mapped to {len(images_by_chapter)} chapters.")

    except Exception as e:
        logging.error(f"Error scanning image directory {image_dir}: {e}")

    return images_by_chapter, image_files


def create_epub(input_dir, chapter_files, output_filename, novel_title):
    """Creates an EPUB file from chapter files, embedding images found in 'images' subdirectory."""
    if not chapter_files:
        logging.warning(f"No chapter files provided for '{novel_title}'. Skipping EPUB creation.")
        return

    book = epub.EpubBook()
    image_dir_path = os.path.join(input_dir, "images") # Path to images on disk

    # --- Set Metadata ---
    book.set_identifier(f"urn:uuid:{os.path.basename(input_dir)}-{hash(output_filename)}")
    book.set_title(novel_title)
    book.set_language(EPUB_LANGUAGE)
    book.add_author(EPUB_AUTHOR) # Consider fetching author if available

    # --- Find and Map Images ---
    images_by_chapter, all_image_paths = find_and_map_images(image_dir_path)
    added_epub_images = {} # Track images added to EPUB (path -> EpubImage item)

    # --- Add CSS ---
    css = """
    p { text-indent: 2em; margin-top: 0; margin-bottom: 0.5em; line-height: 1.6; } /* Adjusted margin-bottom */
    img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
    h1 { text-align: center; margin-top: 2em; margin-bottom: 1.5em; }
    """
    style = epub.EpubItem(uid="style_default", file_name="style/default.css", media_type="text/css", content=css.encode('utf-8'))
    book.add_item(style)

    chapters = []
    toc = []

    logging.info(f"Starting EPUB chapter creation for '{novel_title}'...")
    # --- Process Chapters ---
    for i, filepath in enumerate(chapter_files):
        logging.info(f"Processing chapter file {i+1}/{len(chapter_files)}: {os.path.basename(filepath)}")

        raw_content = read_chapter_content(filepath)
        if raw_content is None:
            logging.warning(f"Skipping chapter due to read error: {filepath}")
            continue

        # --- Extract Chapter Title and Content ---
        lines = raw_content.split('\n', 1)
        chapter_title = ""
        main_content = ""
        filename_base = os.path.splitext(os.path.basename(filepath))[0]
        # Try to extract chapter number like '001' from '001_...'
        chapter_num_match = re.match(r'^(\d+)', filename_base)
        current_chapter_num_str = chapter_num_match.group(1) if chapter_num_match else None

        if len(lines) >= 1:
            chapter_title = lines[0].strip()
            if not chapter_title:
                 title_fallback = re.sub(r'^\d+_', '', filename_base).replace('_', ' ')
                 chapter_title = title_fallback if title_fallback else f"Chapter {i+1}"
                 logging.warning(f"Using fallback title: {chapter_title}")
            main_content = lines[1].strip() if len(lines) > 1 else ""
        else:
            title_fallback = re.sub(r'^\d+_', '', filename_base).replace('_', ' ')
            chapter_title = title_fallback if title_fallback else f"Chapter {i+1}"
            main_content = raw_content.strip()
            logging.warning(f"Using fallback title: {chapter_title}")

        logging.info(f"  Chapter Title: {chapter_title}")
        if current_chapter_num_str:
             logging.info(f"  Chapter Number Key: {current_chapter_num_str}")


        # --- Convert plain text to HTML ---
        html_body_content = f"<h1>{chapter_title}</h1>\n"
        # --- MODIFICATION START ---
        # Split content by single newline, treat each line as a paragraph
        text_lines = main_content.split('\n')
        for line in text_lines:
            line_strip = line.strip()
            if line_strip: # Only add non-empty lines as paragraphs
                html_body_content += f"<p>{line_strip}</p>\n"
            else:
                # Optionally, add an empty paragraph for blank lines if desired
                # html_body_content += "<p>&nbsp;</p>\n"
                pass # Currently, empty lines in txt are ignored
        # --- MODIFICATION END ---

        # --- Append Images for this chapter (if any) ---
        if current_chapter_num_str and current_chapter_num_str in images_by_chapter:
            chapter_images = images_by_chapter[current_chapter_num_str]
            logging.info(f"  Found {len(chapter_images)} images for chapter {current_chapter_num_str}.")

            # Images are already sorted by find_and_map_images (due to natsort on directory listing)
            for image_disk_path in chapter_images:
                img_filename = os.path.basename(image_disk_path)
                epub_image_path = f"images/{img_filename}" # Path inside EPUB

                # Add image to book only once
                if image_disk_path not in added_epub_images:
                    try:
                        mime_type, _ = mimetypes.guess_type(image_disk_path)
                        if not mime_type:
                            # Try getting mime type using Pillow as fallback
                            try:
                                with Image.open(image_disk_path) as pil_img:
                                    mime_type = Image.MIME.get(pil_img.format)
                            except Exception as pil_e:
                                logging.warning(f"Pillow could not open image {img_filename} to determine mime type: {pil_e}")

                        if not mime_type:
                             mime_type = 'application/octet-stream' # Final fallback
                             logging.warning(f"Could not determine mime type for {img_filename}, using fallback '{mime_type}'.")

                        with open(image_disk_path, 'rb') as img_file:
                            image_content = img_file.read()

                        epub_image = epub.EpubImage(
                            uid=f"img_{sanitize_filename(img_filename).replace('.', '_')}",
                            file_name=epub_image_path,
                            media_type=mime_type,
                            content=image_content
                        )
                        book.add_item(epub_image)
                        added_epub_images[image_disk_path] = epub_image # Track added image
                        logging.debug(f"Added image {img_filename} to EPUB.")

                    except FileNotFoundError:
                         logging.error(f"Image file not found: {image_disk_path}")
                         html_body_content += f"<p>[Image not found: {img_filename}]</p>\n"
                         continue
                    except Exception as img_e:
                        logging.error(f"Error processing image file {image_disk_path}: {img_e}", exc_info=True)
                        html_body_content += f"<p>[Error loading image: {img_filename}]</p>\n"
                        continue # Skip adding img tag if error

                # Append <img> tag to HTML content
                html_body_content += f'<img src="{epub_image_path}" alt="{img_filename}" />\n'
        elif current_chapter_num_str:
             logging.debug(f"  No images found for chapter {current_chapter_num_str}.")
        else:
             logging.warning(f"  Could not determine chapter number for {filename_base} to check for images.")


        # --- Create EPUB Chapter ---
        epub_chapter_filename = f'chapter_{i+1:04d}.xhtml'
        epub_chapter = epub.EpubHtml(title=chapter_title,
                                     file_name=epub_chapter_filename,
                                     lang=EPUB_LANGUAGE)
        epub_chapter.content = f"""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{EPUB_LANGUAGE}">
<head>
    <meta charset="utf-8"/>
    <title>{chapter_title}</title>
    <link href="style/default.css" rel="stylesheet" type="text/css"/>
</head>
<body>
{html_body_content}
</body>
</html>
""".encode('utf-8')
        epub_chapter.add_item(style) # Link CSS

        book.add_item(epub_chapter)
        chapters.append(epub_chapter)
        toc.append(epub.Link(epub_chapter_filename, chapter_title, f'chap_{i+1:04d}'))

    if not chapters:
        logging.error(f"No valid chapters processed for '{novel_title}'. EPUB creation aborted.")
        return

    # Define TOC and Spine
    book.toc = tuple(toc)
    book.spine = ['nav'] + chapters # Add nav page first, then chapters

    # Add default NCX and Nav file
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Write EPUB file ---
    try:
        os.makedirs(os.path.dirname(output_filename), exist_ok=True)
        epub.write_epub(output_filename, book, {})
        logging.info(f"Successfully created EPUB: {output_filename}")
    except Exception as e:
        logging.error(f"Error writing EPUB file {output_filename}: {e}")

def main():
    """Finds novel directories and creates EPUBs."""
    logging.info(f"--- Starting EPUB creation process ---")
    logging.info(f"Scanning base directory: {BASE_INPUT_DIR}")

    if not os.path.isdir(BASE_INPUT_DIR):
        logging.error(f"Base input directory not found: {BASE_INPUT_DIR}")
        return

    try:
        os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    except OSError as e:
        logging.error(f"Failed to create base output directory {BASE_OUTPUT_DIR}: {e}")
        return

    processed_count = 0
    for item_name in os.listdir(BASE_INPUT_DIR):
        item_path = os.path.join(BASE_INPUT_DIR, item_name)

        if os.path.isdir(item_path):
            logging.info(f"--- Processing novel directory: {item_name} ---")
            input_dir = item_path

            # --- Derive Title and Output Filename ---
            dir_basename = os.path.basename(input_dir)
            # Try to extract title from 'ID-Novel_Title' format
            match = re.match(r"^\d+-(.*)", dir_basename)
            if match:
                novel_title_part = match.group(1).replace('_', ' ') # Use spaces for title
            else:
                novel_title_part = dir_basename.replace('_', ' ') # Fallback
            epub_title = novel_title_part # Title for EPUB metadata

            # Sanitize title for use in OS filename
            safe_filename_title = sanitize_filename(novel_title_part)
            current_date = datetime.date.today().strftime('%Y%m%d')
            output_filename_base = f"{safe_filename_title}_{current_date}.epub"
            output_filepath = os.path.join(BASE_OUTPUT_DIR, output_filename_base)

            logging.info(f"  Novel Directory: {input_dir}")
            logging.info(f"  EPUB Title: {epub_title}")
            logging.info(f"  Output Filename: {output_filepath}")

            chapter_files = get_chapter_files(input_dir)

            if chapter_files:
                create_epub(input_dir, chapter_files, output_filepath, epub_title)
                processed_count += 1
            else:
                logging.warning(f"  No chapter files found or error occurred for '{item_name}'. EPUB not created.")
            logging.info(f"--- Finished processing directory: {item_name} ---")
        else:
            logging.debug(f"Skipping non-directory item: {item_name}")

    logging.info(f"--- EPUB creation process finished. Processed {processed_count} novel directories. ---")

if __name__ == "__main__":
    # --- Dependency Check ---
    try:
        from PIL import Image
    except ImportError:
        logging.error("Pillow library not found. Please install it: pip install Pillow")
        # Consider exiting if Pillow is critical: exit(1)
    try:
        import natsort
    except ImportError:
        logging.error("natsort library not found. Please install it: pip install natsort")
        # Consider exiting if natsort is critical: exit(1)

    mimetypes.init() # Ensure mimetypes database is loaded

    main()
