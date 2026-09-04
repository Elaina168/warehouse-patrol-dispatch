import random

import pytest

from backend.benchmarks.solvability_cases import (
    SolvabilityAgent,
    SolvabilityCase,
    generate_solvability_cases,
    solvability_catalog,
    solvability_cases,
)


def test_solvability_catalog_has_exact_ids_and_expected_outcomes() -> None:
    cases = solvability_catalog()

    assert [case.case_id for case in cases] == [
        "catalog-solo-straight",
        "catalog-independent-r2",
        "catalog-side-bypass-swap-r2",
        "catalog-no-bypass-swap-r2",
    ]
    assert [case.expected_oracle_outcome for case in cases] == [
        "solved",
        "solved",
        "solved",
        "unsolved",
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"width": 0},
        {"width": 6},
        {"height": 6},
        {"obstacles": ((9, 9),)},
        {
            "agents": (
                SolvabilityAgent("R1", (0, 0), (1, 0)),
                SolvabilityAgent("R2", (0, 0), (1, 1)),
            )
        },
        {
            "agents": (
                SolvabilityAgent("R1", (0, 0), (1, 0)),
                SolvabilityAgent("R2", (0, 1), (1, 0)),
            )
        },
    ],
)
def test_solvability_case_rejects_invalid_bounded_inputs(updates) -> None:
    values = {
        "case_id": "invalid",
        "source": "generated",
        "width": 2,
        "height": 2,
        "obstacles": (),
        "agents": (SolvabilityAgent("R1", (0, 0), (1, 1)),),
        "expected_oracle_outcome": None,
    }
    values.update(updates)

    with pytest.raises(ValueError):
        SolvabilityCase(**values)


def test_generated_solvability_cases_are_exact_count_unique_and_deterministic() -> None:
    first = generate_solvability_cases(seed=20260904, sample_count=12)
    second = generate_solvability_cases(seed=20260904, sample_count=12)

    assert first == second
    assert len(first) == 12
    assert len({case.case_id for case in first}) == 12
    assert len({case.case_key for case in first}) == 12
    assert all(case.source == "generated" for case in first)
    assert all(agent.start != agent.goal for case in first for agent in case.agents)


def test_generated_solvability_cases_do_not_change_global_random_state() -> None:
    random.seed(73)
    before = random.getstate()

    generate_solvability_cases(seed=20260904, sample_count=4)

    assert random.getstate() == before


def test_solvability_cases_prepends_catalog_and_accepts_zero_samples() -> None:
    assert solvability_cases(20260904, 0) == solvability_catalog()
