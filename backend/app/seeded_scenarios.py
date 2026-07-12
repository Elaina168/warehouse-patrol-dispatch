import random

from backend.app.schemas import Scenario


def seeded_pressure_scenario(label: str, seed: int, robot_count: int, task_count: int) -> Scenario:
    rng = random.Random(seed)
    width = 18
    height = 12
    robot_starts = [
        [0, 0],
        [17, 0],
        [0, 11],
        [17, 11],
        [0, 5],
        [17, 5],
        [8, 0],
        [8, 11],
    ][:robot_count]
    robots = [
        {
            "id": f"R{index + 1}",
            "name": f"R{index + 1}",
            "start": start,
            "battery": 90 - index,
            "load": 3 if index % 3 == 0 else 2,
        }
        for index, start in enumerate(robot_starts)
    ]
    obstacle_candidates = [
        [5, row] for row in range(1, 11) if row not in {3, 8}
    ] + [
        [12, row] for row in range(1, 11) if row not in {2, 9}
    ]
    obstacles = sorted(rng.sample(obstacle_candidates, k=min(6 + seed % 3, len(obstacle_candidates))))
    blocked = {tuple(cell) for cell in obstacles}
    occupied = {tuple(cell) for cell in robot_starts}
    all_cells = [
        [x, y]
        for y in range(1, height - 1)
        for x in range(1, width - 1)
        if (x, y) not in blocked and (x, y) not in occupied
    ]
    inspection_cells = rng.sample(all_cells, k=task_count + 3)
    pickup_cells = [[1, 1], [16, 1], [1, 10], [16, 10], [1, 5], [16, 5], [8, 1], [9, 10]]
    dropoff_cells = [[16, 10], [1, 10], [16, 1], [1, 1], [16, 5], [1, 5], [8, 10], [9, 1]]
    tasks = []
    for index in range(task_count):
        if index % 3 == 2:
            pair_index = index % len(pickup_cells)
            tasks.append(
                {
                    "id": f"D{index + 1}",
                    "type": "delivery",
                    "title": f"D{index + 1}",
                    "priority": 2 + index % 4,
                    "releaseTime": index % 7,
                    "deadline": 90 + index,
                    "pickup": pickup_cells[pair_index],
                    "dropoff": dropoff_cells[pair_index],
                    "demand": 1 + index % 2,
                }
            )
        else:
            tasks.append(
                {
                    "id": f"I{index + 1}",
                    "type": "inspection",
                    "title": f"I{index + 1}",
                    "priority": 1 + index % 5,
                    "releaseTime": index % 6,
                    "deadline": 80 + index,
                    "targets": [inspection_cells[index]],
                }
            )

    dynamic_tasks = [
        {
            "id": f"E{index + 1}",
            "type": "emergency",
            "title": f"E{index + 1}",
            "priority": 5,
            "target": inspection_cells[task_count + index],
        }
        for index in range(3)
    ]
    dynamic_blocked_candidates = [
        cell
        for cell in all_cells
        if cell not in inspection_cells[: task_count + 3] and cell not in pickup_cells + dropoff_cells
    ]
    dynamic_blocked = rng.sample(dynamic_blocked_candidates, k=2)

    return Scenario.model_validate(
        {
            "id": f"seeded-pressure-{label}",
            "name": f"seeded-pressure-{label}",
            "description": f"fixed seed pressure scenario {seed}",
            "width": width,
            "height": height,
            "obstacles": obstacles,
            "zones": {
                "warehouse": pickup_cells,
                "inspection": inspection_cells,
                "delivery": dropoff_cells,
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 8,
                "blockedCells": dynamic_blocked,
                "failedRobots": [],
                "tasks": dynamic_tasks,
            },
        }
    )
