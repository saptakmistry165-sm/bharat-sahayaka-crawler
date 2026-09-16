import os
import sys
import time
import hashlib
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser


USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "BharatSahayakaBot/1.0 (+https://bharat-sahayaka.example)"
)

MAX_PAGES = int(os.getenv("MAX_PAGES", "10"))
MAX_DEPTH = int(os.getenv("MAX_DEPTH", "1"))
CRAWL_DELAY = float(os.getenv("CRAWL_DELAY", "1.0"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

RESPECT_ROBOTS = os.getenv("RESPECT_ROBOTS", "true").lower() == "true"


session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml"
})


def normalize_url(url):
    url, _ = urldefrag(url)
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    # Remove default ports
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and parsed.scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and parsed.scheme == "https":
        netloc = netloc[:-4]

    path = parsed.path or "/"

    return parsed._replace(
        netloc=netloc,
        path=path
    ).geturl()


def same_domain(seed_url, target_url):
    return urlparse(seed_url).netloc.lower() == urlparse(target_url).netloc.lower()


def get_robots_parser(seed_url):
    parsed = urlparse(seed_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    rp = RobotFileParser()

    try:
        response = session.get(robots_url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            rp.parse(response.text.splitlines())
        else:
            rp.parse([])

    except Exception:
        rp.parse([])

    return rp


def fetch(url):
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )

            return response

        except requests.RequestException as exc:
            last_error = exc

            if attempt < MAX_RETRIES:
                time.sleep(min(2 ** attempt, 8))

    raise last_error


def extract_page(response):
    soup = BeautifulSoup(response.text, "html.parser")

    # Remove non-content elements
    for tag in soup([
        "script",
        "style",
        "noscript",
        "svg"
    ]):
        tag.decompose()

    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

    description = ""
    meta_description = soup.find(
        "meta",
        attrs={"name": lambda x: x and x.lower() == "description"}
    )

    if meta_description:
        description = meta_description.get("content", "").strip()

    content = soup.get_text(" ", strip=True)

    links = []

    for anchor in soup.find_all("a", href=True):
        link = urljoin(response.url, anchor["href"])
        link = normalize_url(link)

        if link:
            links.append(link)

    return {
        "url": response.url,
        "status_code": response.status_code,
        "title": title,
        "description": description,
        "content": content,
        "links": links,
        "content_hash": hashlib.sha256(
            content.encode("utf-8", errors="ignore")
        ).hexdigest()
    }


def crawl(seed_url):
    seed_url = normalize_url(seed_url)

    if not seed_url:
        raise ValueError("Invalid seed URL")

    seed_domain = urlparse(seed_url).netloc.lower()

    robots = get_robots_parser(seed_url)

    queue = deque([
        (seed_url, 0)
    ])

    visited = set()
    seen_content = set()
    results = []

    while queue and len(results) < MAX_PAGES:
        current_url, depth = queue.popleft()

        if current_url in visited:
            continue

        visited.add(current_url)

        if depth > MAX_DEPTH:
            continue

        if RESPECT_ROBOTS and not robots.can_fetch(USER_AGENT, current_url):
            print(f"ROBOTS BLOCKED: {current_url}")
            continue

        print(f"[Depth {depth}] Crawling: {current_url}")

        try:
            response = fetch(current_url)
        except Exception as exc:
            print(f"FAILED: {current_url} -> {exc}")
            continue

        content_type = response.headers.get(
            "content-type",
            ""
        ).lower()

        # Only process HTML pages
        if "text/html" not in content_type:
            continue

        # Respect X-Robots-Tag
        x_robots = response.headers.get(
            "X-Robots-Tag",
            ""
        ).lower()

        if "noindex" in x_robots:
            print(f"X-ROBOTS NOINDEX: {current_url}")
            continue

        try:
            page = extract_page(response)
        except Exception as exc:
            print(f"PARSE FAILED: {current_url} -> {exc}")
            continue

        # Respect meta robots
        soup = BeautifulSoup(response.text, "html.parser")

        meta_robots = soup.find(
            "meta",
            attrs={"name": lambda x: x and x.lower() == "robots"}
        )

        if meta_robots:
            robots_value = meta_robots.get(
                "content",
                ""
            ).lower()

            if "noindex" in robots_value:
                print(f"META NOINDEX: {current_url}")
                continue

        # Duplicate-content detection
        if page["content_hash"] in seen_content:
            print(f"DUPLICATE: {current_url}")
            continue

        seen_content.add(page["content_hash"])

        results.append(page)

        # Don't discover deeper links from max-depth pages
        if depth >= MAX_DEPTH:
            continue

        for link in page["links"]:
            if not same_domain(seed_url, link):
                continue

            parsed = urlparse(link)

            # Avoid obvious non-web resources
            blocked_extensions = (
                ".jpg", ".jpeg", ".png", ".gif",
                ".webp", ".pdf", ".zip", ".mp4",
                ".mp3", ".avi", ".mov"
            )

            if parsed.path.lower().endswith(blocked_extensions):
                continue

            if link not in visited:
                queue.append((link, depth + 1))

        time.sleep(CRAWL_DELAY)

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python crawler.py <URL>")
        sys.exit(1)

    seed_url = sys.argv[1]

    results = crawl(seed_url)

    print("\n==============================")
    print("Bharat Sahayaka Crawl Finished")
    print("==============================")
    print(f"Pages indexed: {len(results)}")

    for page in results:
        print("\nTITLE:", page["title"])
        print("URL:", page["url"])
        print("STATUS:", page["status_code"])
        print("HASH:", page["content_hash"])


if __name__ == "__main__":
    main()
