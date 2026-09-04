from dataclasses import replace
from time import perf_counter

from backend.app.dispatch import build_paths, detect_conflicts
from backend.app.schemas import Assignment, Scenario
from backend.benchmarks.solvability_cases import SolvabilityCase
from backend.benchmarks.solvability_oracle import OracleOutcome, solve_exact
from backend.benchmarks.solvability_results import (
    ComparisonClass,
    PlannerOutcome,
    SolvabilityRun,
)


def build_fixed_assignment_input(
    case: SolvabilityCase,
) -> tuple[Scenario, list[Assignment]]:
    robots = [
        {
            "id": agent.agent_id,
            "name": agent.agent_id,
            "start": agent.start,
            "battery": 10_000,
            "batteryCapacity": 10_000,
            "load": 1,
            "moveTicks": 1,
            "capabilities": ["inspection"],
        }
        for agent in case.agents
    ]
    tasks = [
        {
            "id": f"GOAL-{agent.agent_id}",
            "type": "inspection",
            "title": f"Goal {agent.agent_id}",
            "priority": 1,
            "releaseTime": 0,
            "serviceTime": 0,
            "targets": [agent.goal],
        }
        for agent in case.agents
    ]
    scenario = Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "离线小规模可解性差分案例",
            "width": case.width,
            "height": case.height,
            "obstacles": case.obstacles,
            "zones": {
                "warehouse": [agent.start for agent in case.agents],
                "inspection": [agent.goal for agent in case.agents],
                "delivery": [],
                "charging": [],
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    assignments = [
        Assignment(robotId=robot.id, tasks=[task])
        for robot, task in zip(scenario.robots, scenario.tasks, strict=True)
    ]
    return scenario, assignments


def classify_comparison(
    oracle_outcome: OracleOutcome,
    planner_outcome: PlannerOutcome,
) -> ComparisonClass:
    if oracle_outcome == "limit":
        return "oracleLimit"
    if oracle_outcome == "solved":
        return (
            "agreementSolved"
            if planner_outcome == "solved"
            else "oracleSolvedPlannerMiss"
        )
    return (
        "oracleUnsolvedPlannerSolved"
        if planner_outcome == "solved"
        else "oracleUnsolvedPlannerNoValidPlan"
    )


def execute_solvability_case(
    case: SolvabilityCase,
    run_index: int,
    max_expanded_states: int,
) -> SolvabilityRun:
    started_at = perf_counter()
    oracle = solve_exact(case, max_expanded_states)
    if (
        case.expected_oracle_outcome is not None
        and oracle.outcome != case.expected_oracle_outcome
    ):
        raise AssertionError(
            f"{case.case_id} oracle outcome changed: "
            f"{oracle.outcome} != {case.expected_oracle_outcome}"
        )

    scenario, assignments = build_fixed_assignment_input(case)
    paths, failures = build_paths(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
    )
    conflicts = detect_conflicts(paths)
    planner_reached_all_goals = all(
        agent.agent_id in paths
        and agent.goal in paths[agent.agent_id]
        for agent in case.agents
    )
    if failures or not planner_reached_all_goals:
        planner_outcome: PlannerOutcome = "failed"
    elif conflicts:
        planner_outcome = "conflicted"
    else:
        planner_outcome = "solved"
    planner_makespan = (
        max(
            paths[agent.agent_id].index(agent.goal)
            for agent in case.agents
        )
        if planner_reached_all_goals
        else None
    )
    return SolvabilityRun(
        case_id=case.case_id,
        source=case.source,
        run_index=run_index,
        width=case.width,
        height=case.height,
        robot_count=len(case.agents),
        obstacle_count=len(case.obstacles),
        outcome="completed",
        error_type=None,
        error_message=None,
        oracle_outcome=oracle.outcome,
        oracle_makespan=oracle.makespan,
        oracle_expanded_state_count=oracle.expanded_state_count,
        oracle_paths=oracle.paths,
        planner_outcome=planner_outcome,
        planner_makespan=planner_makespan,
        planner_reached_all_goals=planner_reached_all_goals,
        planner_failure_count=len(failures),
        planner_failures=list(failures),
        planner_conflict_count=len(conflicts),
        planner_conflicts=[
            conflict.model_dump(mode="json") for conflict in conflicts
        ],
        planner_paths=paths,
        comparison_class=classify_comparison(oracle.outcome, planner_outcome),
        wall_clock_ms=round((perf_counter() - started_at) * 1_000, 2),
    )
