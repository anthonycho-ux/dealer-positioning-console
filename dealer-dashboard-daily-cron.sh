#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPORTS="$ROOT/reports"
DASHBOARD_BASE_URL=${DASHBOARD_BASE_URL:-http://127.0.0.1:9090}
export ROOT REPORTS DASHBOARD_BASE_URL

cd "$ROOT"
if ! update_output=$(python3 "$ROOT/update-dealer-dashboard.py" 2>&1); then
  printf '%s\n' "$update_output" >&2
  exit 1
fi

# 전문가용 포지셔닝 콘솔 HTML 갱신
python3 "$ROOT/dealer-positioning-html.py"

# 포지셔닝 스크린샷 갱신 (Chromium 가능 시)
if command -v google-chrome &>/dev/null; then
  google-chrome --headless --disable-gpu --screenshot="$REPORTS/dealer-positioning.png" --window-size=1200,1100 --hide-scrollbars --force-device-scale-factor=2 "$DASHBOARD_BASE_URL/dealer-positioning.html" >/dev/null 2>&1 || true
elif command -v chromium &>/dev/null; then
  chromium --headless --disable-gpu --screenshot="$REPORTS/dealer-positioning.png" --window-size=1200,1100 --hide-scrollbars --force-device-scale-factor=2 "$DASHBOARD_BASE_URL/dealer-positioning.html" >/dev/null 2>&1 || true
fi

if [[ -f "$REPORTS/dealer-data.json" ]]; then
  python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["REPORTS"]) / "dealer-data.json"
base_url = os.environ["DASHBOARD_BASE_URL"].rstrip("/")
data = json.loads(path.read_text(encoding="utf-8"))

def level(value):
    if value is None:
        return "n/a"
    return f"{float(value):,.0f}"

def signed_b(value):
    if value is None:
        return "n/a"
    return f"{float(value):+.2f}B"

def signed_pct(value):
    if value is None:
        return "n/a"
    return f"{float(value):+.2f}%"

def wall_move(value):
    if value is None:
        return "비교값 없음"
    value = float(value)
    if abs(value) < 0.5:
        return "유지"
    return f"{value:+,.0f}"

def strike_line(item):
    strike, gex_b, call_oi, put_oi = item
    return f"{level(strike)} {signed_b(gex_b)} GEX · C/P OI {level(call_oi)}/{level(put_oi)}"

trend = data.get("trend", {}) or {}
top = data.get("top_strikes", [])[:3]
regime = str(data.get("regime", "unknown")).upper()
regime_note = (
    "딜러 헤지가 움직임을 누르는 쪽"
    if regime == "POSITIVE"
    else "딜러 헤지가 움직임을 키울 수 있는 쪽"
    if regime == "NEGATIVE"
    else "방향 확인 필요"
)

def distance_pct(strike, spot):
    if strike is None or not spot:
        return None
    return (float(strike) / float(spot) - 1.0) * 100.0

# ── 기관급 리드 (term structure 기반, Cem 렌즈) ──
def _lead_line(d):
    be = d.get("by_expiry", {})
    spot_dt = d.get("data_time", "")[:10]
    gex_total = d.get("net_gex_b") or 0
    vanna_terms = {"0–30일": 0.0, "31–90일": 0.0, "91일+": 0.0}
    for exp, v in be.items():
        try:
            dte = (__import__("datetime").datetime.strptime(exp, "%Y-%m-%d") - __import__("datetime").datetime.strptime(spot_dt, "%Y-%m-%d")).days
        except Exception:
            continue
        vv = (v.get("vanna_net") or 0) / 1e9
        if dte <= 30:
            vanna_terms["0–30일"] += vv
        elif dte <= 90:
            vanna_terms["31–90일"] += vv
        else:
            vanna_terms["91일+"] += vv
    parts = []
    if gex_total > 0:
        parts.append("양감마 표면, 딜러가 변동 억제")
    elif gex_total < 0:
        parts.append("음감마, 딜러가 변동 증폭")
    dist = d.get("dist_to_flip")
    if dist is not None and dist < 0:
        parts.append(f"현물이 플립 {abs(dist):.1f}% 아래 — 국소적 불안정")
    if d.get("net_vanna_b") is not None:
        dominant_term, dominant_vanna = max(vanna_terms.items(), key=lambda item: abs(item[1]))
        parts.append(
            f"실제 반나 {d.get('net_vanna_b'):+.1f}B/vol pt, "
            f"{dominant_term} 집중({dominant_vanna:+.1f}B) — IV 방향에 따라 헤지 반전"
        )
    if d.get("days_to_opex", 99) <= 2:
        parts.append(f"OPEX 임박(T+{d.get('days_to_opex')}) — 헤지 롤오프 영향 실물화")
    return " · ".join(parts) if parts else "—"

_lead = _lead_line(data)

print("Dealer Dashboard Daily — 핵심")
print(f"SPX {level(data.get('spot'))} · {regime} · {regime_note} · Net GEX {signed_b(data.get('net_gex_b'))}")
print(
    f"Delta GEX {signed_b(data.get('net_delta_gex_b'))} · "
    f"Vega balance {signed_b(data.get('net_vex_b'))} · "
    f"True Vanna {signed_b(data.get('net_vanna_b'))}/vol pt · "
    f"GEX 변화 {signed_b(trend.get('gex_change'))}"
)
print(f"Gamma flip {level(data.get('gamma_flip'))} ({signed_pct(data.get('dist_to_flip'))} from spot) · OPEX {data.get('opex_date')} (T+{data.get('days_to_opex')})")

# ── 딜러 포지셔닝 리드 (term structure 기반) ──
print(f"딜러 포지셔닝: {_lead}")
near_call_pct = distance_pct(data.get('near_call_wall'), data.get('spot'))
near_put_pct = distance_pct(data.get('near_put_wall'), data.get('spot'))
near_call_text = signed_pct(near_call_pct)
near_put_text = signed_pct(near_put_pct)
print(f"   근접 콜월 {level(data.get('near_call_wall'))} ({near_call_text}) / 근접 풋월 {level(data.get('near_put_wall'))} ({near_put_text}) · 콘솔: {base_url}/dealer-positioning.html")
print(
    "근접 Wall (현물 ±5%, 단기 판단 우선): "
    f"Call {level(data.get('near_call_wall'))} ({wall_move(trend.get('near_call_wall_change'))}) / "
    f"Put {level(data.get('near_put_wall'))} ({wall_move(trend.get('near_put_wall_change'))})"
)
print(
    "구조 Wall (모든 만기, 장기 집중대): "
    f"Call {level(data.get('call_wall'))} ({wall_move(trend.get('call_wall_change'))}) / "
    f"Put {level(data.get('put_wall'))} ({wall_move(trend.get('put_wall_change'))})"
)
print("읽는 법: Call은 상승 쪽 압력 후보, Put은 하락 쪽 압력 후보지만 고정 지지·저항은 아님.")
print("왜 오래 머무나: 구조 Wall은 장기·원거리 옵션까지 합쳐 큰 물량이 남으면 7,000·8,000 같은 가격에 오래 고정될 수 있음.")
if data.get('put_wall') is not None and data.get('spot') is not None and float(data['put_wall']) > float(data['spot']):
    print("주의: 구조 Put Wall이 현물 위에 있으므로 현재 지지선이 아니라 전체 옵션 집중대로 해석.")
if top:
    print("Top strikes: " + " | ".join(strike_line(item) for item in top))
print(f"Legacy brief: {base_url}/dealer-brief.html")
print(f"Positioning console: {base_url}/dealer-positioning.html")
PY
else
  echo "Dealer dashboard updated. Report JSON not found."
fi
