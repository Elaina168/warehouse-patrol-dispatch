from collections import deque
from dataclasses import dataclass
import random
from typing import Literal

from backend.app.schemas import Cell


SolvabilityCaseSource = Literal["catalog", "generated"]
ExpectedOracleOutcome = Literal["solved", "unsolved"]

MAX_SOLVABILITY_AXIS = 5
MAX_SOLVABILITY_CELLS = 25
MAX_SOLVABILITY_ROBOTS = 4
MAX_GENERATION_ATTEMPTS_PER_CASE = 1_000


@dataclass(frozen=True, slots=True)
class SolvabilityAgent:
    agent_id: str
    start: Cell
    goal: Cell


@dataclass(frozen=True, slots=True)
class SolvabilityCase:
    case_id: str
    source: SolvabilityCaseSource
    width: int
    height: int
    obstacles: tuple[Cell, ...]
    agents: tuple[SolvabilityAgent, ...]
    expected_oracle_outcome: ExpectedOracleOutcome | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be blank")
        if self.source not in {"catalog", "generated"}:
            raise ValueError(f"unsupported source: {self.source}")
        if not 1 <= self.width <= MAX_SOLVABILITY_AXIS:
            raise ValueError(f"width must be within 1..{MAX_SOLVABILITY_AXIS}")
        if not 1 <= self.height <= MAX_SOLVABILITY_AXIS:
            raise ValueError(f"height must be within 1..{MAX_SOLVABILITY_AXIS}")
        if self.width * self.height > MAX_SOLVABILITY_CELLS:
            raise ValueError(f"cell count must be <= {MAX_SOLVABILITY_CELLS}")
        if not 1 <= len(self.agents) <= MAX_SOLVABILITY_ROBOTS:
            raise ValueError(
                f"agent count must be within 1..{MAX_SOLVABILITY_ROBOTS}"
            )
        if len(set(self.obstacles)) != len(self.obstacles):
            raise ValueError("obstacles must be unique")

        def require_inside(cell: Cell, label: str) -> None:
            x, y = cell
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError(f"{label} must be inside the map: {cell}")

        for obstacle in self.obstacles:
            require_inside(obstacle, "obstacle")
        ids = [agent.agent_id for agent in self.agents]
        starts = [agent.start for agent in self.agents]
        goals = [agent.goal for agent in self.agents]
        if any(not agent_id.strip() for agent_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("agent ids must be non-blank and unique")
        if len(set(starts)) != len(starts):
            raise ValueError("agent starts must be unique")
        if len(set(goals)) != len(goals):
            raise ValueError("agent goals must be unique")
        blocked = set(self.obstacles)
        for agent in self.agents:
            require_inside(agent.start, f"{agent.agent_id} start")
            require_inside(agent.goal, f"{agent.agent_id} goal")
            if agent.start in blocked or agent.goal in blocked:
                raise ValueError("obstacles must not overlap starts or goals")

    @property
    def case_key(self) -> tuple[object, ...]:
        return (
            self.width,
            self.height,
            tuple(sorted(self.obstacles)),
            tuple((agent.start, agent.goal) for agent in self.agents),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "obstacles": [list(cell) for cell in self.obstacles],
            "agents": [
                {
                    "agentId": agent.agent_id,
                    "start": list(agent.start),
                    "goal": list(agent.goal),
                }
                for agent in self.agents
            ],
            "expectedOracleOutcome": self.expected_oracle_outcome,
        }


def solvability_catalog() -> tuple[SolvabilityCase, ...]:
    return (
        SolvabilityCase(
            "catalog-solo-straight",
            "catalog",
            3,
            1,
            (),
            (SolvabilityAgent("R1", (0, 0), (2, 0)),),
            "solved",
        ),
        SolvabilityCase(
            "catalog-independent-r2",
            "catalog",
            3,
            2,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (0, 1), (2, 1)),
            ),
            "solved",
        ),
        SolvabilityCase(
            "catalog-side-bypass-swap-r2",
            "catalog",
            3,
            2,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (2, 0), (0, 0)),
            ),
            "solved",
        ),
        SolvabilityCase(
            "catalog-no-bypass-swap-r2",
            "catalog",
            3,
            1,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (2, 0), (0, 0)),
            ),
            "unsolved",
        ),
    )


def _statically_reachable(
    width: int,
    height: int,
    blocked: set[Cell],
    start: Cell,
    goal: Cell,
) -> bool:
    queue = deque([start])
    visited = {start}
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            following = (x + dx, y + dy)
            if (
                0 <= following[0] < width
                and 0 <= following[1] < height
                and following not in blocked
                and following not in visited
            ):
                visited.add(following)
                queue.append(following)
    return False


_GENERATION_PROFILES = ((3, 3, 2), (4, 3, 2), (4, 4, 3))


def generate_solvability_cases(
    seed: int,
    sample_count: int,
) -> tuple[SolvabilityCase, ...]:
    if not 0 <= sample_count <= 512:
        raise ValueError("sample_count must be within 0..512")
    rng = random.Random(seed)
    generated: list[SolvabilityCase] = []
    seen: set[tuple[object, ...]] = set()
    total_attempts = sample_count * MAX_GENERATION_ATTEMPTS_PER_CASE
    attempts = 0
    while len(generated) < sample_count and attempts < total_attempts:
        index = len(generated)
        width, height, robot_count = _GENERATION_PROFILES[
            index % len(_GENERATION_PROFILES)
        ]
        cells = [(x, y) for y in range(height) for x in range(width)]
        obstacle_count = rng.randint(0, min(3, len(cells) - robot_count))
        obstacles = tuple(sorted(rng.sample(cells, obstacle_count)))
        blocked = set(obstacles)
        traversable = [cell for cell in cells if cell not in blocked]
        starts = rng.sample(traversable, robot_count)
        goals = rng.sample(traversable, robot_count)
        attempts += 1
        if any(start == goal for start, goal in zip(starts, goals, strict=True)):
            continue
        if not all(
            _statically_reachable(width, height, blocked, start, goal)
            for start, goal in zip(starts, goals, strict=True)
        ):
            continue
        case = SolvabilityCase(
            case_id=f"generated-s{seed}-i{index + 1:04d}",
            source="generated",
            width=width,
            height=height,
            obstacles=obstacles,
            agents=tuple(
                SolvabilityAgent(f"R{agent_index + 1}", start, goal)
                for agent_index, (start, goal) in enumerate(
                    zip(starts, goals, strict=True)
                )
            ),
        )
        if case.case_key in seen:
            continue
        seen.add(case.case_key)
        generated.append(case)
    if len(generated) != sample_count:
        raise RuntimeError(
            f"unable to generate {sample_count} unique cases after {attempts} attempts"
        )
    return tuple(generated)


def solvability_cases(seed: int, sample_count: int) -> tuple[SolvabilityCase, ...]:
    return solvability_catalog() + generate_solvability_cases(seed, sample_count)
