#!/usr/bin/env python3
"""
Dealer Positioning Brief — Full pipeline
=========================================
1. Fetch SPX option chain from CBOE delayed quotes (free)
2. Compute GEX profile (gamma × OI × 100 × spot)
3. Key levels: Net GEX, Gamma Flip, Call/Put Wall, Vanna regime
4. HTML report with Cem Karsan framework overlay
"""

import re, json, sys, os, time, math
from datetime import datetime, date, timedelta
from collections import defaultdict
import urllib.request
import yfinance as yf
import numpy as np

# ── Config ──────────────────────────────────────────────────────────
CBOE_URL = "https://www.cboe.com/delayed_quotes/spx/quote_table"
CBOE_HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
MULTIPLIER = 100

# BSM vanna assumptions (documented in output metadata)
VANNA_R = 0.04
VANNA_Q = 0.013

DTE_BUCKETS = ['0-7d', '8-30d', '31-90d', '91-180d', '180d+']
MONEYNESS_BUCKETS = ['<-5%', '-5%', '-4%', '-3%', '-2%', '-1%', '0%', '+1%', '+2%', '+3%', '+4%', '+5%', '>+5%']
SPOT_SHOCKS_PCT = [-2, 0, 2]
IV_SHOCKS_PT = [5, 0, -5]


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def classify_dte_bucket(dte: int) -> str:
    if dte <= 7:
        return '0-7d'
    if dte <= 30:
        return '8-30d'
    if dte <= 90:
        return '31-90d'
    if dte <= 180:
        return '91-180d'
    return '180d+'


def classify_moneyness_bucket(strike: float, spot: float) -> str:
    if strike <= 0 or spot <= 0:
        return '<-5%'
    pct = (strike / spot - 1.0) * 100.0
    if pct < -5.0:
        return '<-5%'
    if pct > 5.0:
        return '>+5%'
    if pct >= 0:
        return f'+{int(pct)}%' if int(pct) > 0 else '0%'
    return f'{int(pct)}%'


def empty_surface_cell() -> dict:
    return {'gex': 0.0, 'dgex': 0.0, 'vanna': 0.0, 'calls': 0.0, 'puts': 0.0, 'active': 0}


def init_dealer_surface() -> dict:
    return {
        dte: {m: empty_surface_cell() for m in MONEYNESS_BUCKETS}
        for dte in DTE_BUCKETS
    }


def compute_vanna_exposure(spot: float, strike: float, T: float, sigma: float,
                         oi: float, is_call: bool,
                         r: float = VANNA_R, q: float = VANNA_Q) -> float | None:
    """BSM vanna exposure per 1 IV percentage point (call+, put-)."""
    if sigma <= 0 or strike <= 0 or T <= 0 or oi <= 0:
        return None
    sqrt_t = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    vanna = -math.exp(-q * T) * _phi(d1) * d2 / sigma
    exposure = vanna * oi * MULTIPLIER * spot * 0.01
    return exposure if is_call else -exposure


def build_scenario_matrix(net_gex: float, net_vanna_per_iv_point: float, spot: float) -> dict:
    """3x3 spot/IV shock grid — model proxy, not observed flow."""
    cells = []
    for iv_pt in IV_SHOCKS_PT:
        row = []
        for spot_pct in SPOT_SHOCKS_PCT:
            delta_spot_dollars = spot * (spot_pct / 100.0)
            gamma_contrib = net_gex * delta_spot_dollars
            vanna_contrib = net_vanna_per_iv_point * iv_pt
            hedge_demand = -(gamma_contrib + vanna_contrib)
            abs_g = abs(gamma_contrib)
            abs_v = abs(vanna_contrib)
            if abs_g == 0 and abs_v == 0:
                dominant = 'neutral'
            elif abs_g >= abs_v:
                dominant = 'gamma'
            else:
                dominant = 'vanna'
            row.append({
                'spot_pct': spot_pct,
                'iv_pt': iv_pt,
                'hedge_demand_b': round(hedge_demand / 1e9, 3),
                'gamma_contrib_b': round(gamma_contrib / 1e9, 3),
                'vanna_contrib_b': round(vanna_contrib / 1e9, 3),
                'dominant': dominant,
            })
        cells.append(row)
    return {
        'label': 'Estimated dealer hedge demand (model proxy, not observed flow)',
        'spot_shocks_pct': SPOT_SHOCKS_PCT,
        'iv_shocks_pt': IV_SHOCKS_PT,
        'formula': '-(net_gex * deltaSpotDollars + net_vanna_per_iv_point * deltaIvPoints)',
        'sign_convention': 'Positive = estimated buy · Negative = estimated sell',
        'cells': cells,
    }

# ── Data Fetch ──────────────────────────────────────────────────────

def fetch_cboe_data(url=CBOE_URL, attempts=3, base_delay=5):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=CBOE_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode('utf-8')
            match = re.search(r'CTX\.contextOptionsData\s*=\s*(\{.+?\});', html, re.DOTALL)
            if not match:
                raise ValueError("CBOE options data not found in page")
            return json.loads(match.group(1))
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = base_delay * attempt
            print(
                f"⚠️ CBOE fetch attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}. Retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"CBOE fetch failed after {attempts} attempts: {type(last_error).__name__}: {last_error}"
    ) from last_error

def parse_symbol(sym):
    """Returns (exp_date, opt_type, strike)."""
    base = sym.split('SPX')[-1]
    if base[0] == 'W':
        yy = 2000 + int(base[1:3]); mm = int(base[3:5]); dd = int(base[5:7])
        opt_type = 'C' if base[7] == 'C' else 'P'
        strike = int(base[8:]) / 1000.0
    else:
        yy = 2000 + int(base[0:2]); mm = int(base[2:4]); dd = int(base[4:6])
        opt_type = 'C' if base[6] == 'C' else 'P'
        strike = int(base[7:]) / 1000.0
    return date(yy, mm, dd), opt_type, strike

def get_opex(today=None):
    if today is None: today = date.today()
    first = today.replace(day=1)
    friday = first + timedelta(days=(4 - first.weekday()) % 7)
    opex = friday + timedelta(days=14)
    # Keep today's monthly expiry visible as T0; advance only after it passes.
    if opex < today:
        if today.month == 12:
            first = today.replace(year=today.year+1, month=1, day=1)
        else:
            first = today.replace(month=today.month+1, day=1)
        friday = first + timedelta(days=(4 - first.weekday()) % 7)
        opex = friday + timedelta(days=14)
    return opex

# ── GEX Computation ─────────────────────────────────────────────────

def compute_profile(raw):
    data = raw['data']
    spot = data['current_price']
    options = data['options']
    today = date.today()

    # Per-strike aggregators
    by_strike = defaultdict(lambda: {
        'call_gamma_oi': 0, 'put_gamma_oi': 0,
        'call_oi': 0, 'put_oi': 0,
        'call_gex': 0.0, 'put_gex': 0.0,
        'call_delta_gex': 0.0, 'put_delta_gex': 0.0,
        'call_vex': 0.0, 'put_vex': 0.0,
        'call_vanna': 0.0, 'put_vanna': 0.0,
    })
    by_expiry = defaultdict(lambda: {
        'call_gex': 0.0, 'put_gex': 0.0, 'call_dgex': 0.0, 'put_dgex': 0.0,
        'call_vex': 0.0, 'put_vex': 0.0, 'call_vanna': 0.0, 'put_vanna': 0.0,
    })
    dealer_surface = init_dealer_surface()

    parsed_count = 0
    vanna_valid_count = 0
    vanna_skipped_count = 0
    for o in options:
        try:
            exp, typ, strike = parse_symbol(o['option'])
            # Keep same-day expiry: 0DTE gamma is often the dominant tradable layer.
            if exp < today:
                continue
            gamma = o.get('gamma', 0) or 0
            oi = o.get('open_interest', 0) or 0
            delta = o.get('delta', 0) or 0
            vega = o.get('vega', 0) or 0
            iv = o.get('iv', 0) or 0
            if gamma <= 0 or oi <= 0:
                continue

            gex_val = gamma * oi * MULTIPLIER * spot
            abs_delta = abs(delta)
            dgex_val = gex_val * abs_delta  # delta-adjusted GEX
            vex_val = vega * oi * MULTIPLIER  # vega exposure in $
            key = f"{exp.year}-{exp.month:02d}-{exp.day:02d}"
            dte = (exp - today).days
            dte_bucket = classify_dte_bucket(dte)
            m_bucket = classify_moneyness_bucket(strike, spot)
            surf = dealer_surface[dte_bucket][m_bucket]

            sd = by_strike[strike]
            ed = by_expiry[key]

            vanna_val = None
            # CBOE does not provide precise time-to-close here.  Use half a day
            # for 0DTE vanna instead of dropping the entire same-day layer.
            T = max(float(dte), 0.5) / 365.0
            if iv > 0 and strike > 0 and T > 0:
                vanna_val = compute_vanna_exposure(spot, strike, T, iv, oi, typ == 'C')
                if vanna_val is not None:
                    vanna_valid_count += 1
                else:
                    vanna_skipped_count += 1
            else:
                vanna_skipped_count += 1

            if typ == 'C':
                sd['call_gamma_oi'] += gamma * oi
                sd['call_oi'] += oi
                sd['call_gex'] += gex_val
                sd['call_delta_gex'] += dgex_val
                sd['call_vex'] += vex_val
                ed['call_gex'] += gex_val
                ed['call_dgex'] += dgex_val
                ed['call_vex'] += vex_val
                surf['calls'] += oi
                if vanna_val is not None:
                    sd['call_vanna'] += vanna_val
                    ed['call_vanna'] += vanna_val
                    surf['vanna'] += vanna_val
            else:
                sd['put_gamma_oi'] += gamma * oi
                sd['put_oi'] += oi
                sd['put_gex'] += gex_val  # positive magnitude
                sd['put_delta_gex'] += dgex_val
                sd['put_vex'] += vex_val
                ed['put_gex'] += gex_val
                ed['put_dgex'] += dgex_val
                ed['put_vex'] += vex_val
                surf['puts'] += oi
                if vanna_val is not None:
                    sd['put_vanna'] += vanna_val
                    ed['put_vanna'] += vanna_val
                    surf['vanna'] += vanna_val
            surf['gex'] += gex_val if typ == 'C' else -gex_val
            surf['dgex'] += dgex_val if typ == 'C' else -dgex_val
            surf['active'] += 1
            parsed_count += 1
        except:
            continue

    # Compute net GEX per strike (calls add, puts subtract — dealer hedging flow direction)
    strikes = sorted(by_strike.keys())
    gex_by_strike = {}
    cum_gex = 0
    cum_gex_map = {}

    for s in strikes:
        sd = by_strike[s]
        net = sd['call_gex'] - sd['put_gex']  # calls +, puts -
        gex_by_strike[s] = net
        cum_gex += net
        cum_gex_map[s] = cum_gex

    net_gex = cum_gex
    total_call_gex = sum(by_strike[s]['call_gex'] for s in strikes)
    total_put_gex = sum(by_strike[s]['put_gex'] for s in strikes)

    # Delta-adjusted GEX totals
    net_delta_gex = sum(by_strike[s]['call_delta_gex'] - by_strike[s]['put_delta_gex'] for s in strikes)
    total_call_dgex = sum(by_strike[s]['call_delta_gex'] for s in strikes)
    total_put_dgex = sum(by_strike[s]['put_delta_gex'] for s in strikes)

    # VEX totals (vega exposure — vega balance, NOT vanna)
    net_vex = sum(by_strike[s]['call_vex'] - by_strike[s]['put_vex'] for s in strikes)
    total_call_vex = sum(by_strike[s]['call_vex'] for s in strikes)
    total_put_vex = sum(by_strike[s]['put_vex'] for s in strikes)

    # True BSM vanna exposure (per 1 IV percentage point).
    # compute_vanna_exposure() already applies call+ / put- at ingestion,
    # so aggregate signed legs by addition (subtracting puts here would invert twice).
    total_call_vanna = sum(by_strike[s]['call_vanna'] for s in strikes)
    signed_put_vanna = sum(by_strike[s]['put_vanna'] for s in strikes)
    net_vanna = total_call_vanna + signed_put_vanna
    total_put_vanna = signed_put_vanna

    # Gamma Flip
    gamma_flip = None
    for i in range(len(strikes)-1):
        c1, c2 = cum_gex_map[strikes[i]], cum_gex_map[strikes[i+1]]
        if c1 <= 0 <= c2 or c1 >= 0 >= c2:
            s1, s2 = strikes[i], strikes[i+1]
            gamma_flip = s1 + (s2 - s1) * (0 - c1) / (c2 - c1) if (c2 - c1) != 0 else s1
            break

    # Walls: largest absolute GEX from each type (all strikes)
    call_wall = max(strikes, key=lambda s: by_strike[s]['call_gex'])
    put_wall = max(strikes, key=lambda s: by_strike[s]['put_gex'])

    # Delta-GEX walls
    dcall_wall = max(strikes, key=lambda s: by_strike[s]['call_delta_gex'])
    dput_wall = max(strikes, key=lambda s: by_strike[s]['put_delta_gex'])

    # Near-the-money walls (strikes within ±5% of spot)
    near_strikes = [s for s in strikes if 0.95 * spot <= s <= 1.05 * spot]
    if near_strikes:
        near_call_wall = max(near_strikes, key=lambda s: by_strike[s]['call_gex'])
        near_put_wall = max(near_strikes, key=lambda s: by_strike[s]['put_gex'])
    else:
        near_call_wall = call_wall
        near_put_wall = put_wall

    # Ten most significant strikes
    top_ten = sorted(strikes, key=lambda s: abs(gex_by_strike[s]), reverse=True)[:10]
    top_list = [(s, gex_by_strike[s], by_strike[s]['call_oi'], by_strike[s]['put_oi']) for s in top_ten]

    # Nearest expiry
    all_exps = sorted(set(k for k in by_expiry.keys()))
    nearest = all_exps[0] if all_exps else None

    # OPEX
    opex_date = get_opex(today)
    days_to_opex = (opex_date - today).days

    # Regime
    regime = 'positive' if net_gex > 0 else 'negative'
    dist = ((spot - gamma_flip) / spot * 100) if gamma_flip else None

    # GEX by expiry
    exp_gex = {}
    for k in sorted(by_expiry.keys()):
        e = by_expiry[k]
        exp_gex[k] = {'net': e['call_gex'] - e['put_gex'],
                      'calls': e['call_gex'], 'puts': e['put_gex'],
                      'dnet': e['call_dgex'] - e['put_dgex'],
                      'vnet': e['call_vex'] - e['put_vex'],
                      'vanna_net': e['call_vanna'] + e['put_vanna']}

    # Round dealer_surface values to JSON-safe floats
    for dte_key in dealer_surface:
        for m_key in dealer_surface[dte_key]:
            c = dealer_surface[dte_key][m_key]
            dealer_surface[dte_key][m_key] = {
                'gex': round(c['gex'], 2),
                'dgex': round(c['dgex'], 2),
                'vanna': round(c['vanna'], 2),
                'calls': int(c['calls']),
                'puts': int(c['puts']),
                'active': int(c['active']),
            }

    opex_bucket = classify_dte_bucket(days_to_opex)
    scenario_matrix = build_scenario_matrix(net_gex, net_vanna, spot)

    assumptions = {
        'data_source': 'CBOE SPX delayed option chain (~20 min lag)',
        'oi_lag': 'Open interest typically T+1; not intraday flow',
        'dealer_sign_convention': 'Call exposure positive, put exposure negative (dealer-side proxy)',
        'vega_balance_note': 'net_vex is vega*OI*100 — vega balance, not vanna',
        'vanna_model': 'Black-Scholes vanna from CBOE IV; r=0.04, q=0.013',
        'vanna_0dte_time_floor': '0.5/365 year because exact time-to-close is unavailable',
        'vanna_exposure_unit': 'Dollar notional per 1 IV percentage point (vanna*OI*100*spot*0.01)',
        'scenario_matrix_note': 'Model proxy for estimated hedge demand — not observed dealer flow',
        'inventory_unknown': 'True dealer inventory and trade direction not directly observable',
        'charm': 'unavailable/unvalidated — not computed from theta',
    }

    return {
        'spot': spot,
        'net_gex': net_gex,
        'net_gex_b': round(net_gex / 1e9, 2),
        'total_call_gex': round(total_call_gex / 1e9, 2),
        'total_put_gex': round(total_put_gex / 1e9, 2),
        'net_delta_gex': round(net_delta_gex / 1e9, 2),
        'net_delta_gex_b': round(net_delta_gex / 1e9, 2),
        'net_vex': round(net_vex / 1e9, 2),
        'net_vex_b': round(net_vex / 1e9, 2),
        'net_vex_meta': {
            'alias': 'vega_balance',
            'formula': 'vega * OI * 100',
            'note': 'Vega balance — not vanna. See net_vanna for true vanna exposure.',
        },
        'net_vanna': net_vanna,
        'net_vanna_b': round(net_vanna / 1e9, 2),
        'total_call_vanna': round(total_call_vanna / 1e9, 2),
        'total_put_vanna': round(total_put_vanna / 1e9, 2),
        'vanna_assumptions': {
            'r': VANNA_R,
            'q': VANNA_Q,
            'zero_dte_time_floor_years': 0.5 / 365.0,
            'formula_d1': 'd1=(ln(S/K)+(r-q+0.5*sigma^2)*T)/(sigma*sqrt(T))',
            'formula_vanna': 'vanna=-exp(-q*T)*phi(d1)*d2/sigma',
            'exposure': 'vanna*OI*100*spot*0.01 per 1 IV percentage point',
            'valid_count': vanna_valid_count,
            'skipped_count': vanna_skipped_count,
        },
        'dealer_surface': {
            'spot': spot,
            'dte_buckets': DTE_BUCKETS,
            'moneyness_buckets': MONEYNESS_BUCKETS,
            'atm_bucket': '0%',
            'opex_bucket': opex_bucket,
            'cells': dealer_surface,
        },
        'scenario_matrix': scenario_matrix,
        'assumptions': assumptions,
        'total_call_dgex': round(total_call_dgex / 1e9, 2),
        'total_put_dgex': round(total_put_dgex / 1e9, 2),
        'total_call_vex': round(total_call_vex / 1e9, 2),
        'total_put_vex': round(total_put_vex / 1e9, 2),
        'regime': regime,
        'gamma_flip': gamma_flip,
        'dist_to_flip': round(dist, 2) if dist is not None else None,
        'call_wall': call_wall,
        'put_wall': put_wall,
        'dcall_wall': dcall_wall,
        'dput_wall': dput_wall,
        'near_call_wall': near_call_wall,
        'near_put_wall': near_put_wall,
        'nearest_exp': str(nearest) if nearest else None,
        'opex_date': str(opex_date),
        'days_to_opex': days_to_opex,
        'top_strikes': [(s, round(g/1e9, 2), co, po) for s, g, co, po in top_list],
        'by_expiry': exp_gex,
        'num_options': len(options),
        'num_active': parsed_count,
        'data_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

# ── Bollinger Bands ─────────────────────────────────────────────────

def fetch_bollinger(lookback=30):
    """Fetch SPX daily data and compute 20-day Bollinger Bands (2σ)."""
    import pandas as pd
    try:
        spx = yf.download('^SPX', period=f'{lookback}d', progress=False, auto_adjust=True)
        if spx.empty:
            return None
        close = spx['Close'].squeeze()
        if isinstance(close, (int, float, np.integer, np.floating)):
            return None
        if hasattr(close, 'ndim') and close.ndim > 1:
            close = close.iloc[:, 0]
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

        def v(series):
            val = series.iloc[-1]
            return None if (pd.isna(val) or np.isnan(float(val))) else float(val)

        sma_val = v(sma)
        upper_val = v(upper)
        lower_val = v(lower)
        current_price = float(close.iloc[-1])

        bw = None
        if upper_val is not None and lower_val is not None and sma_val is not None and sma_val != 0:
            bw = round((upper_val - lower_val) / sma_val * 100, 2)
        pos = None
        if upper_val is not None and lower_val is not None and upper_val != lower_val:
            pos = round((current_price - lower_val) / (upper_val - lower_val) * 100, 1)

        return {
            'sma_20': sma_val, 'upper_band': upper_val, 'lower_band': lower_val,
            'band_width': bw, 'current_price': current_price, 'position_pct': pos,
        }
    except Exception as e:
        return {'error': str(e)}

# ── History Tracking ──────────────────────────────────────────────────

HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'history.json')

def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except:
        return {'snapshots': []}

def save_snapshot(profile):
    hist = load_history()
    snap = {
        'date': profile['data_time'][:10],
        'spot': profile['spot'],
        'net_gex_b': profile['net_gex_b'],
        'net_dgex_b': profile['net_delta_gex_b'],
        'net_vex_b': profile['net_vex_b'],
        'gamma_flip': profile['gamma_flip'],
        'regime': profile['regime'],
        'call_wall': profile.get('call_wall'),
        'put_wall': profile.get('put_wall'),
        'near_call_wall': profile.get('near_call_wall'),
        'near_put_wall': profile.get('near_put_wall'),
    }
    # Keep last 30 days
    hist['snapshots'] = [s for s in hist['snapshots'] if s['date'] != snap['date']]
    hist['snapshots'].append(snap)
    hist['snapshots'] = sorted(hist['snapshots'], key=lambda x: x['date'])[-30:]
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(hist, f, indent=2)
    return hist

def get_trend(hist):
    snaps = hist.get('snapshots', [])
    if len(snaps) < 2:
        return None
    latest = snaps[-1]
    prev = snaps[-2]

    def wall_change(key):
        current = latest.get(key)
        previous = prev.get(key)
        if current is None or previous is None:
            return None
        return round(current - previous, 2)

    return {
        'gex_change': round(latest['net_gex_b'] - prev['net_gex_b'], 2),
        'spot_change': round(latest['spot'] - prev['spot'], 2),
        'regime_switch': latest['regime'] != prev['regime'],
        'call_wall_change': wall_change('call_wall'),
        'put_wall_change': wall_change('put_wall'),
        'near_call_wall_change': wall_change('near_call_wall'),
        'near_put_wall_change': wall_change('near_put_wall'),
    }

# ── HTML Report ─────────────────────────────────────────────────────

def build_html(profile):
    emoji = "🟢" if profile['regime'] == 'positive' else "🔴"
    regime_color = "#22c55e" if profile['regime'] == 'positive' else "#ef4444"
    flip_warn = "\u26a0\ufe0f" if profile['dist_to_flip'] is not None and abs(profile['dist_to_flip']) < 1.0 else ""
    flip_distance_text = (
        f"{profile['dist_to_flip']:+.2f}% {flip_warn}"
        if profile['dist_to_flip'] is not None else "N/A"
    )

    # ── Bollinger card ──
    bb = profile.get('bollinger')
    if bb and bb.get('sma_20'):
        pos = bb['position_pct']
        bw = bb['band_width']
        if pos is not None:
            if pos > 80: pos_label = "Near Upper Band ⬆️"
            elif pos < 20: pos_label = "Near Lower Band ⬇️"
            elif 40 <= pos <= 60: pos_label = "Mid-Band (neutral)"
            elif pos > 60: pos_label = "Upper half"
            else: pos_label = "Lower half"
        else: pos_label = "N/A"
        bw_label = f"{bw}%" if bw else "N/A"
        bw_note = ""
        if bw and bw < 4: bw_note = "Narrow bands \u2014 vol compression"
        elif bw and bw > 8: bw_note = "Wide bands \u2014 high vol regime"
        bollinger_card = f"""<div class="card">
    <h2>📈 Bollinger (20d, 2\u03c3)</h2>
    <div class="metric-grid">
      <div class="metric">
        <div class="label">MA20</div>
        <div class="value small">$ {bb['sma_20']:,.0f}</div>
      </div>
      <div class="metric">
        <div class="label">Upper / Lower</div>
        <div class="value small">{'$'+f"{bb['upper_band']:,.0f}"} / {'$'+f"{bb['lower_band']:,.0f}"}</div>
      </div>
      <div class="metric">
        <div class="label">Band Width</div>
        <div class="value small">{bw_label}</div>
        <div class="sub">{bw_note}</div>
      </div>
      <div class="metric">
        <div class="label">Position</div>
        <div class="value small">{pos_label} ({pos:.0f}%)</div>
      </div>
    </div>
  </div>"""
    else:
        err = bb.get('error') if bb else "no data"
        bollinger_card = f"""<div class="card">
    <h2>📈 Bollinger</h2>
    <div class="interpret"><p style="color:#8b949e">Bollinger unavailable: {err}</p></div>
  </div>"""

    # ── Vega balance and true Vanna ──
    vex = profile['net_vex_b']
    vanna = profile.get('net_vanna_b', 0.0)
    vex_emoji = ""
    vex_label = "VEGA BALANCE"
    vex_desc = "Vega × open interest balance; sensitivity inventory, not vanna or observed flow."
    vex_color = "#d29922"

    # ── Delta-GEX vs Raw GEX ──
    raw_b = profile['net_gex_b']
    dg_b = profile['net_delta_gex_b']
    gex_ratio = (dg_b / raw_b * 100) if raw_b != 0 else 0

    # ── Trend ──
    t = profile.get('trend')
    trend_html = ""
    if t:
        gex_ch = t['gex_change']
        gex_arrow = "\u2b06" if gex_ch > 0 else "\u2b07" if gex_ch < 0 else "\u27a1"
        spot_ch = t['spot_change']
        sp_arrow = "\u2b06" if spot_ch > 0 else "\u2b07" if spot_ch < 0 else "\u27a1"
        regime_warn = " \u26a0\ufe0f REGIME SWITCH" if t['regime_switch'] else ""
        trend_html = f"""<div class="trend-bar">
    <span>GEX {gex_arrow} {gex_ch:+.2f}B</span>
    <span>SPX {sp_arrow} {spot_ch:+.0f}</span>
    <span>{regime_warn}</span>
  </div>"""

    # ── Top strikes rows ──
    strike_rows = ""
    for i, (s, gex_b, coi, poi) in enumerate(profile['top_strikes']):
        color = "#22c55e" if gex_b >= 0 else "#ef4444"
        sign = "+" if gex_b >= 0 else ""
        strike_rows += f"""
        <tr>
            <td class="strike">{'🟢' if gex_b >= 0 else '🔴'} $ {s:,.0f}</td>
            <td class="gex" style="color:{color}">$ {sign}{gex_b}B</td>
            <td class="oi">{int(coi):,}</td>
            <td class="oi">{int(poi):,}</td>
        </tr>"""

    # ── Expiry breakdown ──
    exp_rows = ""
    exp_sorted = sorted(profile['by_expiry'].items(), key=lambda x: abs(x[1]['net']), reverse=True)
    for exp, data in exp_sorted[:6]:
        sign = "+" if data['net'] >= 0 else ""
        c = "#22c55e" if data['net'] >= 0 else "#ef4444"
        max_net = max(abs(exp_sorted[0][1]['net']), 1) if exp_sorted else 1
        bar_pct = max(abs(data['net']) / max_net * 100, 3)
        exp_rows += f"""
        <tr>
            <td>{exp}</td>
            <td style="color:{c}">$ {sign}{data['net']/1e9:.2f}B</td>
            <td><div class="gex-bar" style="width:{bar_pct:.0f}%;background:{c}"></div></td>
        </tr>"""

    # ── OPEX text ──
    d = profile['days_to_opex']
    if d <= 7: opex_txt = f"⏰ OPEX THIS WEEK ({profile['opex_date']}). Pinning zone."
    elif d <= 14: opex_txt = f"📌 OPEX in {d}d ({profile['opex_date']}). Gamma building."
    else: opex_txt = f"📌 OPEX in {d}d ({profile['opex_date']}). Early cycle."

    # ── Cem Interpretation ──
    if profile['regime'] == 'positive':
        regime_txt = "Dealers net long gamma. Hedging \u2192 counter-cyclical: buy dips, sell rallies. Vol compressed. Strategy: mean-reversion, fade extremes."
    else:
        regime_txt = "Dealers net short gamma. Hedging \u2192 pro-cyclical: sell weakness, buy strength. Momentum-driven. Strategy: trend-follow, tighten risk."

    vanna_regime_txt = ""
    if vanna > 0.3:
        vanna_regime_txt = "Modeled Vanna positive: under the stated proxy, falling IV implies estimated dealer buying. This is not observed flow."
    elif vanna < -0.3:
        vanna_regime_txt = "Modeled Vanna negative: under the stated proxy, falling IV implies estimated dealer selling. This is not observed flow."

    flip_note = ""
    if profile['dist_to_flip'] is not None and abs(profile['dist_to_flip']) < 1.0:
        flip_note = "SPX within 1% of Gamma Flip \u2014 small spot moves can switch the dealer regime instantaneously."

    # ── Assemble HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Dealer Brief \u2014 {profile['data_time'][:10]}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif; background: #0d1117; color: #e6edf3; padding: 12px; -webkit-font-smoothing: antialiased; }}
  .container {{ max-width: 600px; margin: 0 auto; }}
  h1 {{ font-size: 20px; font-weight: 700; color: #f0f6fc; margin-bottom: 2px; }}
  .meta {{ font-size: 11px; color: #8b949e; }}
  .badge {{ display: inline-block; padding: 3px 12px; border-radius: 14px; font-size: 12px; font-weight: 600; background: {regime_color}22; color: {regime_color}; border: 1px solid {regime_color}55; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 14px; margin-bottom: 10px; }}
  .card h2 {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 10px; font-weight: 600; }}
  .g2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .g3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
  .m .l {{ font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.3px; }}
  .m .v {{ font-size: 18px; font-weight: 700; color: #f0f6fc; }}
  .m .v.sm {{ font-size: 15px; }}
  .m .sub {{ font-size: 11px; color: #8b949e; margin-top: 1px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ text-align: left; color: #8b949e; font-weight: 500; padding: 5px 6px; border-bottom: 1px solid #30363d; font-size: 10px; text-transform: uppercase; }}
  td {{ padding: 5px 6px; border-bottom: 1px solid #21262d; }}
  .strike {{ font-weight: 600; }}
  .gex {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
  .oi {{ font-size: 11px; color: #8b949e; }}
  .gex-bar {{ height: 6px; border-radius: 3px; min-width: 3px; }}
  .tip {{ font-size: 13px; line-height: 1.5; color: #c9d1d9; }}
  .tip strong {{ color: #f0f6fc; }}
  .warn {{ background: #d2992211; border: 1px solid #d2992255; border-radius: 8px; padding: 10px 12px; margin: 10px 0; font-size: 12px; color: #d29922; }}
  .trend-bar {{ display: flex; gap: 12px; font-size: 11px; color: #8b949e; padding: 6px 0; border-top: 1px solid #21262d; margin-top: 8px; }}
  .footer {{ font-size: 10px; color: #484f58; text-align: center; margin-top: 16px; padding-top: 10px; border-top: 1px solid #21262d; }}
  .divider {{ border: none; border-top: 1px solid #21262d; margin: 10px 0; }}
  .mt {{ margin-top: 10px; }}
</style>
</head>
<body>
<div class="container">

  <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:12px;">
    <div>
      <h1>🟢🔴 Dealer Brief</h1>
      <div class="meta">{profile['data_time']} \u00b7 CBOE 20-min delayed</div>
    </div>
    <span class="badge">{emoji} {profile['regime'].upper()}</span>
  </div>

  {trend_html}

  <!-- Gamma Snapshot -->
  <div class="card">
    <h2>📊 Gamma (Net $ {profile['net_gex_b']:+.2f}B)</h2>
    <div class="g2">
      <div class="m">
        <div class="l">SPX</div>
        <div class="v">{profile['spot']:,.0f}</div>
      </div>
      <div class="m">
        <div class="l">Gamma Flip</div>
        <div class="v sm">{'$ {:,.0f}'.format(profile['gamma_flip']) if profile['gamma_flip'] else 'N/A'}</div>
        <div class="sub">{flip_distance_text}</div>
      </div>
      <div class="m">
        <div class="l">Call Wall</div>
        <div class="v sm">$ {profile['call_wall']:,.0f} (Near: $ {profile['near_call_wall']:,.0f})</div>
        <div class="sub">Resistance</div>
      </div>
      <div class="m">
        <div class="l">Put Wall</div>
        <div class="v sm">$ {profile['put_wall']:,.0f} (Near: $ {profile['near_put_wall']:,.0f})</div>
        <div class="sub">Support</div>
      </div>
      <div class="m">
        <div class="l">Next OPEX</div>
        <div class="v sm">{profile['opex_date']}</div>
        <div class="sub">{opex_txt}</div>
      </div>
      <div class="m">
        <div class="l">Nearest Exp</div>
        <div class="v sm">{profile['nearest_exp']}</div>
      </div>
    </div>
    {f'<div class="warn">{flip_warn} SPX within 1% of Gamma Flip \u2014 small moves flip regime</div>' if profile['dist_to_flip'] is not None and abs(profile['dist_to_flip']) < 1.0 else ''}
  </div>

  <!-- Vega balance, true Vanna + Delta GEX -->
  <div class="card">
    <h2>🔍 2nd-Order Greeks</h2>
    <div style="display:flex; gap:10px; align-items:center; margin-bottom:8px;">
      <span class="badge" style="background:{vex_color}22; color:{vex_color}; border-color:{vex_color}55;">{vex_emoji} {vex_label} ($ {vex:+.2f}B)</span>
    </div>
    <div class="g2">
      <div class="m">
        <div class="l">Delta-GEX (Gary model)</div>
        <div class="v sm">$ {dg_b:+.2f}B</div>
        <div class="sub">vs Raw $ {raw_b:+.2f}B \u00b7 {gex_ratio:.0f}% effective</div>
      </div>
      <div class="m">
        <div class="l">Vega Balance / True Vanna</div>
        <div class="v sm">$ {vex:+.2f}B</div>
        <div class="sub">Vanna $ {vanna:+.2f}B per 1 vol pt · {vex_desc}</div>
      </div>
    </div>
  </div>

  <!-- Top GEX Strikes -->
  <div class="card">
    <h2>🔝 Top GEX Strikes</h2>
    <table>
      <tr><th>Strike</th><th>Net GEX</th><th>C OI</th><th>P OI</th></tr>
      {strike_rows}
    </table>
  </div>

  <!-- GEX by Expiry -->
  <div class="card">
    <h2>📅 GEX by Expiry</h2>
    <table>
      <tr><th>Expiry</th><th>Net</th><th></th></tr>
      {exp_rows}
    </table>
  </div>

  <!-- Bollinger -->
  {bollinger_card}

  <!-- Cem Interpretation -->
  <div class="card">
    <h2>🎯 Cem Karsan Framework</h2>
    <div class="tip">
      <p><strong>{profile['regime'].upper()} GAMMA.</strong> {regime_txt}</p>
      {f'<p class="mt"><strong>🔍 VANNA PROXY.</strong> {vanna_regime_txt}</p>' if vanna_regime_txt else ''}
      {f'<p class="mt"><strong>\u26a0\ufe0f FLIP RISK.</strong> {flip_note}</p>' if flip_note else ''}
      <hr class="divider">
      <p style="font-size:11px;color:#8b949e"><strong>📄 Delta-GEX:</strong> gamma × OI × |delta|, an adjusted exposure proxy. <strong>Vega balance:</strong> vega × OI; it is not Vanna. <strong>True Vanna:</strong> Black-Scholes ∂Δ/∂σ × OI, shown per 1 vol point under the stated call+/put− assumption.</p>
    </div>
  </div>

  <div class="footer">
    CBOE free delayed quotes \u00b7 Data refreshed daily \u00b7 Not financial advice<br>
    GEX = Γ×OI×100×S · dGEX = Γ×OI×100×S×|Δ| · VEX = ν×OI×100
  </div>
</div>
</body>
</html>"""
    return html
# ── Main ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true', help='Output JSON')
    parser.add_argument('--html', action='store_true', help='Output HTML to stdout')
    parser.add_argument('--save', type=str, help='Save HTML to path')
    args = parser.parse_args()

    print("📡 Fetching CBOE SPX option chain...", file=sys.stderr)
    raw = fetch_cboe_data()
    spot = raw['data']['current_price']
    print(f"✅ SPX ${spot:,.2f} · {len(raw['data']['options'])} options loaded", file=sys.stderr)

    profile = compute_profile(raw)

    # Bollinger Bands
    bollinger = fetch_bollinger()
    if bollinger and 'error' not in bollinger:
        profile['bollinger'] = bollinger
        bollinger_status = "✅"
    elif bollinger and 'error' in bollinger:
        profile['bollinger'] = None
        bollinger_status = f"⚠️ {bollinger['error']}"
    else:
        profile['bollinger'] = None
        bollinger_status = "⚠️ no data"
    print(f"📊 Bollinger Bands: {bollinger_status}", file=sys.stderr)

    # History
    hist = save_snapshot(profile)
    trend = get_trend(hist)
    profile['trend'] = trend

    if args.json:
        class DateEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, date):
                    return obj.isoformat()
                return super().default(obj)
        print(json.dumps(profile, indent=2, cls=DateEncoder))

    elif args.save:
        html = build_html(profile)
        os.makedirs(os.path.dirname(args.save) or '.', exist_ok=True)
        with open(args.save, 'w') as f:
            f.write(html)
        print(f"✅ Report saved: {args.save}", file=sys.stderr)

    elif args.html:
        print(build_html(profile))

    else:
        # Brief print
        emoji = "🟢" if profile['regime'] == 'positive' else "🔴"
        flip = "${:,.0f}".format(profile['gamma_flip']) if profile['gamma_flip'] else "N/A"
        print(f"\n{'═'*50}")
        print(f"  DEALER POSITIONING BRIEF · {profile['data_time']}")
        print(f"{'═'*50}")
        print(f"  SPX:        {profile['spot']:,.0f}")
        print(f"  Regime:     {emoji} {profile['regime'].upper()} (Net GEX ${profile['net_gex_b']:+.2f}B)")
        print(f"  Gamma Flip: {flip} (dist: {profile['dist_to_flip']:+.2f}%)")
        print(f"  Call Wall:  ${profile['call_wall']:,.0f}  Put Wall: ${profile['put_wall']:,.0f}")
        print(f"  OPEX:       {profile['opex_date']} (T+{profile['days_to_opex']}d)")
        print(f"{'═'*50}")
        for s, g, co, po in profile['top_strikes'][:5]:
            ic = "🟢" if g >= 0 else "🔴"
            print(f"    {ic} ${s:,.0f}  ${g:+.2f}B  C:{co:,.0f} P:{po:,.0f}")
        print(f"{'═'*50}")

if __name__ == '__main__':
    main()
