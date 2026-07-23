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
