import pytest

from orca.data.store import QueryError
from orca.tools import ALL_TOOLS, ToolError


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fish_landings",
        "SELECT 1; DROP TABLE fish_landings",
        "ATTACH DATABASE 'x.db' AS x",
        "PRAGMA table_info(fish_landings)",
        "INSERT INTO fish_landings VALUES ('a','b','c',1,2)",
        "WITH x AS (SELECT 1) DELETE FROM fish_landings",
    ],
)
def test_store_rejects_writes(store, sql):
    with pytest.raises(QueryError):
        store.query(sql)


def test_store_allows_select_and_catalog(store):
    rows = store.query("SELECT COUNT(*) AS n FROM ocean_monthly")
    assert rows[0]["n"] == 6 * 21 * 12
    assert {d.dataset_id for d in store.datasets} == {"D-OCEAN", "D-FISH", "D-OCC", "D-CORAL", "D-SPECIES"}
    assert store.dataset("ocean_monthly").synthetic is True
    assert store.dataset("D-SPECIES").synthetic is False


async def test_query_data_groups_and_records_evidence(ctx):
    res = await ALL_TOOLS["query_data"].run(
        {"dataset": "D-CORAL", "columns": ["max(max_dhw)"], "where": {"reef_area": "Lakshadweep", "year": [2016]},
         "group_by": ["year"]},
        ctx,
    )
    assert res.record and res.sources[0].id == "D-CORAL" and res.sources[0].synthetic
    assert res.data["rows"][0]["year"] == 2016  # group column added automatically
    assert "max_max_dhw" in res.data["rows"][0]


async def test_query_data_rejects_unaggregated_group_column(ctx):
    with pytest.raises(ToolError, match="must be aggregated"):
        await ALL_TOOLS["query_data"].run(
            {"dataset": "D-CORAL", "columns": ["year", "max_dhw"], "group_by": ["year"]}, ctx
        )


async def test_query_data_empty_result_lists_valid_values(ctx):
    res = await ALL_TOOLS["query_data"].run({"dataset": "D-CORAL", "where": {"site": "Lakshadweep Sea"}}, ctx)
    assert not res.record
    assert "Kavaratti" in res.text


async def test_trend_finds_warming(ctx):
    res = await ALL_TOOLS["trend"].run(
        {"dataset": "D-OCEAN", "metric": "sst_c", "agg": "avg", "where": {"region": "Andaman Sea"}}, ctx
    )
    assert res.data["slope_per_year"] > 0
    assert res.data["n_years"] == 21
    assert res.data["slope_per_decade"] == pytest.approx(res.data["slope_per_year"] * 10, rel=1e-2)


async def test_correlate_sardine_upwelling_lag(ctx):
    res = await ALL_TOOLS["correlate"].run(ALL_TOOLS["correlate"].example, ctx)
    assert res.data["lag_years"] == 1
    assert res.data["pearson_r"] > 0.6
    assert len(res.sources) == 2


async def test_anomaly_monthly_with_month_filter(ctx):
    res = await ALL_TOOLS["anomaly"].run(
        {"dataset": "D-OCEAN", "metric": "sst_c", "where": {"region": "Lakshadweep Sea", "month": [3, 4, 5]},
         "baseline": [2004, 2013], "mode": "monthly"},
        ctx,
    )
    assert res.data["n_flagged_months"] > 0
    assert 2016 in res.data["flagged_months_per_year"] or 2024 in res.data["flagged_months_per_year"]


async def test_anomaly_year_filter_limits_reporting_not_baseline(ctx):
    # Regression: an agent passed year [2014, 2024] meaning a range, which used to
    # remove the 2004-2013 baseline from the series and fail.
    res = await ALL_TOOLS["anomaly"].run(
        {"dataset": "D-OCEAN", "metric": "sst_c", "where": {"region": "Lakshadweep Sea", "year": [2014, 2024]},
         "baseline": [2004, 2013], "mode": "monthly"},
        ctx,
    )
    years = set(res.data["flagged_months_per_year"])
    assert years and min(years) >= 2014
    # Regression: monthly series (252 rows) were silently cut at the 200-row agent limit.
    assert 2024 in years
    assert "2016-04" in res.data["flagged_months"] or "2016-05" in res.data["flagged_months"]
    annual = await ALL_TOOLS["anomaly"].run(
        {"dataset": "D-OCEAN", "metric": "sst_c", "where": {"region": "Lakshadweep Sea", "year": {"between": [2015, 2017]}}},
        ctx,
    )
    assert [r["year"] for r in annual.data["years"]] == [2015, 2016, 2017]


async def test_trend_accepts_aggregate_in_metric(ctx):
    res = await ALL_TOOLS["trend"].run(
        {"dataset": "D-OCC", "metric": "sum(individual_count)",
         "where": {"scientific_name": "Lepidochelys olivacea", "region": "NW Bay of Bengal"}},
        ctx,
    )
    assert res.data["series"].startswith("sum(individual_count)")


async def test_calculate_requires_traced_inputs(ctx):
    calc = ALL_TOOLS["calculate"]
    untraced = await calc.run({"expression": "(123456 - 654321) / 654321 * 100"}, ctx)
    assert not untraced.record and "REJECTED" in untraced.text
    ctx.ledger.add("query_data", {}, "x", {"rows": [{"a": 390830}, {"a": 48670}]}, [])
    traced = await calc.run({"expression": "(48670 - 390830) / 390830 * 100"}, ctx)
    assert traced.record
    assert traced.data["result"] == pytest.approx(-87.547, abs=1e-3)


async def test_calculate_blocks_code(ctx):
    with pytest.raises(ToolError):
        await ALL_TOOLS["calculate"].run({"expression": "__import__('os').system('echo hi')"}, ctx)


async def test_search_knowledge_returns_cited_passages(ctx):
    res = await ALL_TOOLS["search_knowledge"].run({"query": "degree heating weeks bleaching alert level"}, ctx)
    assert res.sources[0].id == "K-DHW"
    assert "8 degC-weeks" in res.text


async def test_obis_disabled_by_setting(ctx):
    with pytest.raises(ToolError, match="disabled"):
        await ALL_TOOLS["obis_occurrences"].run({"scientific_name": "Rhincodon typus"}, ctx)
