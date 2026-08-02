from __future__ import annotations

import argparse
import html
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_LOG_FILE = Path("upbit_auto_trader.jsonl")
DEFAULT_STATE_FILE = Path(".upbit_auto_state.json")
DEFAULT_STOP_FILE = Path(".upbit_bot_stop")


@dataclass(frozen=True)
class StatusWebConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    log_file: Path = DEFAULT_LOG_FILE
    state_file: Path = DEFAULT_STATE_FILE
    stop_file: Path = DEFAULT_STOP_FILE
    stale_after_seconds: int = 900
    recent_limit: int = 25


def run_status_server(config: StatusWebConfig) -> None:
    server = ThreadingHTTPServer((config.host, config.port), _handler_for(config))
    print(f"Status web listening on http://{config.host}:{config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_status(
    log_file: Path = DEFAULT_LOG_FILE,
    state_file: Path = DEFAULT_STATE_FILE,
    stop_file: Path = DEFAULT_STOP_FILE,
    stale_after_seconds: int = 900,
    recent_limit: int = 25,
) -> dict[str, Any]:
    events = read_recent_events(log_file, max_lines=max(recent_limit, 100))
    recent_events = events[-recent_limit:]
    last_event = events[-1] if events else None
    last_cycle = _last_event(events, "cycle")
    last_error = _last_event(events, "error")
    last_started = _last_event(events, "started")
    last_stopped = _last_event(events, "stopped")
    state = read_state(state_file)

    now = datetime.now(timezone.utc)
    last_cycle_age = _age_seconds(last_cycle, now)
    last_event_age = _age_seconds(last_event, now)

    level = "waiting"
    message = "아직 자동매매 사이클 로그가 없습니다."
    if stop_file.exists() or (last_stopped and _is_newer(last_stopped, last_started)):
        level = "stopped"
        message = "정지 상태입니다."
    elif last_error and _is_newer(last_error, last_cycle):
        level = "error"
        message = str(last_error.get("error", "최근 오류가 있습니다."))
    elif last_cycle:
        if last_cycle_age is not None and last_cycle_age > stale_after_seconds:
            level = "stale"
            message = "최근 사이클 로그가 오래되었습니다."
        else:
            level = "ok"
            message = "최근 사이클이 정상 기록되었습니다."

    return {
        "ok": level == "ok",
        "level": level,
        "message": message,
        "now": now.isoformat(),
        "log_file": str(log_file),
        "state_file": str(state_file),
        "stop_file": str(stop_file),
        "stop_requested": stop_file.exists(),
        "last_event_age_seconds": last_event_age,
        "last_cycle_age_seconds": last_cycle_age,
        "last_event": last_event,
        "last_cycle": last_cycle,
        "last_error": last_error,
        "state": state,
        "recent_events": recent_events,
    }


def read_recent_events(log_file: Path, max_lines: int = 100, max_bytes: int = 1_000_000) -> list[dict[str, Any]]:
    if not log_file.exists():
        return []
    try:
        with log_file.open("rb") as file:
            file.seek(0, 2)
            size = file.tell()
            start = max(0, size - max_bytes)
            file.seek(start)
            raw = file.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = raw.splitlines()
    if lines and not raw.startswith("{"):
        lines = lines[1:]

    events: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def read_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"exists": False, "positions": {}, "updated_at": None}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exists": True, "error": "state file could not be read", "positions": {}, "updated_at": None}
    if not isinstance(data, dict):
        return {"exists": True, "error": "state file is not an object", "positions": {}, "updated_at": None}
    positions = data.get("positions", {})
    if not isinstance(positions, dict):
        positions = {}
    return {"exists": True, "updated_at": data.get("updated_at"), "positions": positions}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upbit-status-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--stop-file", default=str(DEFAULT_STOP_FILE))
    parser.add_argument("--stale-after-seconds", type=int, default=900)
    parser.add_argument("--recent-limit", type=int, default=25)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_status_server(
        StatusWebConfig(
            host=args.host,
            port=args.port,
            log_file=Path(args.log_file),
            state_file=Path(args.state_file),
            stop_file=Path(args.stop_file),
            stale_after_seconds=args.stale_after_seconds,
            recent_limit=args.recent_limit,
        )
    )
    return 0


def _handler_for(config: StatusWebConfig) -> type[BaseHTTPRequestHandler]:
    def build_status_for_request(query: str) -> dict[str, Any]:
        params = parse_qs(query)
        recent_limit = _int_param(params, "recent", config.recent_limit)
        return build_status(
            log_file=config.log_file,
            state_file=config.state_file,
            stop_file=config.stop_file,
            stale_after_seconds=config.stale_after_seconds,
            recent_limit=recent_limit,
        )

    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "UpbitStatusWeb/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_render_page(build_status_for_request(parsed.query)))
                return
            if parsed.path == "/api/status":
                self._send_json(build_status_for_request(parsed.query))
                return
            if parsed.path == "/health":
                status = build_status_for_request(parsed.query)
                code = HTTPStatus.OK if status["level"] in {"ok", "waiting"} else HTTPStatus.SERVICE_UNAVAILABLE
                self._send_json(status, code=code)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str, code: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], code: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", mimetypes.types_map.get(".json", "application/json"))
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return StatusHandler


def _render_page(status: dict[str, Any]) -> str:
    last_cycle = status.get("last_cycle") or {}
    candidate = last_cycle.get("candidate") or {}
    positions = last_cycle.get("positions") or []
    actions = last_cycle.get("actions") or []
    info = last_cycle.get("info") or {}
    recent_events = status.get("recent_events") or []
    level = str(status.get("level", "waiting"))
    level_label = {
        "ok": "정상",
        "waiting": "대기",
        "stale": "지연",
        "error": "오류",
        "stopped": "정지",
    }.get(level, level)

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>Upbit Auto Trader Status</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #6b7280;
      --line: #d9dde3;
      --ok: #0f8f5f;
      --waiting: #4b6bfb;
      --stale: #b7791f;
      --error: #c53030;
      --stopped: #5f6672;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }}
    header {{ border-bottom: 1px solid var(--line); background: var(--panel); }}
    .wrap {{ width: min(1120px, calc(100vw - 32px)); margin: 0 auto; }}
    .top {{
      min-height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 700; }}
    .badge {{
      min-width: 72px;
      padding: 7px 10px;
      border-radius: 6px;
      color: white;
      text-align: center;
      font-weight: 700;
      background: var(--{level});
    }}
    main {{ padding: 20px 0 28px; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}
    .metric, section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric {{ min-height: 82px; padding: 12px; }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .value {{ font-size: 16px; font-weight: 700; overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 16px; }}
    section {{ padding: 14px; margin-bottom: 16px; }}
    h2 {{ margin: 0 0 12px; font-size: 15px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 9px 6px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 600; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .muted {{ color: var(--muted); }}
    .events {{ display: grid; gap: 8px; }}
    .event {{
      border-top: 1px solid var(--line);
      padding-top: 8px;
      display: grid;
      grid-template-columns: 168px 90px 1fr;
      gap: 10px;
      align-items: start;
    }}
    @media (max-width: 820px) {{
      .summary, .grid {{ grid-template-columns: 1fr; }}
      .top {{ align-items: flex-start; flex-direction: column; padding: 16px 0; }}
      .event {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap top">
      <div>
        <h1>Upbit Auto Trader</h1>
        <div class="muted">{_e(status.get("message"))}</div>
      </div>
      <div class="badge">{_e(level_label)}</div>
    </div>
  </header>
  <main class="wrap">
    <div class="summary">
      {_metric("마지막 사이클", _age_label(status.get("last_cycle_age_seconds")))}
      {_metric("현금 KRW", _e(last_cycle.get("cash", "-")))}
      {_metric("후보", _e(candidate.get("market", "-")))}
      {_metric("정지 신호", "있음" if status.get("stop_requested") else "없음")}
    </div>
    <div class="grid">
      <div>
        <section><h2>후보</h2>{_candidate_table(candidate)}</section>
        <section><h2>보유</h2>{_positions_table(positions)}</section>
      </div>
      <div>
        <section><h2>정보 분석</h2>{_info_table(info)}</section>
        <section><h2>최근 행동</h2>{_actions_table(actions)}</section>
      </div>
    </div>
    <section>
      <h2>최근 로그</h2>
      <div class="events">{_events_list(recent_events)}</div>
    </section>
  </main>
</body>
</html>"""


def _metric(label: str, value: str) -> str:
    return f'<div class="metric"><div class="label">{_e(label)}</div><div class="value">{value}</div></div>'


def _candidate_table(candidate: dict[str, Any]) -> str:
    if not candidate:
        return '<div class="muted">후보 없음</div>'
    return _kv_table(
        [
            ("마켓", candidate.get("market")),
            ("점수", candidate.get("score")),
            ("가격", candidate.get("trade_price")),
            ("변동률", _percent(candidate.get("change_rate"))),
            ("정보 점수", candidate.get("info_score")),
        ]
    )


def _positions_table(positions: list[dict[str, Any]]) -> str:
    if not positions:
        return '<div class="muted">보유 없음</div>'
    rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('market'))}</td>"
        f"<td>{_e(item.get('value_krw'))}</td>"
        f"<td>{_percent(item.get('pnl_rate'))}</td>"
        f"<td>{_e(item.get('trailing_stop_price') or '-')}</td>"
        "</tr>"
        for item in positions
    )
    return f"<table><thead><tr><th>마켓</th><th>평가금액</th><th>수익률</th><th>트레일링</th></tr></thead><tbody>{rows}</tbody></table>"


def _info_table(info: dict[str, Any]) -> str:
    if not info:
        return '<div class="muted">정보 없음</div>'
    return _kv_table(
        [
            ("요약", info.get("summary")),
            ("기사 수", info.get("article_count")),
            ("전체 위험 점수", info.get("global_risk_score")),
            ("차단 마켓", ", ".join(info.get("blocked_markets") or []) or "-"),
        ]
    )


def _actions_table(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return '<div class="muted">최근 행동 없음</div>'
    rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('type'))}</td>"
        f"<td>{_e(item.get('market'))}</td>"
        f"<td>{_e(item.get('reason'))}</td>"
        "</tr>"
        for item in actions
    )
    return f"<table><thead><tr><th>유형</th><th>마켓</th><th>이유</th></tr></thead><tbody>{rows}</tbody></table>"


def _events_list(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<div class="muted">로그 없음</div>'
    return "".join(
        '<div class="event">'
        f'<div class="mono">{_e(item.get("ts", "-"))}</div>'
        f'<div>{_e(item.get("event", "-"))}</div>'
        f'<div class="muted">{_e(_event_detail(item))}</div>'
        "</div>"
        for item in reversed(events)
    )


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(
        f"<tr><th>{_e(label)}</th><td>{_e(value if value not in {None, ''} else '-')}</td></tr>"
        for label, value in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _event_detail(item: dict[str, Any]) -> str:
    if item.get("event") == "error":
        return str(item.get("error", ""))
    if item.get("event") == "cycle":
        candidate = item.get("candidate") or {}
        actions = item.get("actions") or []
        return f"candidate={candidate.get('market', '-')} actions={len(actions)} cash={item.get('cash', '-')}"
    if item.get("event") == "started":
        return f"live={item.get('live')} once={item.get('once')}"
    if item.get("event") == "stopped":
        return f"reason={item.get('reason', '-')}"
    return json.dumps({k: v for k, v in item.items() if k not in {"ts", "event"}}, ensure_ascii=False)


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def _age_label(seconds: Any) -> str:
    if seconds is None:
        return "-"
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return "-"
    if value < 60:
        return f"{value}초 전"
    if value < 3600:
        return f"{value // 60}분 전"
    return f"{value // 3600}시간 {(value % 3600) // 60}분 전"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _last_event(events: list[dict[str, Any]], event_name: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event") == event_name:
            return event
    return None


def _age_seconds(event: dict[str, Any] | None, now: datetime) -> int | None:
    if not event:
        return None
    event_time = _parse_ts(event.get("ts"))
    if event_time is None:
        return None
    return max(0, int((now - event_time).total_seconds()))


def _is_newer(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    left_ts = _parse_ts(left.get("ts"))
    right_ts = _parse_ts(right.get("ts"))
    if left_ts is None:
        return False
    if right_ts is None:
        return True
    return left_ts >= right_ts


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int_param(params: dict[str, list[str]], name: str, default: int) -> int:
    raw_values = params.get(name)
    if not raw_values:
        return default
    try:
        value = int(raw_values[0])
    except ValueError:
        return default
    return max(1, min(value, 100))


if __name__ == "__main__":
    raise SystemExit(main())
