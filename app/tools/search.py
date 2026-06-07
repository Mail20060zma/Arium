import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote, urljoin

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
}
DEFAULT_TIMEOUT = 10
DEFAULT_LIMIT = 5

def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())

def extract_duckduckgo_target(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if "duckduckgo.com" not in parsed.netloc:
        return raw_url

    query = parse_qs(parsed.query)
    target = query.get("uddg")
    if target:
        return unquote(target[0])
    return raw_url

def make_absolute_url(base_url: str, raw_url: str) -> str:
    if not raw_url:
        return raw_url
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return urljoin(base_url, raw_url)

def search_web(query: str, limit: int = DEFAULT_LIMIT) -> dict:
    """
    Инструмент для поиска в интернете через DuckDuckGo HTML.
    """
    normalized_query = normalize_text(query)
    if not normalized_query:
        return {"error": "Поисковый запрос не может быть пустым."}

    url = "https://html.duckduckgo.com/html/"
    
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        
        response = session.post(
            url,
            data={"q": normalized_query},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for item in soup.select(".result"):
            link = item.select_one("a.result__a")
            if link is None:
                continue

            title = normalize_text(link.get_text(" ", strip=True))
            raw_url = make_absolute_url(url, link.get("href", "").strip())
            raw_url = extract_duckduckgo_target(raw_url)
            snippet_node = item.select_one(".result__snippet")
            snippet = normalize_text(
                snippet_node.get_text(" ", strip=True) if snippet_node else ""
            )

            if title and raw_url:
                results.append({
                    "title": title,
                    "url": raw_url,
                    "snippet": snippet
                })

            if len(results) >= limit:
                break

        if not results:
            return {"error": "DuckDuckGo не вернул результатов для данного запроса."}
            
        return {"query": query, "results": results}

    except requests.RequestException as e:
        logger.error(f"Ошибка поиска в сети по запросу '{query}': {e}")
        return {"error": f"Сетевая ошибка при поиске: {str(e)}"}
    except Exception as e:
        logger.error(f"Неизвестная ошибка при поиске: {e}")
        return {"error": str(e)}
