from dataclasses import dataclass, field
from typing import Literal


TimedAStarOutcome = Literal[
    "success",
    "invalidEndpoint",
    "goalFullyReserved",
    "exhausted",
]


@dataclass(slots=True)
class TimedAStarCallDiagnostics:
    outcome: TimedAStarOutcome | None = None
    expanded_state_count: int = 0

    def finish(
        self,
        outcome: TimedAStarOutcome,
        expanded_state_count: int,
    ) -> None:
        self.outcome = outcome
        self.expanded_state_count = expanded_state_count


@dataclass(slots=True)
class PathCandidateDiagnostics:
    robot_order: list[str]
    timed_astar_calls: list[TimedAStarCallDiagnostics] = field(default_factory=list)
    failure_count: int | None = None
    conflict_count: int | None = None
    deadline_miss_count: int | None = None

    def finish(
        self,
        failure_count: int,
        conflict_count: int,
        deadline_miss_count: int,
    ) -> None:
        self.failure_count = failure_count
        self.conflict_count = conflict_count
        self.deadline_miss_count = deadline_miss_count

    def start_timed_astar_call(self) -> TimedAStarCallDiagnostics:
        diagnostics = TimedAStarCallDiagnostics()
        self.timed_astar_calls.append(diagnostics)
        return diagnostics

    @property
    def timed_astar_call_count(self) -> int:
        return len(self.timed_astar_calls)

    @property
    def timed_astar_expanded_state_count(self) -> int:
        return sum(item.expanded_state_count for item in self.timed_astar_calls)

    @property
    def max_timed_astar_expanded_state_count(self) -> int:
        return max(
            (item.expanded_state_count for item in self.timed_astar_calls),
            default=0,
        )

    @property
    def timed_astar_exhausted_search_count(self) -> int:
        return sum(item.outcome == "exhausted" for item in self.timed_astar_calls)

    @property
    def timed_astar_goal_fully_reserved_reject_count(self) -> int:
        return sum(
            item.outcome == "goalFullyReserved"
            for item in self.timed_astar_calls
        )


@dataclass(slots=True)
class PlanningDiagnostics:
    path_candidates: list[PathCandidateDiagnostics] = field(default_factory=list)
    selected_path_candidate_index: int | None = None

    def start_path_candidate(
        self,
        robot_order: list[str],
    ) -> PathCandidateDiagnostics:
        candidate = PathCandidateDiagnostics(robot_order=list(robot_order))
        self.path_candidates.append(candidate)
        return candidate

    @property
    def path_candidate_count(self) -> int:
        return len(self.path_candidates)

    @property
    def failed_path_candidate_count(self) -> int:
        return sum(
            (item.failure_count or 0) > 0
            for item in self.path_candidates
        )

    @property
    def timed_astar_call_count(self) -> int:
        return sum(item.timed_astar_call_count for item in self.path_candidates)

    @property
    def timed_astar_expanded_state_count(self) -> int:
        return sum(
            item.timed_astar_expanded_state_count
            for item in self.path_candidates
        )

    @property
    def max_timed_astar_expanded_state_count(self) -> int:
        return max(
            (
                item.max_timed_astar_expanded_state_count
                for item in self.path_candidates
            ),
            default=0,
        )

    @property
    def timed_astar_exhausted_search_count(self) -> int:
        return sum(
            item.timed_astar_exhausted_search_count
            for item in self.path_candidates
        )

    @property
    def timed_astar_goal_fully_reserved_reject_count(self) -> int:
        return sum(
            item.timed_astar_goal_fully_reserved_reject_count
            for item in self.path_candidates
        )
