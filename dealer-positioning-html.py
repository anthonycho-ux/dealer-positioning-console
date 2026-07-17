#!/usr/bin/env python3
"""Dealer Positioning Console v2 — multidimensional reaction map.

Institutional palette: near-black, neutral gray, amber accent.
Restrained blue = estimated buy, muted rust = estimated sell.
No emoji, gauges, fake scores, or external dependencies.
"""
import json
import sys
from pathlib import Path
from html import escape

REPORTS = Path(__file__).resolve().parent / 'reports'
DATA = REPORTS / 'dealer-data.json'
OUT = REPORTS / 'dealer-positioning.html'

DTE_LABELS = {
    '0-7d': '0–7d',
    '8-30d': '8–30d',
    '31-90d': '31–90d',
    '91-180d': '91–180d',
    '180d+': '180d+',
}

EVIDENCE_LINKS = [
    ('https://youtu.be/4-aYHDpZH8Y?t=565',
     'Open interest, calendar expiries, and the reaction function (Option Alpha 9:25)'),
    ('https://youtu.be/4-aYHDpZH8Y?t=2886',
     'Basic positioning data is only a simplified view; track time and price (Option Alpha 48:06)'),
    ('https://youtu.be/fUDf7oKXc5Y?t=479',
     'Vanna/charm event-vol buyback mechanism (ReSolve Riffs 7:59)'),
    ('https://optionalpha.com/interviews/cem-karsan',
     'Full interview and transcript'),
]


def load():
    return json.loads(DATA.read_text(encoding='utf-8'))


def fmt_b(x, signed=True):
    if x is None:
        return '—'
    if abs(x) < 0.005:
        x = 0.0
    sign = '+' if (signed and x >= 0) else ''
    return f'{sign}{x:.2f}B' if x else '0.00B'


def fmt_price(x):
    if x is None:
        return '—'
    return f'{x:,.0f}'


def fmt_pct(x, signed=True):
    if x is None:
        return '—'
    if abs(x) < 0.005:
        x = 0.0
    sign = '+' if (signed and x >= 0) else ''
    return f'{sign}{x:.2f}%' if x else '0.00%'


def narrative_base_state(d):
    spot = d.get('spot')
    net_gex = d.get('net_gex_b')
    flip = d.get('gamma_flip')
    dist = d.get('dist_to_flip')
    regime = d.get('regime', '')
    parts = [f'SPX {fmt_price(spot)}']
    if net_gex is not None:
        parts.append(f'net gamma exposure {fmt_b(net_gex)} ({regime} gamma regime)')
    if flip and dist is not None:
        parts.append(f'flip at {fmt_price(flip)} ({fmt_pct(dist)})')
    return '. '.join(parts) + '.'


def narrative_failure_state(d):
    flip = d.get('gamma_flip')
    dist = d.get('dist_to_flip')
    net_gex = d.get('net_gex_b') or 0
    near_call = d.get('near_call_wall')
    near_put = d.get('near_put_wall')
    risks = []
    if dist is not None and abs(dist) < 1.0:
        risks.append('spot within 1% of gamma flip — regime can invert on small moves')
    if net_gex > 0 and dist is not None and dist < 0:
        risks.append('headline positive gamma but spot below flip — local instability')
    if net_gex < 0:
        risks.append('negative gamma — dealer hedging tends to amplify directional moves')
    if near_call and near_put and d.get('spot'):
        span = abs(near_call - near_put) / d['spot'] * 100
        if span < 3:
            risks.append(f'near walls compressed ({span:.1f}% band) — pinning risk into OPEX')
    if not risks:
        risks.append('no acute boundary breach; monitor IV co-movement via reaction map')
    return 'Failure state if: ' + '; '.join(risks) + '.'


def narrative_clock(d):
    opex = d.get('opex_date', '')
    d2o = d.get('days_to_opex')
    nearest = d.get('nearest_exp', '')
    surf = d.get('dealer_surface') or {}
    opex_bucket = surf.get('opex_bucket', '')
    if d2o is not None and d2o <= 7:
        urgency = 'OPEX window active'
    elif d2o is not None and d2o <= 14:
        urgency = 'OPEX approaching'
    else:
        urgency = 'Early cycle'
    return (f'Clock: next OPEX {opex} (T+{d2o}d, bucket {opex_bucket}). '
            f'Nearest expiry {nearest}. {urgency}.')


def decision_summary(d):
    """Translate the model snapshot into four plain-language decisions."""
    net_gex = d.get('net_gex_b')
    spot = d.get('spot')
    flip = d.get('gamma_flip')
    d2o = d.get('days_to_opex')

    if net_gex is None:
        state = '판단 자료 부족'
        state_note = '순감마 자료가 없어 현재 반응을 판정할 수 없다.'
        action = '방향 판단을 보류한다'
        action_note = '새 자료가 들어올 때까지 구조 신호를 쓰지 않는다.'
        state_class = 'neutral'
    elif net_gex < 0:
        state = '변동 확대 구간'
        state_note = f'순감마 {fmt_b(net_gex)} · SPX {fmt_price(spot)}'
        action = '작은 움직임도 커질 수 있다'
        action_note = '반전 확인 전 방향 확신을 낮춘다.'
        state_class = 'amplify'
    else:
        state = '변동 완충 구간'
        state_note = f'순감마 {fmt_b(net_gex)} · SPX {fmt_price(spot)}'
        action = '급한 움직임은 되돌림을 확인한다'
        action_note = '헤지가 가격을 현 수준으로 되미는 구조가 우세하다.'
        state_class = 'stabilize'

    if flip:
        reversal = f'SPX {fmt_price(flip)}'
        reversal_note = '이 가격을 통과하면 감마 체제가 뒤집힐 수 있다.'
    else:
        reversal = '순감마가 0 위로 바뀔 때'
        reversal_note = '정확한 반전 가격은 현재 계산되지 않았다.'

    if d2o is not None and d2o <= 0:
        clock = '오늘 만기'
        clock_note = '장중 구조가 빠르게 바뀔 수 있다.'
    elif d2o is not None and d2o <= 7:
        clock = f'만기까지 {d2o}일'
        clock_note = '만기 효과가 이미 가까운 구간이다.'
    else:
        clock = f'만기까지 {d2o}일' if d2o is not None else '만기 시계 없음'
        clock_note = '시간 경과에 따른 변화를 계속 확인한다.'

    return {
        'state': state,
        'state_note': state_note,
        'state_class': state_class,
        'action': action,
        'action_note': action_note,
        'reversal': reversal,
        'reversal_note': reversal_note,
        'clock': clock,
        'clock_note': clock_note,
        'confidence': '보통',
        'confidence_note': '지연 자료 · 전일 미결제약정 · 모형 추정',
    }


def today_changes(d):
    trend = d.get('trend') or {}
    items = []
    if trend.get('regime_switch'):
        if (d.get('net_gex_b') or 0) < 0:
            items.append('완충에서 확대로 감마 체제가 바뀌었다.')
        else:
            items.append('확대에서 완충으로 감마 체제가 바뀌었다.')
    gex_change = trend.get('gex_change')
    if gex_change is not None and abs(gex_change) >= 0.005:
        direction = '늘었다' if gex_change > 0 else '줄었다'
        items.append(f'순감마가 {abs(gex_change):.2f}B {direction}.')
    spot_change = trend.get('spot_change')
    if spot_change is not None and abs(spot_change) >= 0.5:
        direction = '올랐다' if spot_change > 0 else '내렸다'
        items.append(f'SPX가 {abs(spot_change):.0f}포인트 {direction}.')
    return items[:3] or ['비교 가능한 전일 변화가 없다.']


def heat_color(val, max_abs):
    if max_abs <= 0 or val == 0:
        return '#1a1a1a', '#888'
    intensity = min(abs(val) / max_abs, 1.0)
    alpha = 0.15 + intensity * 0.75
    if val > 0:
        return f'rgba(90,143,176,{alpha:.2f})', '#d8d8d8'
    return f'rgba(180,100,80,{alpha:.2f})', '#d8d8d8'


def build_heatmap_rows(surface, metric, opex_bucket):
    cells = surface.get('cells', {})
    dte_buckets = surface.get('dte_buckets', list(DTE_LABELS.keys()))
    m_buckets = surface.get('moneyness_buckets', [])
    atm = surface.get('atm_bucket', '0%')

    values = []
    for dte in dte_buckets:
        for m in m_buckets:
            raw = cells.get(dte, {}).get(m, {}).get(metric, 0) or 0
            values.append(raw / 1e9 if metric != 'active' else raw)
    max_abs = max((abs(v) for v in values if metric != 'active'), default=1) or 1

    rows = []
    for m in reversed(m_buckets):
        is_atm = m == atm
        cells_html = []
        for dte in dte_buckets:
            cell = cells.get(dte, {}).get(m, {})
            if metric == 'active':
                val = cell.get('active', 0)
                display = str(int(val))
                bg, fg = '#141414', '#707070'
            else:
                val = (cell.get(metric, 0) or 0) / 1e9
                bg, fg = heat_color(val, max_abs)
                display = fmt_b(val)
            opex_mark = ' opex-col' if dte == opex_bucket else ''
            tip = (f'{m} / {dte} · {metric.upper()}: {display}'
                   f' · GEX {fmt_b(cell.get("gex",0)/1e9)}'
                   f' · dGEX {fmt_b(cell.get("dgex",0)/1e9)}'
                   f' · Vanna {fmt_b(cell.get("vanna",0)/1e9)}'
                   f' · active {cell.get("active",0)}')
            atm_cls = ' atm-row' if is_atm else ''
            cells_html.append(
                f'<td class="hm-cell{opex_mark}{atm_cls}" data-m="{escape(m)}" data-d="{escape(dte)}" '
                f'data-gex="{(cell.get("gex",0)/1e9):.4f}" data-dgex="{(cell.get("dgex",0)/1e9):.4f}" '
                f'data-vanna="{(cell.get("vanna",0)/1e9):.4f}" data-active="{cell.get("active",0)}" '
                f'style="background:{bg};color:{fg}" title="{escape(tip)}">{display}</td>'
            )
        atm_label = ' atm-row' if is_atm else ''
        rows.append(
            f'<tr class="{atm_label.strip()}"><th class="hm-sticky{atm_label}">{escape(m)}</th>'
            + ''.join(cells_html) + '</tr>'
        )
    return rows, max_abs


def build_reaction_map(matrix):
    if not matrix or 'cells' not in matrix:
        return '<p class="muted">Scenario matrix unavailable.</p>', ''
    spot_cols = matrix.get('spot_shocks_pct', [-2, 0, 2])
    iv_rows = matrix.get('iv_shocks_pt', [5, 0, -5])
    cells = matrix['cells']
    header = ''.join(f'<th>Spot {s:+d}%</th>' for s in spot_cols)
    body_rows = []
    for i, iv_pt in enumerate(iv_rows):
        row_cells = []
        for j, spot_pct in enumerate(spot_cols):
            cell = cells[i][j] if i < len(cells) and j < len(cells[i]) else {}
            demand = cell.get('hedge_demand_b', 0)
            dom = cell.get('dominant', '—')
            dom_label = {
                'gamma': '감마',
                'vanna': '반나',
                'charm': '참',
                'neutral': '중립',
            }.get(str(dom).lower(), str(dom))
            if demand > 0:
                cls = 'buy'
                side = '매수 추정'
            elif demand < 0:
                cls = 'sell'
                side = '매도 추정'
            else:
                cls = 'neutral'
                side = '중립'
            row_cells.append(
                f'<td class="rx-cell {cls}">'
                f'<div class="rx-val mono">{fmt_b(demand)}</div>'
                f'<div class="rx-side">{side}</div>'
                f'<div class="rx-dom">주요축: {escape(dom_label)}</div>'
                f'</td>'
            )
        body_rows.append(
            f'<tr><th class="hm-sticky">변동성 {iv_pt:+d}pt</th>{"".join(row_cells)}</tr>'
        )
    table = f'''
    <table class="rx-table">
      <thead><tr><th class="hm-sticky"></th>{header}</tr></thead>
      <tbody>{"".join(body_rows)}</tbody>
    </table>'''
    note = escape(matrix.get('label', ''))
    return table, note


def build_term_clock(d):
    be = d.get('by_expiry', {})
    if not be:
        return '<p class="muted">No expiry data.</p>'
    data_time = d.get('data_time', '')[:10]
    from datetime import datetime
    try:
        spot_date = datetime.strptime(data_time, '%Y-%m-%d')
    except Exception:
        spot_date = None
    rows = []
    for exp, v in sorted(be.items())[:6]:
        dte = '—'
        if spot_date:
            try:
                dte = (datetime.strptime(exp, '%Y-%m-%d') - spot_date).days
            except Exception:
                pass
        gex = v.get('net', 0) / 1e9
        bar_w = min(abs(gex) * 8, 48)
        bar_c = '#5a8fb0' if gex >= 0 else '#b46450'
        rows.append(
            f'<div class="clock-row">'
            f'<span class="clock-exp">{escape(exp)}</span>'
            f'<span class="clock-dte mono">{dte}d</span>'
            f'<span class="clock-bar"><span style="width:{bar_w:.0f}%;background:{bar_c}"></span></span>'
            f'<span class="clock-val mono">{fmt_b(gex)}</span>'
            f'</div>'
        )
    return ''.join(rows)


def build_levels_bar(d):
    spot = d.get('spot')
    if not spot:
        return ''
    grouped = {}
    for price, label in [
        (d.get('call_wall'), 'call wall'),
        (d.get('near_call_wall'), 'near call'),
        (d.get('gamma_flip'), 'gamma flip'),
        (spot, 'spot'),
        (d.get('near_put_wall'), 'near put'),
        (d.get('put_wall'), 'put wall'),
    ]:
        if price:
            grouped.setdefault(float(price), []).append(label)
    levels = list(grouped.items())
    if not levels:
        return ''
    lo = min(p for p, _ in levels) * 0.998
    hi = max(p for p, _ in levels) * 1.002
    rng = hi - lo or 1
    parts = []
    for price, labels in sorted(levels, key=lambda x: -x[0]):
        pos = (hi - price) / rng * 100
        joined = ' · '.join(labels)
        if labels == ['call wall', 'near call', 'near put', 'put wall']:
            joined = 'call/put walls · near walls'
        if 'spot' in labels:
            kind = 'spot'
        elif 'gamma flip' in labels:
            kind = 'flip'
        elif any('call' in x for x in labels) and any('put' in x for x in labels):
            kind = 'mixed'
        elif any('call' in x for x in labels):
            kind = 'call'
        else:
            kind = 'put'
        parts.append(
            f'<div class="lv-row" style="top:{pos:.1f}%">'
            f'<div class="lv-line lv-{kind}"></div>'
            f'<span class="lv-lbl mono">{fmt_price(price)}</span>'
            f'<span class="lv-desc">{escape(joined)} ({fmt_pct((price-spot)/spot*100)})</span>'
            f'</div>'
        )
    return ''.join(parts)


def build_html(d):
    spot = d.get('spot')
    net_gex = d.get('net_gex_b')
    dgex = d.get('net_delta_gex_b')
    vega_bal = d.get('net_vex_b')
    net_vanna = d.get('net_vanna_b')
    data_time = d.get('data_time', '')
    opex = d.get('opex_date')
    d2o = d.get('days_to_opex')
    trend = d.get('trend') or {}
    surface = d.get('dealer_surface') or {}
    opex_bucket = surface.get('opex_bucket', '0-7d')
    matrix = d.get('scenario_matrix') or {}

    hm_rows, _ = build_heatmap_rows(surface, 'gex', opex_bucket)
    dte_headers = ''.join(
        f'<th class="{"opex-col" if t == opex_bucket else ""}">{escape(DTE_LABELS.get(t, t))}</th>'
        for t in surface.get('dte_buckets', list(DTE_LABELS.keys()))
    )
    rx_table, rx_note = build_reaction_map(matrix)

    evidence_items = ''.join(
        f'<li><a href="{escape(url)}" target="_blank" rel="noopener">{escape(lbl)}</a></li>'
        for url, lbl in EVIDENCE_LINKS
    )

    assumptions = d.get('assumptions') or {}
    assum_lines = ''.join(
        f'<li>{escape(str(k))}: {escape(str(v))}</li>'
        for k, v in assumptions.items()
    )

    base_n = escape(narrative_base_state(d))
    fail_n = escape(narrative_failure_state(d))
    clock_n = escape(narrative_clock(d))

    cem_en = ('A dealer book is not a wall. It is weather over terrain. Price is the terrain, '
              'expiry is altitude, volatility is pressure, and gamma/vanna are winds. '
              'Marking one strike as resistance is useful only until the atmosphere moves.')
    cem_ko = ('딜러 포지션은 벽이 아니라 지형 위의 날씨다. 가격이 지형, 만기가 고도, '
              '변동성이 기압, 감마/반나가 바람이다. 하나의 행사가를 저항으로 표시하는 것은 '
              '대기가 움직이기 전까지만 유용하다.')

    trend_gex = trend.get('gex_change')
    trend_spot = trend.get('spot_change')
    decision = decision_summary(d)
    change_items = ''.join(f'<li>{escape(item)}</li>' for item in today_changes(d))

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dealer Positioning · SPX v2</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0a0a; color: #c8c8c8;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 1.5; padding: 16px;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  .mono {{ font-family: 'SF Mono', Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #666; font-size: 12px; }}
  .accent {{ color: #d4a017; }}

  header {{
    border-bottom: 1px solid #2a2a2a; padding-bottom: 10px; margin-bottom: 14px;
    display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: baseline;
  }}
  header h1 {{ font-size: 15px; font-weight: 650; color: #f0f0f0; letter-spacing: 0.03em; }}
  header .meta {{ font-size: 11px; color: #666; }}

  .decision-board {{ display: grid; grid-template-columns: 1.55fr 0.85fr; gap: 14px; margin-bottom: 14px; }}
  .answers {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #292929; border: 1px solid #303030; }}
  .answer {{ background: #0e0e0e; padding: 14px 16px; min-height: 108px; }}
  .answer .eyebrow {{ display: block; color: #777; font-size: 10px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 7px; }}
  .answer strong {{ display: block; color: #ededed; font-size: 19px; line-height: 1.2; letter-spacing: -.02em; }}
  .answer p {{ margin-top: 7px; color: #888; font-size: 12px; }}
  .answer.now {{ border-left: 3px solid #d4a017; }}
  .answer.now.amplify strong {{ color: #e2b132; }}
  .answer.now.stabilize strong {{ color: #96afbf; }}
  .change-card {{ background: #0e0e0e; border: 1px solid #303030; padding: 14px 16px; display: flex; flex-direction: column; }}
  .change-card h2 {{ color: #999; font-size: 10px; letter-spacing: .08em; text-transform: uppercase; }}
  .change-card ol {{ list-style: none; counter-reset: changes; margin-top: 10px; }}
  .change-card li {{ position: relative; padding: 8px 0 8px 26px; border-bottom: 1px solid #202020; color: #d0d0d0; font-size: 13px; }}
  .change-card li::before {{ counter-increment: changes; content: counter(changes); position: absolute; left: 0; color: #d4a017; font-family: 'SF Mono', Menlo, monospace; }}
  .confidence {{ margin-top: auto; padding-top: 12px; color: #737373; font-size: 11px; }}
  .confidence b {{ display: inline-block; color: #c6c6c6; border: 1px solid #3a3a3a; padding: 3px 7px; margin-right: 6px; font-weight: 600; }}

  .narrative {{
    background: #0e0e0e; border: 1px solid #2a2a2a; padding: 10px 12px; margin-bottom: 14px;
    font-size: 12px; color: #b0b0b0;
  }}
  .narrative p {{ margin-bottom: 6px; }}
  .narrative p:last-child {{ margin-bottom: 0; }}
  .narrative .lbl {{ color: #888; text-transform: uppercase; font-size: 10px; letter-spacing: 0.06em; }}

  .strip {{
    display: grid; grid-template-columns: repeat(6, 1fr); gap: 1px;
    background: #222; border: 1px solid #2a2a2a; margin-bottom: 14px;
  }}
  .strip .c {{ background: #0e0e0e; padding: 8px 10px; }}
  .strip .k {{ font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }}
  .strip .v {{ font-size: 14px; color: #e8e8e8; margin-top: 2px; }}
  .strip .pos {{ color: #6a9aba; }}
  .strip .neg {{ color: #b46450; }}

  .grid2 {{ display: grid; grid-template-columns: 1.35fr 1fr; gap: 14px; margin-bottom: 14px; }}
  @media (max-width: 800px) {{ .grid2 {{ grid-template-columns: 1fr; }} .strip {{ grid-template-columns: repeat(3, 1fr); }} }}

  .panel {{ background: #0e0e0e; border: 1px solid #2a2a2a; margin-bottom: 14px; }}
  .panel-h {{
    font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 8px 12px; border-bottom: 1px solid #2a2a2a; font-weight: 600;
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px;
  }}
  .panel-h .note {{ color: #777; font-weight: 400; text-transform: none; letter-spacing: 0; }}
  .panel-b {{ padding: 10px 12px; }}

  .metric-btns {{ display: flex; gap: 4px; }}
  .metric-btns button {{
    background: #1a1a1a; border: 1px solid #333; color: #888; font-size: 10px;
    min-height: 36px; min-width: 54px; padding: 7px 10px; cursor: pointer; font-family: inherit;
  }}
  .metric-btns button.active {{ border-color: #d4a017; color: #d4a017; background: #1a1608; }}

  .hm-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  .hm-table {{ border-collapse: collapse; width: max-content; min-width: 100%; font-size: 11px; }}
  .hm-table th, .hm-table td {{ padding: 4px 6px; text-align: right; border: 1px solid #1e1e1e; }}
  .hm-sticky {{
    position: sticky; left: 0; z-index: 2; background: #0e0e0e; text-align: left !important;
    color: #999; min-width: 52px;
  }}
  .hm-cell {{ min-width: 58px; font-size: 10px; }}
  .atm-row .hm-sticky, .atm-row td {{ box-shadow: inset 0 1px 0 #d4a01744, inset 0 -1px 0 #d4a01744; }}
  .opex-col {{ box-shadow: inset 2px 0 0 #d4a01755; }}
  thead .opex-col {{ color: #d4a017; }}

  .rx-table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
  .rx-table th, .rx-table td {{ border: 1px solid #222; padding: 8px; text-align: center; vertical-align: middle; }}
  .rx-cell.buy {{ background: #0a1218; }}
  .rx-cell.sell {{ background: #140e0c; }}
  .rx-cell.neutral {{ background: #111; }}
  .rx-val {{ font-size: 14px; font-weight: 600; }}
  .rx-cell.buy .rx-val {{ color: #5a8fb0; }}
  .rx-cell.sell .rx-val {{ color: #b46450; }}
  .rx-side {{ font-size: 10px; color: #666; margin-top: 2px; }}
  .rx-dom {{ font-size: 10px; color: #888; margin-top: 2px; }}

  .side-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 640px) {{ .side-grid {{ grid-template-columns: 1fr; }} }}

  .levels {{ position: relative; height: 200px; background: #0a0a0a; border: 1px solid #222; }}
  .lv-row {{ position: absolute; left: 0; right: 0; display: flex; align-items: center; gap: 8px; font-size: 10px; }}
  .lv-line {{ width: 55%; height: 1px; margin-left: 4%; }}
  .lv-line.lv-spot {{ height: 2px; background: #e8e8e8; }}
  .lv-line.lv-flip {{ background: #d4a017; }}
  .lv-line.lv-mixed {{ background: #8c7a52; }}
  .lv-line.lv-call {{ background: #b46450; }}
  .lv-line.lv-put {{ background: #5a8fb0; }}
  .lv-lbl {{ color: #ccc; font-weight: 600; }}
  .lv-desc {{ color: #666; }}

  .clock-row {{
    display: grid; grid-template-columns: 72px 32px 1fr 52px; gap: 6px; align-items: center;
    font-size: 11px; padding: 3px 0; border-bottom: 1px solid #1a1a1a;
  }}
  .clock-bar {{ height: 4px; background: #1a1a1a; }}
  .clock-bar span {{ display: block; height: 100%; }}
  .clock-val {{ text-align: right; }}

  .synthesis {{ font-size: 12px; color: #b0b0b0; line-height: 1.55; }}
  .synthesis .tag {{ font-size: 10px; color: #d4a017; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
  .synthesis .ko {{ margin-top: 8px; color: #888; font-size: 11px; }}

  details.evidence {{ border: 1px solid #2a2a2a; background: #0e0e0e; margin-bottom: 14px; }}
  details.evidence summary {{
    padding: 8px 12px; cursor: pointer; font-size: 10px; color: #888;
    text-transform: uppercase; letter-spacing: 0.06em; list-style: none;
  }}
  details.evidence summary::-webkit-details-marker {{ display: none; }}
  details.evidence .ev-body {{ padding: 0 12px 12px; font-size: 12px; }}
  details.evidence ul {{ margin: 8px 0 0 16px; }}
  details.evidence li {{ margin-bottom: 6px; }}
  details.evidence a {{ color: #5a8fb0; text-decoration: none; }}
  details.evidence a:hover {{ color: #7ab0cc; }}

  details.raw-data {{ border: 1px solid #2a2a2a; background: #0c0c0c; margin-bottom: 14px; }}
  details.raw-data > summary {{ cursor: pointer; min-height: 46px; padding: 12px; color: #9a9a9a; font-size: 11px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; list-style: none; display: flex; align-items: center; justify-content: space-between; }}
  details.raw-data > summary::-webkit-details-marker {{ display: none; }}
  details.raw-data > summary::after {{ content: '펼치기'; color: #d4a017; font-size: 10px; letter-spacing: 0; }}
  details.raw-data[open] > summary::after {{ content: '접기'; }}
  .raw-inner {{ padding: 0 12px 12px; }}
  .raw-inner .strip {{ margin-bottom: 12px; }}
  .raw-inner .panel {{ margin-bottom: 0; }}

  details.lens {{ border: 1px solid #2a2a2a; background: #0e0e0e; margin-bottom: 14px; }}
  details.lens summary {{ min-height: 44px; padding: 11px 12px; cursor: pointer; list-style: none; color: #d4a017; font-size: 10px; letter-spacing: .06em; text-transform: uppercase; }}
  details.lens summary::-webkit-details-marker {{ display: none; }}
  details.lens .synthesis {{ padding: 0 12px 12px; }}

  footer {{ font-size: 10px; color: #555; border-top: 1px solid #222; padding-top: 10px; margin-top: 8px; }}

  @media (max-width: 800px) {{
    body {{ padding: 10px; font-size: 14px; }}
    header {{ display: block; padding-bottom: 12px; margin-bottom: 12px; }}
    header h1 {{ font-size: 15px; }}
    header .meta {{ margin-top: 5px; font-size: 10px; }}
    .decision-board {{ grid-template-columns: 1fr; gap: 10px; margin-bottom: 12px; }}
    .answers {{ grid-template-columns: 1fr; }}
    .answer {{ min-height: 0; padding: 12px 14px; }}
    .answer strong {{ font-size: 18px; }}
    .answer p {{ font-size: 12px; margin-top: 5px; }}
    .change-card {{ padding: 12px 14px; }}
    .change-card li {{ padding-top: 7px; padding-bottom: 7px; }}
    .confidence {{ margin-top: 8px; }}
    .panel {{ margin-bottom: 12px; }}
    .panel-h {{ min-height: 46px; padding: 8px 10px; }}
    .panel-b {{ padding: 10px; }}
    .metric-btns button {{ min-height: 44px; min-width: 62px; font-size: 11px; }}
    .hm-table {{ min-width: 620px; font-size: 11px; }}
    .hm-table th, .hm-table td {{ padding: 6px 8px; }}
    .hm-cell {{ min-width: 70px; font-size: 11px; }}
    .rx-table th, .rx-table td {{ padding: 10px 4px; }}
    .rx-val {{ font-size: 15px; }}
    .raw-inner {{ padding: 0 8px 8px; }}
    .synthesis .en {{ color: #666; font-size: 10px; margin-top: 8px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>DEALER POSITIONING <span class="muted">SPX · v2 reaction map</span></h1>
  <div class="meta">{escape(data_time)} · CBOE delayed · OPEX {escape(str(opex))} T+{d2o}</div>
</header>

<section class="decision-board" aria-label="현재 판단">
  <div class="answers">
    <article class="answer now {decision['state_class']}">
      <span class="eyebrow">지금 · Now</span>
      <strong>{escape(decision['state'])}</strong>
      <p class="mono">{escape(decision['state_note'])}</p>
    </article>
    <article class="answer">
      <span class="eyebrow">대응 · Action</span>
      <strong>{escape(decision['action'])}</strong>
      <p>{escape(decision['action_note'])}</p>
    </article>
    <article class="answer">
      <span class="eyebrow">반전 · Break</span>
      <strong>{escape(decision['reversal'])}</strong>
      <p>{escape(decision['reversal_note'])}</p>
    </article>
    <article class="answer">
      <span class="eyebrow">시간 · Clock</span>
      <strong>{escape(decision['clock'])}</strong>
      <p>{escape(decision['clock_note'])}</p>
    </article>
  </div>
  <aside class="change-card">
    <h2>오늘 달라진 점 · What changed</h2>
    <ol>{change_items}</ol>
    <p class="confidence"><b>신뢰도 {escape(decision['confidence'])}</b>{escape(decision['confidence_note'])}</p>
  </aside>
</section>

<div class="panel hero-map">
  <div class="panel-h">반응 지도 · Reaction map <span class="note">관측 흐름이 아닌 모형 추정</span></div>
  <div class="panel-b">
    {rx_table}
    <p class="muted" style="margin-top:8px">{rx_note}</p>
  </div>
</div>

<details class="raw-data" open>
  <summary>원자료와 노출 표면 · Raw data</summary>
  <div class="raw-inner">
    <div class="strip">
      <div class="c"><div class="k">SPX</div><div class="v mono">{fmt_price(spot)}</div></div>
      <div class="c"><div class="k">Net GEX</div><div class="v mono {'pos' if (net_gex or 0)>=0 else 'neg'}">{fmt_b(net_gex)}</div></div>
      <div class="c"><div class="k">Net dGEX</div><div class="v mono {'pos' if (dgex or 0)>=0 else 'neg'}">{fmt_b(dgex)}</div></div>
      <div class="c"><div class="k">Vega balance</div><div class="v mono {'pos' if (vega_bal or 0)>=0 else 'neg'}">{fmt_b(vega_bal)}</div></div>
      <div class="c"><div class="k">Net Vanna</div><div class="v mono {'pos' if (net_vanna or 0)>=0 else 'neg'}">{fmt_b(net_vanna)}</div></div>
      <div class="c"><div class="k">Prior day</div><div class="v mono">GEX {fmt_b(trend_gex)} · SPX {(f"{trend_spot:+.0f} pts" if trend_spot is not None else "—")}</div></div>
    </div>
    <div class="panel">
    <div class="panel-h">
      <span>노출 표면 · Exposure surface</span>
      <div class="metric-btns">
        <button type="button" class="active" data-metric="gex">GEX</button>
        <button type="button" data-metric="dgex">dGEX</button>
        <button type="button" data-metric="vanna">Vanna</button>
      </div>
    </div>
    <div class="panel-b hm-scroll">
      <table class="hm-table" id="heatmap">
        <thead><tr><th class="hm-sticky">Moneyness</th>{dte_headers}</tr></thead>
        <tbody id="hm-body">
          {''.join(hm_rows)}
        </tbody>
      </table>
      <p class="muted" style="margin-top:8px">Y = moneyness vs spot · X = DTE bucket · amber row = ATM · amber column = OPEX bucket</p>
    </div>
  </div>
  </div>
</details>

<div class="side-grid">
  <div class="panel">
    <div class="panel-h">Key boundaries</div>
    <div class="panel-b"><div class="levels">{build_levels_bar(d)}</div></div>
  </div>
  <div class="panel">
    <div class="panel-h">Term clock <span class="note">nearest expiries · net GEX</span></div>
    <div class="panel-b">{build_term_clock(d)}</div>
  </div>
</div>

<details class="lens">
  <summary>Cem식 해석의 틀 · 합성 설명, 직접 인용 아님</summary>
  <div class="synthesis">
    <p class="ko">{escape(cem_ko)}</p>
    <p class="en">{escape(cem_en)}</p>
  </div>
</details>

<details class="evidence">
  <summary>Evidence and assumptions</summary>
  <div class="ev-body">
    <p class="muted">Sources and limitations governing this console.</p>
    <ul>{evidence_items}</ul>
    <ul style="margin-top:10px">{assum_lines}</ul>
  </div>
</details>

<footer>Computed locally from CBOE SPX chain · Scenario cells are model estimates · Vega balance is not vanna · Charm unavailable</footer>
</div>

<script>
(function() {{
  var maxAbs = {{ gex: 0, dgex: 0, vanna: 0 }};
  document.querySelectorAll('.hm-cell').forEach(function(td) {{
    ['gex','dgex','vanna'].forEach(function(m) {{
      var v = parseFloat(td.getAttribute('data-' + m) || '0');
      if (Math.abs(v) > maxAbs[m]) maxAbs[m] = Math.abs(v);
    }});
  }});
  if (maxAbs.gex === 0) maxAbs.gex = 1;
  if (maxAbs.dgex === 0) maxAbs.dgex = 1;
  if (maxAbs.vanna === 0) maxAbs.vanna = 1;

  function colorFor(val, mx) {{
    if (Math.abs(val) < 0.005) val = 0;
    if (!mx || !val) return {{ bg: '#1a1a1a', fg: '#888', txt: '0.00B' }};
    var t = Math.min(Math.abs(val) / mx, 1);
    var a = 0.15 + t * 0.75;
    var sign = val >= 0 ? '+' : '';
    var txt = sign + val.toFixed(2) + 'B';
    if (val > 0) return {{ bg: 'rgba(90,143,176,' + a.toFixed(2) + ')', fg: '#d8d8d8', txt: txt }};
    return {{ bg: 'rgba(180,100,80,' + a.toFixed(2) + ')', fg: '#d8d8d8', txt: txt }};
  }}

  function applyMetric(metric) {{
    document.querySelectorAll('.hm-cell').forEach(function(td) {{
      var val = parseFloat(td.getAttribute('data-' + metric) || '0');
      var c = colorFor(val, maxAbs[metric]);
      td.style.background = c.bg;
      td.style.color = c.fg;
      td.textContent = c.txt;
    }});
    document.querySelectorAll('.metric-btns button').forEach(function(btn) {{
      btn.classList.toggle('active', btn.getAttribute('data-metric') === metric);
    }});
  }}

  document.querySelectorAll('.metric-btns button').forEach(function(btn) {{
    btn.addEventListener('click', function() {{ applyMetric(btn.getAttribute('data-metric')); }});
  }});
  if (window.matchMedia('(max-width: 800px)').matches) {{
    var raw = document.querySelector('details.raw-data');
    if (raw) raw.removeAttribute('open');
  }}
}})();
</script>
</body>
</html>'''
    return html


def main():
    d = load()
    html = build_html(d)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding='utf-8')
    print(f'OK {OUT} ({len(html):,} bytes)', file=sys.stderr)


if __name__ == '__main__':
    main()
