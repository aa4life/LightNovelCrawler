# Linovelib 爬蟲與 EPUB 製作工具

本專案可以於下列網站爬蟲抓取小說，並透過程式碼運行後產生epub檔案。
https://tw.linovelib.com/novel/

這個專案包含兩個 Python 腳本：

1.  `scraper.py`：從 `tw.linovelib.com` 抓取小說章節和圖片。
2.  `create_epub.py`：將下載的章節轉換成 EPUB 格式。

## 功能

*   下載小說內容，包括文字和圖片。
*   處理章節內的頁碼。
*   按小說標題整理下載的內容。
*   包含可配置的抓取延遲和重試。
*   從下載的章節創建 EPUB 文件，保留結構和圖片。
*   支援繁體中文（`zh-TW`）。

## 必備條件

*   Python 3.x
*   所需的 Python 套件（透過 pip 安裝）：
    *   `requests`
    *   `beautifulsoup4`
    *   `playwright`
    *   `ebooklib`

## 安裝

1.  **Clone：**
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **安裝所需的 Python 套件：**
    ```bash
    pip install requests beautifulsoup4 playwright ebooklib
    ```

## 配置

兩個腳本都在頂部包含可以修改的配置變數：

*   **`scraper.py`：**
    *   `BASE_URL`：目標網站 URL。
    *   `OUTPUT_DIR_BASE`：儲存下載小說章節的位置。
    *   `REQUEST_DELAY_SECONDS`：獲取章節之間的延遲。
    *   `REQUEST_TIMEOUT_SECONDS`：網路請求超時。
    *   `MAX_RETRIES`、`RETRY_DELAY_SECONDS`：獲取目錄/卷的重試邏輯。
    *   `HEADERS`：請求的 User-Agent。
    *   選擇器（`NEXT_BUTTON_SELECTOR`、`CONTENT_XPATH_SELECTOR` 等）：用於在網站上查找元素的 CSS/XPath 選擇器。如果網站結構發生變化，請調整。

*   **`create_epub.py`：**
    *   `BASE_INPUT_DIR`：包含下載小說章節的目錄（應與 `scraper.py` 中的 `OUTPUT_DIR_BASE` 匹配）。
    *   `BASE_OUTPUT_DIR`：儲存生成的 EPUB 文件的目錄。
    *   `EPUB_LANGUAGE`：EPUB 元數據的語言代碼。
    *   `EPUB_AUTHOR`：如果未找到，則為預設作者姓名。

## 使用方法

1.  **運行爬蟲：**
    執行 `scraper.py` 腳本，提供來自 `tw.linovelib.com` 的小說 ID 作為命令行參數。
    ```bash
    python scraper.py --novel-id <novel_id>
    ```
    例如，如果小說 URL 是 `https://tw.linovelib.com/novel/1234.html`，則小說 ID 為 `1234`。
    ```bash
    python scraper.py --novel-id 1234
    ```
    該腳本將在 `novel_chapters/<novel_title>/` 下創建一個目錄結構，其中包含下載的 `.txt` 章節文件和圖片。

2.  **創建 EPUB：**
    抓取完成後，運行 `create_epub.py` 腳本。它將自動在 `novel_chapters` 目錄（或更改後的 `BASE_INPUT_DIR`）中查找小說，並在 `epub_output` 目錄（或 `BASE_OUTPUT_DIR`）中創建 EPUB 文件。
    ```bash
    python create_epub.py
    ```
    該腳本將為在輸入目錄中找到的每部小說生成一個名為 `<novel_title>.epub` 的 EPUB 文件。

## 備註

*   請尊重網站的服務條款。包含抓取延遲 (`REQUEST_DELAY_SECONDS`) 是為了避免伺服器過載。請負責任地調整。
*   網站結構的更改可能會破壞爬蟲。如果發生這種情況，可能需要更新 `scraper.py` 中的 CSS/XPath 選擇器。
*   `sanitize_filename` 函數在兩個腳本中用於確保有效的檔案/目錄名稱。
