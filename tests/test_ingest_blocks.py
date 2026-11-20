"""The point-to-block conversion, on small hand-built curves.

CAISO publishes a bid curve as points where the price at a point applies up to the
next point and the last point only closes the range. These tests pin that reading.
"""
import pathlib
import sys

import duckdb
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from ingest import build_bids, build_virtuals

T0, T1 = "2025-07-10T18:00:00-00:00", "2025-07-10T19:00:00-00:00"
T2 = "2025-07-10T20:00:00-00:00"
HOURS = [(T0, T1), (T1, T2)]


def physical(rows, direction=None):
    """rows: (res, res_type, x, y, self_sched_mw[, hour]). Curve points leave self_sched_mw
    None; a self-schedule row leaves x and y None, as in the published file."""
    con = duckdb.connect()
    con.execute("set TimeZone='UTC'")
    con.execute("""create table raw (
        RESOURCEBID_SEQ bigint, SCHEDULINGCOORDINATOR_SEQ bigint, RESOURCE_TYPE varchar,
        MARKETPRODUCTTYPE varchar, SELFSCHEDMW double,
        TIMEINTERVALSTART_GMT varchar, TIMEINTERVALEND_GMT varchar,
        SCH_BID_TIMEINTERVALSTART_GMT varchar, SCH_BID_TIMEINTERVALSTOP_GMT varchar,
        SCH_BID_XAXISDATA double, SCH_BID_Y1AXISDATA double)""")
    for res, typ, x, y, ss, *hour in rows:
        curve = x is not None
        t0, t1 = HOURS[hour[0] if hour else 0]
        con.execute("insert into raw values (?, 1, ?, 'EN', ?, ?, ?, ?, ?, ?, ?)",
                    [res, typ, ss, None if curve else t0, None if curve else t1,
                     t0 if curve else None, t1 if curve else None, x, y])
    if direction:
        con.execute("create table extra (res bigint, direction varchar, basis varchar)")
        con.executemany("insert into extra values (?, ?, 'test')", list(direction.items()))
    build_bids.make_tables(con, "raw", "extra" if direction else None)
    return con


def blocks(con, res):
    return con.execute("select side, mw, price from bid_blocks where res = ? order by price, mw",
                       [res]).fetchall()


def test_price_applies_from_a_point_up_to_the_next():
    con = physical([(1, "GENERATOR", 0, 20, None), (1, "GENERATOR", 50, 30, None),
                    (1, "GENERATOR", 80, 45, None), (1, "GENERATOR", 100, 45, None)])
    assert blocks(con, 1) == [("supply", 50, 20), ("supply", 30, 30), ("supply", 20, 45)]


def test_self_scheduled_mw_is_not_counted_twice():
    # A 113 MW bid: 56.5 MW self-scheduled, 56.5 MW offered at -22.
    con = physical([(2, "GENERATOR", None, None, 56.5),
                    (2, "GENERATOR", 56.5, -22, None), (2, "GENERATOR", 113, -22, None)])
    assert blocks(con, 2) == [("supply", 56.5, -22)]
    ss = con.execute("select side, mw from self_sched where res = 2").fetchall()
    assert ss == [("supply", 56.5)]
    total = con.execute("""select (select sum(mw) from bid_blocks where res = 2)
                                + (select sum(mw) from self_sched where res = 2)""").fetchone()[0]
    assert total == pytest.approx(113)


def test_minimum_load_below_the_first_point_is_left_out():
    con = physical([(3, "GENERATOR", 100, 35, None), (3, "GENERATOR", 300, 35, None)])
    assert blocks(con, 3) == [("supply", 200, 35)]


def test_flat_load_bid_is_demand():
    con = physical([(4, "LOAD", 0, 60, None), (4, "LOAD", 25, 60, None)])
    assert blocks(con, 4) == [("demand", 25, 60)]


def test_falling_load_curve_is_priced_at_the_lower_mw_point():
    con = physical([(5, "LOAD", 0, 90, None), (5, "LOAD", 10, 70, None), (5, "LOAD", 30, 70, None)])
    assert blocks(con, 5) == [("demand", 20, 70), ("demand", 10, 90)]


def test_intertie_side_follows_slope():
    con = physical([(6, "INTERTIE", 0, 40, None), (6, "INTERTIE", 50, 55, None), (6, "INTERTIE", 80, 55, None),
                    (7, "INTERTIE", 0, 80, None), (7, "INTERTIE", 50, 30, None), (7, "INTERTIE", 80, 30, None)])
    assert {s for s, _, _ in blocks(con, 6)} == {"supply"}
    assert {s for s, _, _ in blocks(con, 7)} == {"demand"}


def test_points_sharing_a_mw_value_take_the_lower_price():
    # Two configurations of a multi-stage generator, interleaved under one id.
    con = physical([(8, "GENERATOR", 100, 30, None), (8, "GENERATOR", 200, 30, None),
                    (8, "GENERATOR", 200, 50, None), (8, "GENERATOR", 300, 50, None)])
    assert blocks(con, 8) == [("supply", 100, 30), ("supply", 100, 30)]


def test_virtual_curve_uses_the_same_convention():
    con = duckdb.connect()
    con.execute("""create table v (PSEUDO_NODE_ID bigint, PSEUDO_SC_ID bigint, NODE_TYPE varchar,
        SUPPLY_DEMAND_FLAG varchar, BID_INT_START_TIME_GMT timestamp,
        SCHEDULED_BID_X double, SCHEDULED_BID_Y double)""")
    for x, y in [(0, -1.36), (1, -5.61), (2, -23.88), (3, -23.88)]:
        con.execute("insert into v values (1, 1, 'P Node', 'Demand', '2025-07-10 18:00:00', ?, ?)", [x, y])
    build_virtuals.make_view(con, "v")
    got = con.execute("select side, mw, price from vblocks order by price desc").fetchall()
    assert got == [("demand", 1, -1.36), ("demand", 1, -5.61), ("demand", 1, -23.88)]


def test_storage_curve_is_split_at_zero():
    # Charging 100 MW up to a price of 15, discharging 100 MW from 60.
    con = physical([(20, "GENERATOR", -100, 15, None), (20, "GENERATOR", 0, 60, None),
                    (20, "GENERATOR", 100, 60, None)])
    assert blocks(con, 20) == [("demand", 100, 15), ("supply", 100, 60)]


def test_segment_straddling_zero_gives_both_sides_at_one_price():
    con = physical([(21, "GENERATOR", -100, 30, None), (21, "GENERATOR", 100, 30, None)])
    assert sorted(blocks(con, 21)) == [("demand", 100, 30), ("supply", 100, 30)]


def test_storage_charging_steps_keep_their_own_prices():
    con = physical([(22, "GENERATOR", -80, 5, None), (22, "GENERATOR", -30, 12, None),
                    (22, "GENERATOR", 40, 70, None), (22, "GENERATOR", 90, 70, None)])
    assert blocks(con, 22) == [("demand", 50, 5), ("demand", 30, 12),
                               ("supply", 40, 12), ("supply", 50, 70)]


def test_negative_self_schedule_is_must_take_demand():
    # Committed to charge 50 MW, with a further 50 MW of charging offered up to 10.
    con = physical([(23, "GENERATOR", None, None, -50),
                    (23, "GENERATOR", -100, 10, None), (23, "GENERATOR", -50, 10, None)])
    assert blocks(con, 23) == [("demand", 50, 10)]
    ss = con.execute("select side, mw, price from self_sched where res = 23").fetchall()
    assert ss == [("demand", 50, build_bids.BID_CAP)]


def test_self_schedule_rows_for_one_hour_add_up():
    # Two self-schedule types, 40 and 25 MW; the curve starts at their sum.
    con = physical([(30, "GENERATOR", None, None, 40), (30, "GENERATOR", None, None, 25),
                    (30, "GENERATOR", 65, 30, None), (30, "GENERATOR", 100, 30, None)])
    assert con.execute("select side, mw from self_sched where res = 30").fetchall() == [("supply", 65)]
    assert blocks(con, 30) == [("supply", 35, 30)]


def test_repeated_identical_self_schedule_row_counts_once():
    con = physical([(31, "LOAD", None, None, 40), (31, "LOAD", None, None, 40)])
    assert con.execute("select side, mw from self_sched where res = 31").fetchall() == [("demand", 40)]


def test_exporter_self_schedule_is_must_take_demand():
    # Hour 0: a falling curve marks the resource as an export. Hour 1: it only
    # self-schedules, and that must follow the resource's direction.
    con = physical([(32, "INTERTIE", 0, 80, None), (32, "INTERTIE", 50, 40, None),
                    (32, "INTERTIE", 100, 40, None),
                    (32, "INTERTIE", None, None, 70, 1)])
    ss = con.execute("select side, mw, price from self_sched where res = 32").fetchall()
    assert ss == [("demand", 70, build_bids.BID_CAP)]


def test_flat_intertie_curve_takes_its_resources_direction():
    con = physical([(33, "INTERTIE", 0, 80, None), (33, "INTERTIE", 50, 40, None),
                    (33, "INTERTIE", 100, 40, None),
                    (33, "INTERTIE", 0, 55, None, 1), (33, "INTERTIE", 30, 55, None, 1)])
    flat = con.execute("select side, mw, price from bid_blocks where res = 33 and price = 55").fetchall()
    assert flat == [("demand", 30, 55)]


def test_direction_table_labels_resources_with_no_sloped_curve():
    rows = [(34, "INTERTIE", None, None, 20), (35, "INTERTIE", None, None, 20),
            (36, "INTERTIE", 0, 10, None), (36, "INTERTIE", 15, 10, None)]
    con = physical(rows, direction={34: "export", 36: "export"})
    ss = dict(con.execute("select res, side from self_sched").fetchall())
    assert ss == {34: "demand", 35: "supply"}      # 35 has no evidence: import
    assert blocks(con, 36) == [("demand", 15, 10)]


def test_a_sloped_curve_outranks_the_direction_table():
    con = physical([(37, "INTERTIE", 0, 20, None), (37, "INTERTIE", 50, 30, None),
                    (37, "INTERTIE", 80, 30, None), (37, "INTERTIE", None, None, 10)],
                   direction={37: "export"})
    assert con.execute("select side from self_sched where res = 37").fetchall() == [("supply",)]


def test_wheel_pairs_are_split_one_each_way():
    import pandas as pd
    from ingest.intertie_direction import wheel_pairs
    hours = pd.date_range("2025-01-01", periods=48, freq="h")
    def prof(res, mw):
        return pd.DataFrame({"res": res, "ts_gmt": hours, "mw": mw})
    ss = pd.concat([prof(1, 100.0), prof(2, 100.0), prof(3, 55.0)])
    assert wheel_pairs(ss) == {1: "import", 2: "export"}
