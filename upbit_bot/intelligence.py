from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree


DEFAULT_NEWS_QUERIES = [
    "crypto OR bitcoin OR ethereum when:1d",
    "암호화폐 OR 비트코인 OR 이더리움 when:1d",
    "site:upbit.com crypto OR 코인 OR 상장 OR 유의종목 when:7d",
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
    "상장",
    "파트너십",
    "업그레이드",
    "메인넷",
    "승인",
    "호재",
    "급등",
    "채택",
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
    "해킹",
    "소송",
    "상장폐지",
    "상폐",
    "유의종목",
    "규제",
    "조사",
    "사기",
    "급락",
    "장애",
}

CRITICAL_KEYWORDS = {
    "delist",
    "delisting",
    "상장폐지",
    "상폐",
    "exploit",
    "hack",
    "hacked",
    "해킹",
    "fraud",
    "사기",
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
        return [
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=ko&gl=KR&ceid=KR:ko"
            for query in DEFAULT_NEWS_QUERIES
        ]

    def _read_feed(self, url: str) -> list[NewsItem]:
        request = Request(url, headers={"User-Agent": "upbit-auto-trader/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except URLError:
            return []
        root = ElementTree.fromstring(raw)
        items = []
        for node in root.findall(".//item"):
            title = _node_text(node, "title")
            link = _node_text(node, "link")
            summary = _node_text(node, "description")
            source = _node_text(node, "source")
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
        return f"{label}: {article.title[:160]}"

    def _looks_like_crypto_news(self, text: str) -> bool:
        return any(term in text for term in ["crypto", "bitcoin", "ethereum", "coin", "암호화폐", "비트코인", "코인"])

    def _has_critical_keyword(self, text: str) -> bool:
        return any(keyword in text for keyword in CRITICAL_KEYWORDS)


class OpenAIInfoAnalyzer:
    def __init__(self, model: str | None = None, timeout: float = 20.0) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5").strip()
        self.timeout = timeout

    def analyze(self, markets: list[dict[str, Any]], articles: list[NewsItem]) -> InfoSignal:
        if not self.api_key:
            return InfoSignal(article_count=len(articles), errors=["OPENAI_API_KEY is not set"])
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
            for item in articles[:30]
        ]
        prompt = {
            "markets": compact_markets,
            "articles": compact_articles,
            "schema": {
                "market_scores": {"KRW-BTC": -1.0},
                "global_risk_score": -0.2,
                "blocked_markets": ["KRW-ABC"],
                "summary": "short Korean summary",
            },
        }
        payload = {
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
            "max_output_tokens": 900,
        }
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
        except Exception as exc:
            return InfoSignal(article_count=len(articles), errors=[f"OpenAI analysis failed: {exc}"])
        text = self._extract_text(data)
        try:
            parsed = json.loads(_strip_json_fence(text))
        except json.JSONDecodeError as exc:
            return InfoSignal(article_count=len(articles), errors=[f"OpenAI JSON parse failed: {exc}"])
        return self._signal_from_json(parsed, len(articles))

    def _signal_from_json(self, parsed: dict[str, Any], article_count: int) -> InfoSignal:
        signal = InfoSignal(article_count=article_count)
        for market, score in dict(parsed.get("market_scores", {})).items():
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


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))
