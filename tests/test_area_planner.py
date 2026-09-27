"""Published milestone years, counted once per nearby project."""
import pandas as pd
from gridlock.area_planner import add_timing_evidence, rank_year_spans, find_nearby_projects, lookup_zip

projects = pd.DataFrame([
    dict(project_key="A", utility="DESC", distance_km=5, in_service_date="2030-01-01"),
    dict(project_key="B", utility="GPC", distance_km=8, need_date="2031-01-01"),
    dict(project_key="C", utility="GPC", distance_km=1, need_date="", status="Planned"),
    dict(project_key="D", utility="GPC", distance_km=2, need_date="2030-01-01", status="Cancelled"),
]).fillna("")
evidence = add_timing_evidence(projects)
spans = rank_year_spans(evidence, 2028, 2032)
assert spans.nearby_projects.max() == 1
assert set(spans.start_year) == {2030, 2031}
assert evidence[evidence.project_key.eq("C")].iloc[0].planning_years == []
assert evidence[evidence.project_key.eq("D")].iloc[0].planning_years == []
assert rank_year_spans(evidence, 2040, 2050).empty
assert rank_year_spans(add_timing_evidence(projects.iloc[:0]), 2028, 2032).empty

multiple = projects.iloc[[0]].copy()
multiple["in_service_dates"] = '["2030-01-01", "2035-01-01"]'
multi_evidence = add_timing_evidence(multiple)
assert multi_evidence.iloc[0].planning_years == [2030, 2035]
assert rank_year_spans(pd.concat([multi_evidence, multi_evidence]), 2029, 2035).nearby_projects.max() == 1
multiple["in_service_dates"] = '["2030-01-01", "2031-01-01", "2033-01-01"]'
adjacent = rank_year_spans(add_timing_evidence(multiple), 2030, 2033)
assert list(zip(adjacent.start_year, adjacent.end_year)) == [(2030, 2031), (2033, 2033)]

route = pd.DataFrame([dict(project_key="route", utility="DESC", longitude=0, latitude=0,
                          geometry={"type":"LineString", "coordinates":[[-2,0],[2,0]]})])
nearby = find_nearby_projects(route, 0.01, 1.8, 5)
assert len(nearby) == 1 and nearby.iloc[0].location_basis == "route"
assert nearby.iloc[0].distance_km < 2
assert find_nearby_projects(route, 30, 30, 5).empty
for invalid in ["abc", "3090", "123456"]:
    try:
        lookup_zip(invalid)
        raise AssertionError("Invalid ZIP accepted")
    except ValueError:
        pass
print("Area lookup, nearest routes, milestone-only timing and year ranking checks passed")
