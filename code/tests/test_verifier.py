from orca.agents.verifier import extract_numbers, verify
from orca.evidence import EvidenceLedger, Source

SRC = [Source("dataset", "D-FISH", "landings", "cite", synthetic=True)]


def ledger_with(*payloads):
    led = EvidenceLedger()
    for p in payloads:
        led.add("query_data", {}, "rows", p, SRC)
    return led


def test_verified_claim_with_rounding_and_thousands():
    led = ledger_with({"rows": [{"year": 2013, "t": 390830}, {"year": 2016, "t": 48670}]})
    rep = verify("Landings fell from 390,830 tonnes in 2013 to about 49,000 tonnes in 2016 [E1].", led)
    assert rep.passed and rep.score == 1.0
    assert rep.claims[0].status == "verified"


def test_unsupported_number_fails():
    led = ledger_with({"r": 0.9401})
    rep = verify("The correlation was r=0.62 [E1].", led)
    assert not rep.passed
    assert rep.claims[0].status == "unsupported"


def test_scientific_notation_and_percent_scaling():
    led = ledger_with({"pearson_r": 0.9401, "r2": 0.8838, "p": 7.56e-10})
    rep = verify("r=0.94 (p=7.56e-10) explains 88% of the variance [E1].", led)
    assert rep.passed, rep.issues


def test_invalid_citation_fails():
    led = ledger_with({"x": 1.5})
    rep = verify("Value 1.5 [E7].", led)
    assert not rep.passed
    assert rep.claims[0].status == "invalid_citation"


def test_miscited_and_uncited_are_warnings():
    led = ledger_with({"a": 27.31}, {"b": 0.452})
    rep = verify("Mean SST was 27.31 degC [E2]. The slope was 0.452 per decade.", led)
    assert rep.passed
    assert [c.status for c in rep.claims] == ["miscited", "uncited"]
    assert rep.score == 0.0


def test_years_small_counts_and_ids_are_ignored():
    nums = extract_numbers("Between 2004-2024 the 6 regions in E12 and qwen3 saw 2016 heat [E3].")
    assert nums == []


def test_answer_without_citations_fails_when_evidence_exists():
    led = ledger_with({"a": 1.0})
    assert not verify("Everything is fine.", led).passed


def test_knowledge_thresholds_count_as_evidence():
    led = EvidenceLedger()
    led.add("search_knowledge", {}, "Alert Level 2 starts at DHW of 8 degC-weeks.", {"passages": []}, [])
    led.add("query_data", {}, "rows", {"rows": [{"max_dhw": 11.4}]}, SRC)
    rep = verify("DHW reached 11.4 degC-weeks [E2], above the Level 2 threshold of 8 degC-weeks [E1].", led)
    assert rep.passed, rep.issues
