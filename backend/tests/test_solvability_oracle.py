import random

import pytest

from backend.app.dispatch import detect_conflicts
from backend.benchmarks.solvability_cases import (
    SolvabilityAgent,
    SolvabilityCase,
    generate_solvability_cases,
    solvability_catalog,
    solvability_cases,
)
from backend.benchmarks.solvability_oracle import solve_exact


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


def _catalog_case(case_id: str) -> SolvabilityCase:
    return next(case for case in solvability_catalog() if case.case_id == case_id)


def test_exact_oracle_solves_catalog_cases_with_minimum_makespan() -> None:
    solo = solve_exact(_catalog_case("catalog-solo-straight"), 100_000)
    independent = solve_exact(_catalog_case("catalog-independent-r2"), 100_000)
    bypass = solve_exact(_catalog_case("catalog-side-bypass-swap-r2"), 100_000)

    assert (solo.outcome, solo.makespan) == ("solved", 2)
    assert (independent.outcome, independent.makespan) == ("solved", 2)
    assert (bypass.outcome, bypass.makespan) == ("solved", 4)
    for result in (solo, independent, bypass):
        assert result.paths is not None
        assert detect_conflicts(result.paths) == []


def test_exact_oracle_reports_no_bypass_swap_as_unsolved() -> None:
    result = solve_exact(_catalog_case("catalog-no-bypass-swap-r2"), 100_000)

    assert result.outcome == "unsolved"
    assert result.makespan is None
    assert result.paths is None


def test_exact_oracle_reports_limit_instead_of_unsolved() -> None:
    result = solve_exact(_catalog_case("catalog-side-bypass-swap-r2"), 1)

    assert result.outcome == "limit"
    assert result.expanded_state_count == 1
    assert result.paths is None


def test_exact_oracle_handles_initial_goal_and_is_deterministic() -> None:
    case = SolvabilityCase(
        "already-there",
        "generated",
        1,
        1,
        (),
        (SolvabilityAgent("R1", (0, 0), (0, 0)),),
    )
    first = solve_exact(case, 100)
    second = solve_exact(case, 100)

    assert first == second
    assert first.makespan == 0
    assert first.expanded_state_count == 0
    assert first.paths == {"R1": [(0, 0)]}


def test_exact_oracle_rejects_non_positive_expansion_limit() -> None:
    with pytest.raises(ValueError, match="max_expanded_states"):
        solve_exact(_catalog_case("catalog-solo-straight"), 0)


def test_exact_oracle_solved_paths_visit_targets_and_share_horizon() -> None:
    for case_id in (
        "catalog-solo-straight",
        "catalog-independent-r2",
        "catalog-side-bypass-swap-r2",
    ):
        case = _catalog_case(case_id)
        result = solve_exact(case, 100_000)
        assert result.paths is not None
        for agent in case.agents:
            assert result.paths[agent.agent_id][0] == agent.start
            assert agent.goal in result.paths[agent.agent_id]
        assert len({len(path) for path in result.paths.values()}) == 1
