"""Dealer gamma and option-positioning analytics computed from a delayed option chain.

All dollar exposures use the common public convention that dealers are long the calls
and short the puts open in the market. That assumption cannot be verified from public
data, which is why these numbers are estimates and are labelled that way in the app.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.data.market_calendar import monthly_opex

CONTRACT_MULTIPLIER = 100.0
ET = ZoneInfo("America/New_York")
DEFAULT_CARRY = 0.03
MIN_IV, MAX_IV = 0.01, 4.0
MIN_YEARS = 1.0 / (365.0 * 24.0)


def _norm_pdf(value: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * value * value) / np.sqrt(2.0 * np.pi)


def _norm_cdf(value: np.ndarray) -> np.ndarray:
    # Abramowitz & Stegun 26.2.17: accurate to about 7 digits, with no extra dependency.
    x = np.abs(value)
    t = 1.0 / (1.0 + 0.2316419 * x)
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    tail = _norm_pdf(x) * poly
    return np.where(value >= 0.0, 1.0 - tail, tail)


def _d1_d2(spot, strike, years, iv, carry):
    root = iv * np.sqrt(years)
    d1 = (np.log(spot / strike) + (carry + 0.5 * iv * iv) * years) / root
    return d1, d1 - root


def bs_gamma(spot, strike, years, iv, carry: float = DEFAULT_CARRY):
    """Change in option delta per $1 move in the underlying."""
    d1, _ = _d1_d2(spot, strike, years, iv, carry)
    return _norm_pdf(d1) / (spot * iv * np.sqrt(years))


def bs_delta(spot, strike, years, iv, is_call, carry: float = DEFAULT_CARRY):
    d1, _ = _d1_d2(spot, strike, years, iv, carry)
    call_delta = _norm_cdf(d1)
    return np.where(is_call, call_delta, call_delta - 1.0)


def bs_vanna(spot, strike, years, iv, carry: float = DEFAULT_CARRY):
    """Change in delta per 1.00 (100 vol points) rise in implied volatility."""
    d1, d2 = _d1_d2(spot, strike, years, iv, carry)
    return -_norm_pdf(d1) * d2 / iv


def bs_charm(spot, strike, years, iv, carry: float = DEFAULT_CARRY):
    """Change in delta per year as time passes (same value for calls and puts here)."""
    d1, d2 = _d1_d2(spot, strike, years, iv, carry)
    root = iv * np.sqrt(years)
    return -_norm_pdf(d1) * (2.0 * carry * years - d2 * root) / (2.0 * years * root)


def _years_to_expiry(expiries: pd.Series, now: dt.datetime) -> np.ndarray:
    # Expiry is taken as the 4pm ET close; a small floor keeps same-day math stable.
    naive_now = now.astimezone(ET).replace(tzinfo=None)
    close = pd.to_datetime(expiries) + pd.Timedelta(hours=16)
    seconds = (close - pd.Timestamp(naive_now)).dt.total_seconds()
    return np.maximum(seconds.values / (365.0 * 86400.0), MIN_YEARS)


def prepare_chain(frame: pd.DataFrame, spot: float, now: dt.datetime | None = None) -> pd.DataFrame:
    """Drop expired contracts and add time to expiry, greeks and dollar exposures."""
    now = now or dt.datetime.now(ET)
    naive_now = now.astimezone(ET).replace(tzinfo=None)
    cutoff = pd.Timestamp(naive_now.date()) if naive_now.time() < dt.time(16, 15) else pd.Timestamp(naive_now.date()) + pd.Timedelta(days=1)
    chain = frame[pd.to_datetime(frame["expiry"]) >= cutoff].copy()
    if chain.empty:
        return chain
    chain["expiry"] = pd.to_datetime(chain["expiry"])
    chain["years"] = _years_to_expiry(chain["expiry"], now)
    chain["dte"] = (chain["expiry"] - pd.Timestamp(naive_now.date())).dt.days
    chain["is_call"] = chain["type"] == "C"
    chain["sign"] = np.where(chain["is_call"], 1.0, -1.0)

    usable = (chain["iv"] >= MIN_IV) & (chain["iv"] <= MAX_IV)
    chain["iv_ok"] = usable
    safe_iv = chain["iv"].where(usable, 0.3).values
    strike, years = chain["strike"].values, chain["years"].values

    gamma = bs_gamma(spot, strike, years, safe_iv)
    chain["gamma_model"] = np.where(usable, gamma, chain["gamma"].values)
    notional = chain["open_interest"].values * CONTRACT_MULTIPLIER
    # Dollar gamma: how much delta (in $) dealers must trade for a 1% move in the underlying.
    chain["gex"] = chain["sign"].values * chain["gamma_model"].values * notional * spot * spot * 0.01

    vanna = np.where(usable, bs_vanna(spot, strike, years, safe_iv), 0.0)
    chain["vanna_exposure"] = chain["sign"].values * vanna * 0.01 * notional * spot
    charm = np.where(usable, bs_charm(spot, strike, years, safe_iv), 0.0)
    chain["charm_exposure"] = chain["sign"].values * charm / 365.0 * notional * spot

    mid = (chain["bid"] + chain["ask"]) / 2.0
    chain["mid"] = mid.where((chain["bid"] > 0) & (chain["ask"] > 0), chain["last"])
    chain["premium"] = chain["volume"] * chain["mid"] * CONTRACT_MULTIPLIER
    return chain.reset_index(drop=True)


def select_scope(chain: pd.DataFrame, scope: str) -> pd.DataFrame:
    """Limit contracts to an expiry scope: all, 0dte, 7d, 30d, 90d, monthly or a date."""
    if chain.empty or scope in (None, "", "all"):
        return chain
    if scope == "0dte":
        nearest = chain["dte"].min()
        return chain[chain["dte"] == nearest] if nearest <= 0 else chain.iloc[0:0]
    if scope in ("7d", "30d", "90d"):
        return chain[chain["dte"] <= int(scope[:-1])]
    if scope == "monthly":
        monthly = chain[chain["expiry"].map(_is_monthly_expiry)]
        if monthly.empty:
            return monthly
        return monthly[monthly["expiry"] == monthly["expiry"].min()]
    day = pd.Timestamp(scope)
    return chain[chain["expiry"] == day]


def _is_monthly_expiry(day: pd.Timestamp) -> bool:
    day = pd.Timestamp(day).date()
    return day == monthly_opex(day.year, day.month)


def gamma_profile(chain: pd.DataFrame, spot: float, width: float = 0.2, steps: int = 81) -> dict:
    """Net dealer gamma if the underlying were at other prices, today's positions held."""
    if chain.empty or not spot:
        return {"spot": [], "net_gex": []}
    book = chain[chain["iv_ok"] & (chain["open_interest"] > 0)]
    grid = np.linspace(spot * (1.0 - width), spot * (1.0 + width), steps)
    if book.empty:
        return {"spot": grid.tolist(), "net_gex": [0.0] * steps}
    strike, years, iv = book["strike"].values, book["years"].values, book["iv"].values
    weight = book["sign"].values * book["open_interest"].values * CONTRACT_MULTIPLIER
    totals = np.empty(steps)
    for index, level in enumerate(grid):
        totals[index] = float(np.sum(weight * bs_gamma(level, strike, years, iv) * level * level * 0.01))
    return {"spot": grid.tolist(), "net_gex": totals.tolist()}


def find_gamma_flip(profile: dict, spot: float) -> float | None:
    """The price nearest to spot where estimated dealer gamma changes sign."""
    levels = np.asarray(profile.get("spot", []), dtype=float)
    totals = np.asarray(profile.get("net_gex", []), dtype=float)
    if levels.size < 2:
        return None
    crossings = np.where(np.sign(totals[:-1]) * np.sign(totals[1:]) < 0)[0]
    if crossings.size == 0:
        return None
    points = levels[crossings] + (0.0 - totals[crossings]) * (levels[crossings + 1] - levels[crossings]) / (totals[crossings + 1] - totals[crossings])
    return float(points[np.argmin(np.abs(points - spot))])


def max_pain(chain: pd.DataFrame) -> float | None:
    """Strike at which option holders collectively lose the most at expiry."""
    if chain.empty:
        return None
    book = chain[chain["open_interest"] > 0]
    if book.empty:
        return None
    levels = np.sort(book["strike"].unique())
    calls, puts = book[book["is_call"]], book[~book["is_call"]]
    call_payoff = np.maximum(levels[:, None] - calls["strike"].values[None, :], 0.0) @ calls["open_interest"].values
    put_payoff = np.maximum(puts["strike"].values[None, :] - levels[:, None], 0.0) @ puts["open_interest"].values
    return float(levels[int(np.argmin(call_payoff + put_payoff))])


def _atm_row(expiry_rows: pd.DataFrame, spot: float) -> dict | None:
    """IV and straddle price at the strike closest to spot for one expiry."""
    valid = expiry_rows[expiry_rows["iv_ok"]]
    if valid.empty:
        return None
    strikes = valid["strike"].unique()
    strike = float(strikes[np.argmin(np.abs(strikes - spot))])
    at_strike = valid[valid["strike"] == strike]
    call, put = at_strike[at_strike["is_call"]], at_strike[~at_strike["is_call"]]
    if call.empty or put.empty:
        return None
    iv = float((call["iv"].iloc[0] + put["iv"].iloc[0]) / 2.0)
    call_mid, put_mid = float(call["mid"].iloc[0]), float(put["mid"].iloc[0])
    straddle = call_mid + put_mid if call_mid > 0 and put_mid > 0 else None
    return {"strike": strike, "iv": iv, "straddle": straddle}


def expiry_table(chain: pd.DataFrame, spot: float) -> list[dict]:
    rows = []
    for expiry, group in chain.groupby("expiry", sort=True):
        calls, puts = group[group["is_call"]], group[~group["is_call"]]
        atm = _atm_row(group, spot)
        straddle = atm["straddle"] if atm else None
        rows.append({
            "expiry": expiry.strftime("%Y-%m-%d"),
            "dte": int(group["dte"].iloc[0]),
            "monthly": bool(_is_monthly_expiry(expiry)),
            "net_gex": float(group["gex"].sum()),
            "call_gex": float(calls["gex"].sum()),
            "put_gex": float(puts["gex"].sum()),
            "call_oi": float(calls["open_interest"].sum()),
            "put_oi": float(puts["open_interest"].sum()),
            "call_volume": float(calls["volume"].sum()),
            "put_volume": float(puts["volume"].sum()),
            "atm_iv": atm["iv"] * 100.0 if atm else None,
            "atm_strike": atm["strike"] if atm else None,
            "straddle": straddle,
            "expected_move_pct": straddle / spot * 100.0 if straddle else None,
            "max_pain": max_pain(group),
        })
    return rows


def constant_maturity_iv(expiries: list[dict], target_days: int = 30) -> float | None:
    """ATM IV at a fixed horizon, interpolating linearly in total variance."""
    points = sorted(
        (row["dte"], row["atm_iv"]) for row in expiries
        if row["atm_iv"] and row["dte"] > 0
    )
    if not points:
        return None
    below = [point for point in points if point[0] <= target_days]
    above = [point for point in points if point[0] >= target_days]
    if not below:
        return above[0][1]
    if not above:
        return below[-1][1]
    (d1, v1), (d2, v2) = below[-1], above[0]
    if d1 == d2:
        return v1
    w1, w2 = (v1 / 100.0) ** 2 * d1, (v2 / 100.0) ** 2 * d2
    total = w1 + (w2 - w1) * (target_days - d1) / (d2 - d1)
    return float(np.sqrt(max(total, 0.0) / target_days) * 100.0)


def skew_25_delta(chain: pd.DataFrame, target_days: int = 30) -> dict | None:
    """25-delta put IV minus 25-delta call IV at the expiry closest to the target."""
    usable = chain[chain["iv_ok"] & (chain["dte"] > 2)]
    if usable.empty:
        return None
    dtes = usable["dte"].unique()
    dte = int(dtes[np.argmin(np.abs(dtes - target_days))])
    rows = usable[usable["dte"] == dte]

    def iv_at(frame: pd.DataFrame, target: float) -> float | None:
        ordered = frame[(frame["delta"] != 0.0)].sort_values("delta")
        if len(ordered) < 2 or not (ordered["delta"].min() <= target <= ordered["delta"].max()):
            return None
        return float(np.interp(target, ordered["delta"].values, ordered["iv"].values))

    put_iv = iv_at(rows[~rows["is_call"]], -0.25)
    call_iv = iv_at(rows[rows["is_call"]], 0.25)
    if put_iv is None or call_iv is None:
        return None
    return {
        "dte": dte,
        "put_iv": put_iv * 100.0,
        "call_iv": call_iv * 100.0,
        "skew": (put_iv - call_iv) * 100.0,
        "ratio": put_iv / call_iv if call_iv else None,
    }


def strike_table(chain: pd.DataFrame) -> pd.DataFrame:
    calls = chain[chain["is_call"]].groupby("strike").agg(
        call_gex=("gex", "sum"), call_oi=("open_interest", "sum"), call_volume=("volume", "sum"),
    )
    puts = chain[~chain["is_call"]].groupby("strike").agg(
        put_gex=("gex", "sum"), put_oi=("open_interest", "sum"), put_volume=("volume", "sum"),
    )
    exposure = chain.groupby("strike").agg(vanna=("vanna_exposure", "sum"), charm=("charm_exposure", "sum"))
    table = pd.concat([calls, puts, exposure], axis=1).fillna(0.0).sort_index()
    table["net_gex"] = table["call_gex"] + table["put_gex"]
    return table


def unusual_activity(chain: pd.DataFrame, spot: float, limit: int = 30) -> list[dict]:
    """Contracts trading far more today than their existing open interest."""
    if chain.empty:
        return []
    active = chain[(chain["volume"] >= 50) & (chain["mid"] > 0)].copy()
    active["vol_oi"] = active["volume"] / active["open_interest"].clip(lower=1.0)
    flagged = active[active["vol_oi"] >= 1.5].sort_values("premium", ascending=False).head(limit)
    rows = []
    for row in flagged.itertuples():
        rows.append({
            "contract": row.contract,
            "expiry": row.expiry.strftime("%Y-%m-%d"),
            "dte": int(row.dte),
            "type": "Call" if row.is_call else "Put",
            "strike": float(row.strike),
            "moneyness_pct": (float(row.strike) / spot - 1.0) * 100.0,
            "volume": float(row.volume),
            "open_interest": float(row.open_interest),
            "vol_oi": float(row.vol_oi),
            "mid": float(row.mid),
            "premium": float(row.premium),
            "iv": float(row.iv) * 100.0 if row.iv_ok else None,
            "delta": float(row.delta),
        })
    return rows


def smile(chain: pd.DataFrame, spot: float, target_days: int = 30, window: float = 0.25) -> dict | None:
    """Implied volatility across strikes for the expiry closest to the target."""
    usable = chain[chain["iv_ok"] & (chain["dte"] > 0)]
    if usable.empty:
        return None
    dtes = usable["dte"].unique()
    dte = int(dtes[np.argmin(np.abs(dtes - target_days))])
    rows = usable[(usable["dte"] == dte) & (usable["strike"].between(spot * (1 - window), spot * (1 + window)))]
    # Each side of the smile is priced by its out-of-the-money options, the liquid ones.
    otm = rows[(rows["is_call"] & (rows["strike"] >= spot)) | (~rows["is_call"] & (rows["strike"] <= spot))].sort_values("strike")
    if otm.empty:
        return None
    return {
        "dte": dte,
        "expiry": otm["expiry"].iloc[0].strftime("%Y-%m-%d"),
        "strikes": otm["strike"].tolist(),
        "iv": (otm["iv"] * 100.0).round(2).tolist(),
    }


def _session_activity(frame: pd.DataFrame, session_day: pd.Timestamp) -> dict:
    """Volume and premium of the latest session, split by side and by same-day expiry."""
    expiries = pd.to_datetime(frame["expiry"])
    traded = frame[expiries >= session_day]
    is_call = traded["type"] == "C"
    mid = ((traded["bid"] + traded["ask"]) / 2.0).where((traded["bid"] > 0) & (traded["ask"] > 0), traded["last"])
    premium = traded["volume"] * mid * CONTRACT_MULTIPLIER
    return {
        "call_volume": float(traded.loc[is_call, "volume"].sum()),
        "put_volume": float(traded.loc[~is_call, "volume"].sum()),
        "call_premium": float(premium[is_call].sum()),
        "put_premium": float(premium[~is_call].sum()),
        "total_volume": float(traded["volume"].sum()),
        "same_day_volume": float(traded.loc[expiries[expiries >= session_day] == session_day, "volume"].sum()),
    }


def analyze_chain(
    frame: pd.DataFrame,
    spot: float,
    *,
    scope: str = "all",
    window_pct: float = 15.0,
    now: dt.datetime | None = None,
    session: str | None = None,
) -> dict:
    """Everything the Options & Gamma page shows, for one underlying and expiry scope."""
    prepared = prepare_chain(frame, spot, now=now)
    if prepared.empty:
        return {"available": False, "reason": "All listed contracts have expired."}
    expiries_all = expiry_table(prepared, spot)
    chain = select_scope(prepared, scope)
    if chain.empty:
        return {
            "available": True,
            "empty_scope": True,
            "reason": "No contracts in this expiry scope.",
            "spot": spot,
            "expiries": expiries_all,
        }

    profile = gamma_profile(chain, spot)
    flip = find_gamma_flip(profile, spot)
    strikes = strike_table(chain)
    calls, puts = chain[chain["is_call"]], chain[~chain["is_call"]]
    net_gex = float(chain["gex"].sum())

    # Walls are read as resistance above and support below, so each looks on its own side.
    above = strikes[(strikes.index >= spot) & (strikes.index <= spot * 1.2)]
    below = strikes[(strikes.index <= spot) & (strikes.index >= spot * 0.8)]
    call_wall = float(above["call_gex"].idxmax()) if not above.empty and above["call_gex"].max() > 0 else None
    put_wall = float(below["put_gex"].idxmin()) if not below.empty and below["put_gex"].min() < 0 else None

    total_call_volume, total_put_volume = float(calls["volume"].sum()), float(puts["volume"].sum())
    total_call_oi, total_put_oi = float(calls["open_interest"].sum()), float(puts["open_interest"].sum())
    call_premium, put_premium = float(calls["premium"].sum()), float(puts["premium"].sum())
    min_dte = int(prepared["dte"].min())
    zero_dte = prepared[prepared["dte"] <= 0]
    all_abs_gamma = float(prepared["gex"].abs().sum())

    # Volume is what traded in the latest session, which includes contracts that expired
    # that same day. They are gone from `prepared` once that day is over, so session
    # totals are read from the raw chain.
    session_day = pd.Timestamp(session) if session else pd.Timestamp((now or dt.datetime.now(ET)).astimezone(ET).date())
    activity = _session_activity(frame, session_day)
    if scope in (None, "", "all"):
        total_call_volume, total_put_volume = activity["call_volume"], activity["put_volume"]
        call_premium, put_premium = activity["call_premium"], activity["put_premium"]
    total_volume = total_call_volume + total_put_volume

    next_expiry = next((row for row in expiries_all if row["dte"] >= 0), None)
    next_week = next((row for row in expiries_all if row["dte"] >= 5), None)
    next_monthly = next((row for row in expiries_all if row["monthly"] and row["dte"] >= 0), None)
    iv30 = constant_maturity_iv(expiries_all, 30)
    iv7 = constant_maturity_iv(expiries_all, 7)
    iv90 = constant_maturity_iv(expiries_all, 90)
    skew = skew_25_delta(prepared)

    kpis = {
        "spot": spot,
        "net_gex": net_gex,
        "call_gex": float(calls["gex"].sum()),
        "put_gex": float(puts["gex"].sum()),
        "gamma_regime": "positive" if net_gex > 0 else "negative",
        "gamma_flip": flip,
        "flip_distance_pct": (spot / flip - 1.0) * 100.0 if flip else None,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_distance_pct": (call_wall / spot - 1.0) * 100.0 if call_wall else None,
        "put_wall_distance_pct": (put_wall / spot - 1.0) * 100.0 if put_wall else None,
        "max_pain": next_expiry["max_pain"] if next_expiry else None,
        "max_pain_expiry": next_expiry["expiry"] if next_expiry else None,
        "max_pain_monthly": next_monthly["max_pain"] if next_monthly else None,
        "max_pain_monthly_expiry": next_monthly["expiry"] if next_monthly else None,
        "pc_volume": total_put_volume / total_call_volume if total_call_volume else None,
        "pc_oi": total_put_oi / total_call_oi if total_call_oi else None,
        "pc_premium": put_premium / call_premium if call_premium else None,
        "call_volume": total_call_volume,
        "put_volume": total_put_volume,
        "call_oi": total_call_oi,
        "put_oi": total_put_oi,
        "call_premium": call_premium,
        "put_premium": put_premium,
        "vanna_total": float(chain["vanna_exposure"].sum()),
        "charm_total": float(chain["charm_exposure"].sum()),
        "iv30": iv30,
        "iv7": iv7,
        "iv90": iv90,
        "term_slope": iv30 - iv90 if iv30 is not None and iv90 is not None else None,
        "skew_25d": skew["skew"] if skew else None,
        "skew_dte": skew["dte"] if skew else None,
        "skew_put_iv": skew["put_iv"] if skew else None,
        "skew_call_iv": skew["call_iv"] if skew else None,
        "expected_move_next": next_expiry["expected_move_pct"] if next_expiry else None,
        "expected_move_next_expiry": next_expiry["expiry"] if next_expiry else None,
        "expected_move_week": next_week["expected_move_pct"] if next_week else None,
        "expected_move_week_expiry": next_week["expiry"] if next_week else None,
        "expected_move_month": next_monthly["expected_move_pct"] if next_monthly else None,
        "expected_move_month_expiry": next_monthly["expiry"] if next_monthly else None,
        "zero_dte_volume_share": activity["same_day_volume"] / activity["total_volume"] * 100.0 if activity["total_volume"] else None,
        "zero_dte_gamma_share": float(zero_dte["gex"].abs().sum()) / all_abs_gamma * 100.0 if min_dte <= 0 and all_abs_gamma else 0.0,
        "total_volume": total_volume,
    }

    lower, upper = spot * (1.0 - window_pct / 100.0), spot * (1.0 + window_pct / 100.0)
    shown = strikes[(strikes.index >= lower) & (strikes.index <= upper)]
    return {
        "available": True,
        "spot": spot,
        "scope": scope,
        "kpis": kpis,
        "strikes": {
            "strike": shown.index.tolist(),
            **{column: shown[column].round(2).tolist() for column in shown.columns},
        },
        "profile": profile,
        "expiries": expiries_all,
        "smile": smile(prepared, spot),
        "unusual": unusual_activity(chain, spot),
    }


def snapshot_values(result: dict) -> dict[str, float]:
    """The numbers worth keeping as a daily history for one underlying."""
    kpis = result.get("kpis") or {}
    keys = {
        "net_gex": kpis.get("net_gex"),
        "gamma_flip": kpis.get("gamma_flip"),
        "flip_distance_pct": kpis.get("flip_distance_pct"),
        "call_wall": kpis.get("call_wall"),
        "put_wall": kpis.get("put_wall"),
        "pc_volume": kpis.get("pc_volume"),
        "pc_oi": kpis.get("pc_oi"),
        "iv30": kpis.get("iv30"),
        "skew_25d": kpis.get("skew_25d"),
        "zero_dte_volume_share": kpis.get("zero_dte_volume_share"),
        "spot": kpis.get("spot"),
    }
    return {key: float(value) for key, value in keys.items() if value is not None}
