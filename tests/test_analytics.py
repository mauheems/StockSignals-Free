"""Checks of the signal maths against hand-calculated results and known identities."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.analytics import options as o
from src.analytics import rotation as r
from src.analytics import technicals as ta
from src.analytics.breadth import compute_breadth_series, last_breadth_thrust
from src.analytics.studies import forward_return_study, select_events, spans_from_mask


def ohlcv(close, volume=1000.0, spread=1.0):
    close = pd.Series(close, dtype=float, index=pd.date_range("2024-01-01", periods=len(close), freq="B"))
    return pd.DataFrame({
        "Open": close, "High": close + spread, "Low": close - spread, "Close": close,
        "AdjClose": close, "Volume": float(volume),
    })


# ------------------------------------------------------------------------ technicals

def test_sma_needs_a_full_window():
    values = ta.sma(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert values.isna().tolist() == [True, True, False, False]
    assert values.iloc[-1] == pytest.approx(3.0)


def test_rsi_extremes_and_midpoint():
    assert ta.rsi(pd.Series(np.arange(1.0, 40.0))).iloc[-1] == pytest.approx(100.0)
    assert ta.rsi(pd.Series(np.arange(40.0, 1.0, -1.0))).iloc[-1] == pytest.approx(0.0)
    assert ta.rsi(pd.Series([10.0] * 30)).iloc[-1] == pytest.approx(50.0)


def test_rsi_matches_wilder_calculation_by_hand():
    close = pd.Series([44.0, 45.0, 44.0, 46.0])
    # Gains 1, 0, 2 and losses 0, 1, 0, smoothed with alpha = 1/3 from the first change.
    gain = 1.0
    gain = gain * (2 / 3) + 0 * (1 / 3)
    gain = gain * (2 / 3) + 2 * (1 / 3)
    loss = 0.0
    loss = loss * (2 / 3) + 1 * (1 / 3)
    loss = loss * (2 / 3) + 0 * (1 / 3)
    expected = 100.0 - 100.0 / (1.0 + gain / loss)
    assert ta.rsi(close, window=3).iloc[-1] == pytest.approx(expected)


def test_relative_volume_excludes_today_from_its_own_average():
    volume = pd.Series([100.0] * 20 + [300.0])
    assert ta.relative_volume(volume, 20).iloc[-1] == pytest.approx(3.0)


def test_historical_volatility_of_constant_growth_is_zero():
    close = pd.Series(100.0 * 1.01 ** np.arange(40))
    assert ta.historical_volatility(close, 20).iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_obv_adds_on_up_days_and_subtracts_on_down_days():
    frame = ohlcv([10, 11, 10, 10, 12], volume=100)
    assert ta.obv(frame).tolist() == [0.0, 100.0, 0.0, 0.0, 100.0]


def test_cross_events_name_golden_and_death_crosses():
    fast = pd.Series([1.0, 3.0, 3.0, 1.0], index=pd.date_range("2024-01-01", periods=4))
    slow = pd.Series([2.0, 2.0, 2.0, 2.0], index=fast.index)
    events = ta.cross_events(fast, slow)
    assert [event["type"] for event in events] == ["golden", "death"]
    assert events[0]["date"] == "2024-01-02"


@pytest.mark.parametrize("close,sma50,sma200,expected", [
    (110, 105, 100, "uptrend"),
    (110, 95, 100, "recovery"),
    (102, 105, 100, "pullback"),
    (97, 95, 100, "bear_rally"),
    (95, 105, 100, "breakdown"),
    (90, 95, 100, "downtrend"),
])
def test_trend_state_names_every_arrangement(close, sma50, sma200, expected):
    assert ta.trend_state(close, sma50, sma200)["id"] == expected


def test_trend_state_without_history():
    assert ta.trend_state(100.0, None, None)["id"] == "unknown"


def test_anchored_vwap_only_counts_from_the_anchor():
    frame = ohlcv([100, 100, 200], volume=10, spread=0.0)
    vwap = ta.anchored_vwap(frame, frame.index[2])
    assert vwap.iloc[2] == pytest.approx(200.0)
    assert vwap.iloc[:2].isna().all()


# --------------------------------------------------------------------------- options

def finite_difference(function, x, step):
    return (function(x + step) - function(x - step)) / (2.0 * step)


def test_gamma_is_the_derivative_of_delta():
    spot, strike, years, iv = 100.0, 105.0, 0.25, 0.3
    numeric = finite_difference(lambda s: o.bs_delta(s, strike, years, iv, True), spot, 0.01)
    assert o.bs_gamma(spot, strike, years, iv) == pytest.approx(float(numeric), rel=1e-5)


def test_vanna_is_the_derivative_of_delta_with_respect_to_volatility():
    spot, strike, years, iv = 100.0, 105.0, 0.25, 0.3
    numeric = finite_difference(lambda v: o.bs_delta(spot, strike, years, v, True), iv, 1e-4)
    assert o.bs_vanna(spot, strike, years, iv) == pytest.approx(float(numeric), rel=1e-4)


def test_charm_is_the_change_of_delta_as_time_passes():
    spot, strike, years, iv = 100.0, 105.0, 0.25, 0.3
    # Time passing means years to expiry fall, hence the minus sign.
    numeric = -finite_difference(lambda t: o.bs_delta(spot, strike, t, iv, True), years, 1e-4)
    assert o.bs_charm(spot, strike, years, iv) == pytest.approx(float(numeric), rel=1e-3)


def test_put_delta_is_call_delta_minus_one():
    call = o.bs_delta(100.0, 95.0, 0.5, 0.2, True)
    put = o.bs_delta(100.0, 95.0, 0.5, 0.2, False)
    assert float(put) == pytest.approx(float(call) - 1.0)


NOW = dt.datetime(2026, 3, 2, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))


def chain(rows, expiry="2026-03-20"):
    """rows: (type, strike, open interest, volume, iv)."""
    return pd.DataFrame([{
        "expiry": pd.Timestamp(item[5] if len(item) > 5 else expiry),
        "type": item[0], "strike": float(item[1]), "bid": 1.0, "ask": 1.2, "iv": float(item[4]),
        "open_interest": float(item[2]), "volume": float(item[3]), "delta": 0.5 if item[0] == "C" else -0.5,
        "gamma": 0.01, "vega": 0.1, "theta": -0.1, "last": 1.1, "contract": f"X{item[0]}{item[1]}",
    } for item in rows])


def test_calls_add_gamma_and_puts_subtract_it():
    calls_only = o.prepare_chain(chain([("C", 100, 1000, 10, 0.3)]), 100.0, now=NOW)
    puts_only = o.prepare_chain(chain([("P", 100, 1000, 10, 0.3)]), 100.0, now=NOW)
    assert calls_only["gex"].sum() > 0
    assert puts_only["gex"].sum() < 0
    assert calls_only["gex"].sum() == pytest.approx(-puts_only["gex"].sum())


def test_dollar_gamma_formula_by_hand():
    prepared = o.prepare_chain(chain([("C", 100, 500, 10, 0.3)]), 100.0, now=NOW)
    row = prepared.iloc[0]
    expected = float(o.bs_gamma(100.0, 100.0, row["years"], 0.3)) * 500 * 100 * 100.0 * 100.0 * 0.01
    assert row["gex"] == pytest.approx(expected)


def test_gamma_flip_sits_between_put_heavy_low_strikes_and_call_heavy_high_strikes():
    frame = chain([("P", 90, 20000, 10, 0.25), ("C", 110, 20000, 10, 0.25)])
    prepared = o.prepare_chain(frame, 100.0, now=NOW)
    flip = o.find_gamma_flip(o.gamma_profile(prepared, 100.0), 100.0)
    assert 95.0 < flip < 105.0


def test_no_flip_when_gamma_never_changes_sign():
    prepared = o.prepare_chain(chain([("C", 100, 1000, 10, 0.3)]), 100.0, now=NOW)
    assert o.find_gamma_flip(o.gamma_profile(prepared, 100.0), 100.0) is None


def test_max_pain_by_hand():
    # Heavy calls at 110 and heavy puts at 90 both expire worthless between those strikes.
    frame = chain([("C", 90, 1, 1, 0.3), ("C", 100, 10, 1, 0.3), ("C", 110, 1000, 1, 0.3),
                   ("P", 90, 1000, 1, 0.3), ("P", 100, 10, 1, 0.3), ("P", 110, 1, 1, 0.3)])
    prepared = o.prepare_chain(frame, 100.0, now=NOW)
    assert o.max_pain(prepared) == 100.0


def test_walls_stay_on_their_own_side_of_spot():
    frame = chain([("C", 90, 90000, 1, 0.3), ("C", 110, 1000, 1, 0.3), ("P", 110, 90000, 1, 0.3), ("P", 90, 1000, 1, 0.3)])
    kpis = o.analyze_chain(frame, 100.0, now=NOW)["kpis"]
    assert kpis["call_wall"] == 110.0
    assert kpis["put_wall"] == 90.0


def test_put_call_ratios():
    frame = chain([("C", 100, 100, 50, 0.3), ("P", 100, 300, 200, 0.3)])
    kpis = o.analyze_chain(frame, 100.0, now=NOW)["kpis"]
    assert kpis["pc_volume"] == pytest.approx(4.0)
    assert kpis["pc_oi"] == pytest.approx(3.0)


def test_session_volume_counts_contracts_that_expired_that_day():
    frame = chain([
        ("C", 100, 100, 600, 0.3, "2026-03-02"),
        ("P", 100, 100, 200, 0.3, "2026-03-02"),
        ("C", 100, 100, 100, 0.3, "2026-03-20"),
        ("P", 100, 100, 100, 0.3, "2026-03-20"),
    ])
    after_close = dt.datetime(2026, 3, 2, 18, 0, tzinfo=NOW.tzinfo)
    kpis = o.analyze_chain(frame, 100.0, now=after_close, session="2026-03-02")["kpis"]
    assert kpis["zero_dte_volume_share"] == pytest.approx(80.0)
    assert kpis["pc_volume"] == pytest.approx(300.0 / 700.0)


def test_expired_contracts_do_not_count_toward_gamma():
    frame = chain([("C", 100, 1000, 10, 0.3, "2026-02-20")])
    assert o.prepare_chain(frame, 100.0, now=NOW).empty


def test_scope_selects_expiries():
    frame = chain([("C", 100, 10, 1, 0.3, "2026-03-02"), ("C", 100, 10, 1, 0.3, "2026-03-20"), ("C", 100, 10, 1, 0.3, "2026-06-19")])
    prepared = o.prepare_chain(frame, 100.0, now=NOW)
    assert len(o.select_scope(prepared, "0dte")) == 1
    assert len(o.select_scope(prepared, "30d")) == 2
    assert len(o.select_scope(prepared, "all")) == 3
    assert o.select_scope(prepared, "monthly")["expiry"].iloc[0] == pd.Timestamp("2026-03-20")
    assert len(o.select_scope(prepared, "2026-06-19")) == 1


def test_constant_maturity_iv_interpolates_in_total_variance():
    expiries = [{"dte": 10, "atm_iv": 20.0}, {"dte": 50, "atm_iv": 30.0}]
    variance = (0.2 ** 2 * 10) + ((0.3 ** 2 * 50) - (0.2 ** 2 * 10)) * (30 - 10) / 40
    assert o.constant_maturity_iv(expiries, 30) == pytest.approx((variance / 30) ** 0.5 * 100.0)


def test_empty_scope_is_reported_not_raised():
    frame = chain([("C", 100, 10, 1, 0.3, "2026-03-20")])
    result = o.analyze_chain(frame, 100.0, scope="0dte", now=NOW)
    assert result["empty_scope"] is True


# --------------------------------------------------------------------------- breadth

def breadth_inputs(days=300, members=10):
    index = pd.date_range("2023-01-02", periods=days, freq="B")
    close = pd.DataFrame({f"S{i}": 100.0 + np.arange(days) * 0.1 for i in range(members)}, index=index)
    volume = pd.DataFrame(1000.0, index=index, columns=close.columns)
    return close, volume


def test_everything_rising_means_full_participation():
    close, volume = breadth_inputs()
    result = compute_breadth_series(close, volume)
    assert result["breadth:pct50"].iloc[-1] == pytest.approx(100.0)
    assert result["breadth:pct200"].iloc[-1] == pytest.approx(100.0)
    assert result["breadth:adv"].iloc[-1] == 10
    assert result["breadth:dec"].iloc[-1] == 0
    assert result["breadth:up_vol_share"].iloc[-1] == pytest.approx(100.0)
    assert result["breadth:new_highs"].iloc[-1] == 10
    assert result["breadth:new_lows"].iloc[-1] == 0


def test_advance_decline_line_is_the_running_total():
    close, volume = breadth_inputs(days=5)
    result = compute_breadth_series(close, volume)
    assert result["breadth:ad_line"].dropna().tolist() == [10.0, 20.0, 30.0, 40.0]


def test_mixed_day_counts_each_side():
    close, volume = breadth_inputs(days=30)
    close.iloc[-1, :3] = close.iloc[-2, :3] - 1.0
    result = compute_breadth_series(close, volume)
    assert result["breadth:adv"].iloc[-1] == 7
    assert result["breadth:dec"].iloc[-1] == 3
    assert result["breadth:up_vol_share"].iloc[-1] == pytest.approx(70.0)


def test_days_with_too_few_members_are_dropped():
    close, volume = breadth_inputs(days=30)
    close.iloc[-1, 1:] = np.nan
    result = compute_breadth_series(close, volume)
    assert result["breadth:adv"].index[-1] == close.index[-2]


def test_breadth_thrust_needs_a_recent_low_reading():
    index = pd.date_range("2024-01-01", periods=30, freq="B")
    fired = pd.Series([50.0] * 10 + [35.0, 45.0, 55.0, 62.0] + [65.0] * 16, index=index)
    never_low = pd.Series([50.0] * 10 + [55.0, 58.0, 60.0, 62.0] + [65.0] * 16, index=index)
    assert last_breadth_thrust(fired) == index[13].strftime("%Y-%m-%d")
    assert last_breadth_thrust(never_low) is None


# -------------------------------------------------------------------------- rotation

@pytest.mark.parametrize("x,y,expected", [
    (101, 101, "Leading"), (101, 99, "Weakening"), (99, 99, "Lagging"), (99, 101, "Improving"),
])
def test_rotation_quadrants(x, y, expected):
    assert r.quadrant(x, y) == expected


def test_outperformer_has_positive_relative_strength():
    index = pd.date_range("2022-01-07", periods=120, freq="W-FRI")
    benchmark = pd.Series(100.0, index=index)
    # Flat, then it starts beating the benchmark for the last stretch.
    leader = pd.Series(100.0 + np.where(np.arange(120) > 90, (np.arange(120) - 90) * 2.0, np.sin(np.arange(120))), index=index)
    lines = r.rotation_lines(leader, benchmark, window=14)
    assert lines["rs_ratio"].iloc[-1] > 100.0


def test_performance_row_measures_against_the_benchmark():
    index = pd.date_range("2024-01-01", periods=300, freq="B")
    series = pd.Series(np.linspace(100, 200, 300), index=index)
    benchmark = pd.Series(np.linspace(100, 150, 300), index=index)
    row = r.performance_row(series, benchmark)
    assert row["rel_1m"] == pytest.approx(row["ret_1m"] - ta.period_return(benchmark, 21))
    assert row["ret_1m"] > 0


def test_ratio_state_reads_above_its_average_as_risk_on():
    rising = pd.Series(np.linspace(1.0, 2.0, 200), index=pd.date_range("2024-01-01", periods=200, freq="B"))
    assert r.ratio_state(rising)["state"] == "risk_on"
    assert r.ratio_state(rising[::-1].set_axis(rising.index))["state"] == "risk_off"


# --------------------------------------------------------------------------- studies

def test_cooldown_merges_a_long_episode_into_one_event():
    index = pd.date_range("2024-01-01", periods=40, freq="B")
    mask = pd.Series(False, index=index)
    mask.iloc[5:15] = True     # one long episode
    mask.iloc[30] = True       # and a separate one
    events = select_events(mask, cooldown=10)
    assert list(events) == [index[5], index[30]]
    assert len(select_events(mask, cooldown=0)) == 11


def test_short_gaps_stay_part_of_the_same_event():
    index = pd.date_range("2024-01-01", periods=40, freq="B")
    mask = pd.Series(False, index=index)
    mask.iloc[[5, 8]] = True
    assert list(select_events(mask, cooldown=10)) == [index[5]]


def test_forward_returns_by_hand():
    index = pd.date_range("2024-01-01", periods=12, freq="B")
    prices = pd.Series([100, 100, 110, 110, 110, 110, 121, 121, 121, 121, 121, 121], index=index, dtype=float)
    mask = pd.Series(False, index=index)
    mask.iloc[1] = True
    study = forward_return_study(prices, mask, cooldown=0, horizons={"5 days": 5})
    row = study["horizons"][0]
    assert study["event_count"] == 1
    assert row["signal"]["mean"] == pytest.approx(21.0)
    assert row["signal"]["win_rate"] == pytest.approx(100.0)
    # The final days have no forward return yet, and must not be counted as zero.
    assert row["baseline"]["count"] == 7
    assert row["edge"] == pytest.approx(row["signal"]["mean"] - row["baseline"]["mean"])


def test_condition_spans_for_shading():
    index = pd.date_range("2024-01-01", periods=6, freq="B")
    mask = pd.Series([False, True, True, False, True, True], index=index)
    spans = spans_from_mask(mask)
    assert spans == [
        {"start": index[1].strftime("%Y-%m-%d"), "end": index[2].strftime("%Y-%m-%d")},
        {"start": index[4].strftime("%Y-%m-%d"), "end": index[5].strftime("%Y-%m-%d")},
    ]
