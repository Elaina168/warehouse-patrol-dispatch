from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import product
from typing import Literal

from backend.app.schemas import Cell
from backend.benchmarks.solvability_cases import SolvabilityCase


OracleOutcome = Literal["solved", "unsolved", "limit"]
JointState = tuple[Cell, ...]
SearchState = tuple[JointState, int]
_MOVE_DELTAS = ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0))


@dataclass(frozen=True, slots=True)
class OracleResult:
    outcome: OracleOutcome
    makespan: int | None
    expanded_state_count: int
    paths: dict[str, list[Cell]] | None


def _has_edge_swap(previous: JointState, following: JointState) -> bool:
    return any(
        previous[first] == following[second]
        and previous[second] == following[first]
        for first in range(len(previous))
        for second in range(first + 1, len(previous))
    )


def _joint_successors(case: SolvabilityCase, state: JointState):
    blocked = set(case.obstacles)
    choices: list[tuple[Cell, ...]] = []
    for x, y in state:
        moves = []
        for dx, dy in _MOVE_DELTAS:
            target = (x + dx, y + dy)
            if (
                0 <= target[0] < case.width
                and 0 <= target[1] < case.height
                and target not in blocked
            ):
                moves.append(target)
        choices.append(tuple(moves))
    for following in product(*choices):
        if len(set(following)) != len(following):
            continue
        if _has_edge_swap(state, following):
            continue
        yield following


def _completed_mask(
    positions: JointState,
    goals: JointState,
    previous: int = 0,
) -> int:
    completed = previous
    for index, (cell, goal) in enumerate(zip(positions, goals, strict=True)):
        if cell == goal:
            completed |= 1 << index
    return completed


def _heuristic(
    positions: JointState,
    goals: JointState,
    completed_mask: int,
) -> int:
    return max(
        (
            0
            if completed_mask & (1 << index)
            else abs(cell[0] - goal[0]) + abs(cell[1] - goal[1])
        )
        for index, (cell, goal) in enumerate(
            zip(positions, goals, strict=True)
        )
    )


def _reconstruct_paths(
    case: SolvabilityCase,
    came_from: dict[SearchState, SearchState],
    goal_state: SearchState,
) -> dict[str, list[Cell]]:
    states = [goal_state]
    while states[-1] in came_from:
        states.append(came_from[states[-1]])
    states.reverse()
    return {
        agent.agent_id: [state[0][index] for state in states]
        for index, agent in enumerate(case.agents)
    }


def solve_exact(
    case: SolvabilityCase,
    max_expanded_states: int,
) -> OracleResult:
    if max_expanded_states <= 0:
        raise ValueError("max_expanded_states must be positive")
    start_positions = tuple(agent.start for agent in case.agents)
    goals = tuple(agent.goal for agent in case.agents)
    all_completed_mask = (1 << len(case.agents)) - 1
    start_completed_mask = _completed_mask(start_positions, goals)
    start: SearchState = (start_positions, start_completed_mask)
    if start_completed_mask == all_completed_mask:
        return OracleResult(
            "solved",
            0,
            0,
            {agent.agent_id: [agent.start] for agent in case.agents},
        )

    open_heap: list[tuple[int, int, JointState, int]] = []
    heappush(
        open_heap,
        (_heuristic(start_positions, goals, start_completed_mask), 0, *start),
    )
    best_g = {start: 0}
    came_from: dict[SearchState, SearchState] = {}
    expanded_state_count = 0

    while open_heap:
        _priority, distance, positions, completed_mask = heappop(open_heap)
        state: SearchState = (positions, completed_mask)
        if best_g.get(state) != distance:
            continue
        if completed_mask == all_completed_mask:
            return OracleResult(
                "solved",
                distance,
                expanded_state_count,
                _reconstruct_paths(case, came_from, state),
            )
        if expanded_state_count == max_expanded_states:
            return OracleResult("limit", None, expanded_state_count, None)
        expanded_state_count += 1
        for following_positions in _joint_successors(case, positions):
            following_completed_mask = _completed_mask(
                following_positions,
                goals,
                completed_mask,
            )
            following: SearchState = (
                following_positions,
                following_completed_mask,
            )
            following_distance = distance + 1
            if following_distance >= best_g.get(following, following_distance + 1):
                continue
            best_g[following] = following_distance
            came_from[following] = state
            heappush(
                open_heap,
                (
                    following_distance
                    + _heuristic(
                        following_positions,
                        goals,
                        following_completed_mask,
                    ),
                    following_distance,
                    following_positions,
                    following_completed_mask,
                ),
            )

    return OracleResult("unsolved", None, expanded_state_count, None)
