from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


DEFAULT_NEWS_QUERIES = [
    "cryptocurrency OR bitcoin OR ethereum OR altcoin when:1d",
    "crypto hack OR exploit OR delisting OR lawsuit when:1d",
    "bitcoin ETF OR ethereum ETF OR SEC crypto when:1d",
    "solana OR xrp OR chainlink OR dogecoin crypto when:1d",
    "\uc554\ud638\ud654\ud3d0 OR \ube44\ud2b8\ucf54\uc778 OR \uc774\ub354\ub9ac\uc6c0 OR \uc54c\ud2b8\ucf54\uc778 when:1d",
    "site:tokenpost.kr \uac00\uc0c1\uc790\uc0b0 OR \uc554\ud638\ud654\ud3d0 OR \ube44\ud2b8\ucf54\uc778 OR \uc0c1\uc7a5 OR \uaddc\uc81c when:1d",
    "site:blockmedia.co.kr \uac00\uc0c1\uc790\uc0b0 OR \ube44\ud2b8\ucf54\uc778 OR \uc774\ub354\ub9ac\uc6c0 OR \uc54c\ud2b8\ucf54\uc778 OR \ud574\ud0b9 when:1d",
    "site:digitalasset.works \uac00\uc0c1\uc790\uc0b0 OR \uac70\ub798\uc18c OR \uc0c1\uc7a5 OR \uc720\uc758\uc885\ubaa9 OR \uaddc\uc81c when:1d",
    "site:decenter.kr \uac00\uc0c1\uc790\uc0b0 OR \uc554\ud638\ud654\ud3d0 OR \ube44\ud2b8\ucf54\uc778 OR \uc54c\ud2b8\ucf54\uc778 when:1d",
    "site:coinreaders.com \ube44\ud2b8\ucf54\uc778 OR \uc774\ub354\ub9ac\uc6c0 OR \uc54c\ud2b8\ucf54\uc778 OR \uae09\ub4f1 OR \uae09\ub77d when:1d",
    "site:coinness.com \ube44\ud2b8\ucf54\uc778 OR \uc774\ub354\ub9ac\uc6c0 OR \uc5c5\ube44\ud2b8 OR \uc0c1\uc7a5 OR \ud574\ud0b9 when:1d",
    "site:zdnet.co.kr \uac00\uc0c1\uc790\uc0b0 OR \uc554\ud638\ud654\ud3d0 OR \ube14\ub85d\uccb4\uc778 OR \uac70\ub798\uc18c when:1d",
    "site:upbit.com/service_center/notice \uc5c5\ube44\ud2b8 OR \uac70\ub798\uc9c0\uc6d0 OR \uc0c1\uc7a5 OR \uc720\uc758\uc885\ubaa9 when:7d",
    "site:bithumb.com \uac70\ub798\uc9c0\uc6d0 OR \uc0c1\uc7a5 OR \uc720\uc758\uc885\ubaa9 when:7d",
    "site:coinone.co.kr \uac70\ub798\uc9c0\uc6d0 OR \uc0c1\uc7a5 OR \uc720\uc758\uc885\ubaa9 when:7d",
]

DEFAULT_RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://kr.cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

POSITIVE_KEYWORDS = {
    "listing",
    "listed",
    "partnership",
    "upgrade",
    "mainnet",
    "approval",
    "approved",
    "etf",
    "funding",
    "adoption",
    "integrates",
    "surge",
    "record high",
    "\uac70\ub798\uc9c0\uc6d0",
    "\uc0c1\uc7a5",
    "\ud30c\ud2b8\ub108\uc2ed",
    "\uc5c5\uadf8\ub808\uc774\ub4dc",
    "\uba54\uc778\ub137",
    "\uc2b9\uc778",
    "\ud638\uc7ac",
    "\uae09\ub4f1",
    "\ucc44\ud0dd",
}

NEGATIVE_KEYWORDS = {
    "hack",
    "hacked",
    "exploit",
    "lawsuit",
    "sec",
    "delist",
    "delisting",
    "ban",
    "investigation",
    "fraud",
    "outage",
    "crash",
    "plunge",
    "warning",
    "\ud574\ud0b9",
    "\uc18c\uc1a1",
    "\uac70\ub798\uc9c0\uc6d0 \uc885\ub8cc",
    "\uc0c1\uc7a5\ud3d0\uc9c0",
    "\uc0c1\ud3d0",
    "\uc720\uc758\uc885\ubaa9",
    "\uaddc\uc81c",
    "\uc870\uc0ac",
    "\uc0ac\uae30",
    "\uae09\ub77d",
    "\uc7a5\uc560",
}

CRITICAL_KEYWORDS = {
    "delist",
    "delisting",
    "\uac70\ub798\uc9c0\uc6d0 \uc885\ub8cc",
    "\uc0c1\uc7a5\ud3d0\uc9c0",
    "\uc0c1\ud3d0",
    "exploit",
    "hack",
    "hacked",
    "\ud574\ud0b9",
    "fraud",
    "\uc0ac\uae30",
}


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    summary: str = ""
    source: str = ""
    published: str = ""

    def text(self) -> str:
        return " ".join([self.title, self.summary, self.source]).strip()


@dataclass
class InfoSignal:
    market_scores: dict[str, Decimal] = field(default_factory=dict)
    market_reasons: dict[str, list[str]] = field(default_factory=dict)
    blocked_markets: set[str] = field(default_factory=set)
    global_risk_score: Decimal = Decimal("0")
    article_count: int = 0
    summary: str = ""
    errors: list[str] = field(default_factory=list)

    def market_score(self, market: str) -> Decimal:
        return self.market_scores.get(market, Decimal("0")) + self.global_risk_score

    def merge(self, other: "InfoSignal") -> None:
        for market, score in other.market_scores.items():
            self.market_scores[market] = _clamp(
                self.market_scores.get(market, Decimal("0")) + score,
                Decimal("-1"),
                Decimal("1"),
            )
        for market, reasons in other.market_reasons.items():
            self.market_reasons.setdefault(market, []).extend(reasons)
        self.blocked_markets.update(other.blocked_markets)
        self.global_risk_score = _clamp(
            self.global_risk_score + other.global_risk_score,
            Decimal("-1"),
            Decimal("1"),
        )
        self.article_count = max(self.article_count, other.article_count)
        if other.summary:
            self.summary = other.summary
        self.errors.extend(other.errors)


class NewsCollector:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def collect(self, limit: int = 40) -> list[NewsItem]:
        feeds = self._feed_urls()
        items: list[NewsItem] = []
        seen: set[str] = set()
        for feed in feeds:
            try:
                for item in self._read_feed(feed):
                    identity = item.link or item.title
                    if identity in seen:
                        continue
                    seen.add(identity)
                    items.append(item)
                    if len(items) >= limit:
                        return items
            except Exception:
                continue
        return items

    def _feed_urls(self) -> list[str]:
        raw = os.getenv("AI_NEWS_FEEDS", "").strip()
        if raw:
            return [part.strip() for part in raw.split(",") if part.strip()]

        feeds = list(DEFAULT_RSS_FEEDS)
        feeds.extend(
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=ko&gl=KR&ceid=KR:ko"
            for query in self._news_queries()
        )

        extra = os.getenv("AI_EXTRA_NEWS_FEEDS", "").strip()
        if extra:
            feeds.extend(part.strip() for part in extra.split(",") if part.strip())
        return feeds

    def _news_queries(self) -> list[str]:
        raw = os.getenv("AI_NEWS_QUERIES", "").strip()
        if raw:
            return [part.strip() for part in raw.split("|") if part.strip()]
        queries = list(DEFAULT_NEWS_QUERIES)
        extra = os.getenv("AI_EXTRA_NEWS_QUERIES", "").strip()
        if extra:
            queries.extend(part.strip() for part in extra.split("|") if part.strip())
        return queries

    def _read_feed(self, url: str) -> list[NewsItem]:
        request = Request(url, headers={"User-Agent": "upbit-auto-trader/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except URLError:
            return []
        root = ElementTree.fromstring(raw)
        return self._rss_items(root) + self._atom_items(root, url)

    def _rss_items(self, root: ElementTree.Element) -> list[NewsItem]:
        items = []
        for node in root.findall(".//item"):
            title = _node_text(node, "title")
            link = _node_text(node, "link")
            summary = _node_text(node, "description")
            source = _node_text(node, "source") or _source_from_url(link)
            published = _node_text(node, "pubDate")
            if title:
                items.append(
                    NewsItem(
                        title=_clean_html(title),
                        link=link,
                        summary=_clean_html(summary),
                        source=_clean_html(source),
                        published=published,
                    )
                )
        return items

    def _atom_items(self, root: ElementTree.Element, feed_url: str) -> list[NewsItem]:
        items = []
        for node in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = _node_text(node, "{http://www.w3.org/2005/Atom}title")
            link = _atom_link(node)
            summary = _node_text(node, "{http://www.w3.org/2005/Atom}summary")
            if not summary:
                summary = _node_text(node, "{http://www.w3.org/2005/Atom}content")
            published = _node_text(node, "{http://www.w3.org/2005/Atom}published")
            if not published:
                published = _node_text(node, "{http://www.w3.org/2005/Atom}updated")
            if title:
                items.append(
                    NewsItem(
                        title=_clean_html(title),
                        link=link,
                        summary=_clean_html(summary),
                        source=_source_from_url(link or feed_url),
                        published=published,
                    )
                )
        return items


class KeywordInfoAnalyzer:
    def analyze(self, markets: list[dict[str, Any]], articles: list[NewsItem]) -> InfoSignal:
        signal = InfoSignal(article_count=len(articles))
        term_map = self._market_terms(markets)
        for article in articles:
            text = article.text()
            normalized = text.lower()
            base_score = self._article_score(normalized)
            mentioned = self._mentioned_markets(normalized, term_map)
            if not mentioned:
                if self._looks_like_crypto_news(normalized):
                    signal.global_risk_score = _clamp(
                        signal.global_risk_score + min(base_score, Decimal("0")) / Decimal("5"),
                        Decimal("-1"),
                        Decimal("1"),
                    )
                continue

            reason = self._reason(article, base_score)
            for market in mentioned:
                signal.market_scores[market] = _clamp(
                    signal.market_scores.get(market, Decimal("0")) + base_score,
                    Decimal("-1"),
                    Decimal("1"),
                )
                signal.market_reasons.setdefault(market, []).append(reason)
                if self._has_critical_keyword(normalized):
                    signal.blocked_markets.add(market)
        return signal

    def _market_terms(self, markets: list[dict[str, Any]]) -> dict[str, set[str]]:
        term_map: dict[str, set[str]] = {}
        for item in markets:
            market = str(item.get("market", "")).upper()
            if "-" not in market:
                continue
            symbol = market.split("-", 1)[1]
            terms = {symbol.lower()}
            korean = str(item.get("korean_name", "")).strip().lower()
            english = str(item.get("english_name", "")).strip().lower()
            if korean:
                terms.add(korean)
            if english:
                terms.add(english)
            term_map[market] = {term for term in terms if len(term) >= 2}
        return term_map

    def _mentioned_markets(self, text: str, term_map: dict[str, set[str]]) -> set[str]:
        mentioned: set[str] = set()
        for market, terms in term_map.items():
            for term in terms:
                if len(term) <= 3 and term.isascii():
                    if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
                        mentioned.add(market)
                        break
                elif term in text:
                    mentioned.add(market)
                    break
        return mentioned

    def _article_score(self, text: str) -> Decimal:
        score = Decimal("0")
        for keyword in POSITIVE_KEYWORDS:
            if keyword in text:
                score += Decimal("0.20")
        for keyword in NEGATIVE_KEYWORDS:
            if keyword in text:
                score -= Decimal("0.30")
        for keyword in CRITICAL_KEYWORDS:
            if keyword in text:
                score -= Decimal("0.25")
        return _clamp(score, Decimal("-1"), Decimal("1"))

    def _reason(self, article: NewsItem, score: Decimal) -> str:
        label = "neutral"
        if score > 0:
            label = "positive"
        elif score < 0:
            label = "negative"
        return f"{label}: {article.source}: {article.title[:160]}"

    def _looks_like_crypto_news(self, text: str) -> bool:
        return any(
            term in text
            for term in [
                "crypto",
                "bitcoin",
                "ethereum",
                "coin",
                "\uc554\ud638\ud654\ud3d0",
                "\ube44\ud2b8\ucf54\uc778",
                "\ucf54\uc778",
            ]
        )

    def _has_critical_keyword(self, text: str) -> bool:
        return any(keyword in text for keyword in CRITICAL_KEYWORDS)


class OpenAIInfoAnalyzer:
    def __init__(self, model: str | None = None, timeout: float = 20.0) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
        self.timeout = timeout

    def analyze(self, markets: list[dict[str, Any]], articles: list[NewsItem]) -> InfoSignal:
        if not self.api_key:
            return InfoSignal(article_count=len(articles), errors=["OPENAI_API_KEY is not set"])
        payload = self._request_payload(markets, articles)
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return InfoSignal(
                article_count=len(articles),
                errors=[f"OpenAI analysis failed: HTTP {exc.code}: {_read_http_error(exc)}"],
            )
        except Exception as exc:
            return InfoSignal(article_count=len(articles), errors=[f"OpenAI analysis failed: {exc}"])
        text = self._extract_text(data)
        if not text.strip():
            status = data.get("status", "unknown")
            incomplete = data.get("incomplete_details", {})
            return InfoSignal(
                article_count=len(articles),
                errors=[f"OpenAI response text was empty: status={status}, incomplete_details={incomplete}"],
            )
        try:
            parsed = json.loads(_strip_json_fence(text))
        except json.JSONDecodeError as exc:
            return InfoSignal(article_count=len(articles), errors=[f"OpenAI JSON parse failed: {exc}"])
        return self._signal_from_json(parsed, len(articles))

    def _request_payload(self, markets: list[dict[str, Any]], articles: list[NewsItem]) -> dict[str, Any]:
        compact_markets = [
            {
                "market": item.get("market"),
                "korean_name": item.get("korean_name"),
                "english_name": item.get("english_name"),
            }
            for item in markets[:250]
        ]
        compact_articles = [
            {
                "title": item.title,
                "summary": item.summary[:300],
                "source": item.source,
                "published": item.published,
            }
            for item in articles[:50]
        ]
        prompt = {
            "markets": compact_markets,
            "articles": compact_articles,
            "schema": {
                "market_scores": [{"market": "KRW-BTC", "score": -1.0}],
                "global_risk_score": -0.2,
                "blocked_markets": ["KRW-ABC"],
                "summary": "short Korean summary",
            },
        }
        return {
            "model": self.model,
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "You analyze cryptocurrency news for a trading risk engine. "
                        "Return only valid JSON. Scores must be between -1 and 1. "
                        "Use negative scores for hacks, lawsuits, delistings, exchange warnings, or regulation risk. "
                        "Use positive scores for listings, credible adoption, mainnet upgrades, partnerships, or ETF approvals. "
                        "Do not invent facts beyond the provided articles."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "crypto_news_signal",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "market_scores": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "market": {"type": "string"},
                                        "score": {
                                            "type": "number",
                                            "minimum": -1,
                                            "maximum": 1,
                                        },
                                    },
                                    "required": ["market", "score"],
                                },
                            },
                            "global_risk_score": {
                                "type": "number",
                                "minimum": -1,
                                "maximum": 1,
                            },
                            "blocked_markets": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "summary": {"type": "string"},
                        },
                        "required": [
                            "market_scores",
                            "global_risk_score",
                            "blocked_markets",
                            "summary",
                        ],
                    },
                }
            },
            "reasoning": {"effort": "low"},
            "max_output_tokens": 2000,
        }

    def _signal_from_json(self, parsed: dict[str, Any], article_count: int) -> InfoSignal:
        signal = InfoSignal(article_count=article_count)
        for market, score in _market_score_items(parsed.get("market_scores", [])):
            market_name = str(market).upper()
            signal.market_scores[market_name] = _clamp(Decimal(str(score)), Decimal("-1"), Decimal("1"))
            signal.market_reasons.setdefault(market_name, []).append("openai_news_analysis")
        signal.global_risk_score = _clamp(
            Decimal(str(parsed.get("global_risk_score", "0"))),
            Decimal("-1"),
            Decimal("1"),
        )
        signal.blocked_markets = {str(market).upper() for market in parsed.get("blocked_markets", [])}
        signal.summary = str(parsed.get("summary", ""))[:500]
        return signal

    def _extract_text(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        parts: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "\n".join(parts)


class IntelligenceEngine:
    def __init__(self, use_openai: bool = False) -> None:
        self.collector = NewsCollector()
        self.keyword_analyzer = KeywordInfoAnalyzer()
        self.openai_analyzer = OpenAIInfoAnalyzer() if use_openai else None

    def evaluate(self, markets: list[dict[str, Any]], limit: int = 40) -> InfoSignal:
        started_at = datetime.now(timezone.utc).isoformat()
        articles = self.collector.collect(limit=limit)
        signal = self.keyword_analyzer.analyze(markets, articles)
        if self.openai_analyzer is not None and articles:
            signal.merge(self.openai_analyzer.analyze(markets, articles))
        if not signal.summary:
            signal.summary = f"Analyzed {len(articles)} articles at {started_at}."
        return signal


def _node_text(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _atom_link(node: ElementTree.Element) -> str:
    for child in node.findall("{http://www.w3.org/2005/Atom}link"):
        href = child.attrib.get("href", "").strip()
        if href:
            return href
    return ""


def _source_from_url(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


def _clean_html(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _market_score_items(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [(str(market), score) for market, score in value.items()]
    if isinstance(value, list):
        items: list[tuple[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            market = item.get("market")
            score = item.get("score")
            if market is not None and score is not None:
                items.append((str(market), score))
        return items
    return []


def _read_http_error(exc: HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    if not raw:
        return exc.reason
    return raw[:1000]


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))
