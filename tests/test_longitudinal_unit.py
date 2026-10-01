"""E5-S1 (unit): which orders are the same medication, what changed, which supersedes which."""

from __future__ import annotations

from datetime import date

from cdi_adapter.longitudinal import change_kind, desired_links, match_key


def _o(i, d, code=None, drug="Tab Amlodipine 5 mg", **m):
    return {"id": i, "encounter_date": d, "code_system": "http://snomed.info/sct" if code else None,
            "code": code, "drug_text": drug, "local_text": drug, **m}  # fmt: skip


def test_the_same_medication_is_the_same_code_else_the_same_name():
    assert match_key(_o(1, None, code="386864001")) == "code:http://snomed.info/sct|386864001"
    assert match_key(_o(1, None, drug="Tab Amlodipine 5 mg")) == match_key(
        _o(2, None, drug="Amlodipine 10mg Tablet")
    )  # form words and strength are not the medicine's name
    assert match_key(_o(1, None, drug="Tab Amlodipine 5 mg")) != match_key(
        _o(2, None, drug="Tab Atorvastatin 10 mg")
    )
    assert match_key(_o(1, None, drug="")) is None


def test_what_changed_between_two_orders():
    old = {"strength_num": 5, "dose_num": 1, "frequency_code": "OD", "duration_days": 30}
    assert change_kind(dict(old), old) == "reordered"
    assert change_kind({**old, "strength_num": 10}, old) == "strength_changed"
    assert change_kind({**old, "frequency_code": "BD"}, old) == "frequency_changed"
    assert change_kind({**old, "strength_num": 10, "frequency_code": "BD"}, old) == "changed"
    assert change_kind({**old, "duration_days": None}, old) == "reordered"  # not written: no claim


def test_each_date_supersedes_the_latest_earlier_date_only():
    d1, d2, d3 = date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)
    orders = [_o("a", d1, code="X"), _o("b", d2, code="X", strength_num=10), _o("c", d3, code="X"),
              _o("z", d1, code="Y")]  # fmt: skip
    links = desired_links(orders)
    assert set(links) == {("b", "a"), ("c", "b")}  # a chain, never c -> a; Y stays alone
    assert links[("b", "a")]["superseded_date"] == d1 and links[("c", "b")]["encounter_date"] == d3


def test_orders_of_one_date_never_supersede_each_other():
    d = date(2026, 8, 1)
    assert desired_links([_o("a", d, code="X"), _o("b", d, code="X")]) == {}
