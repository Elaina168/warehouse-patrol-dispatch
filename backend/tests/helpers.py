import json
from pathlib import Path

from backend.app.seeded_scenarios import seeded_pressure_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def frontend_demo_scenario(scenario_id: str) -> dict:
    scenarios_path = PROJECT_ROOT / "frontend" / "src" / "domain" / "scenarios.json"
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    return next(scenario for scenario in scenarios if scenario["id"] == scenario_id)


def scenario_payload() -> dict:
    return {
        "id": "test-map",
        "name": "测试场景",
        "description": "用于验证后端调度接口。",
        "width": 6,
        "height": 5,
        "obstacles": [[2, 1], [2, 2], [2, 3]],
        "zones": {
            "warehouse": [[0, 0]],
            "inspection": [[5, 0]],
            "delivery": [[5, 4]],
        },
        "robots": [
            {"id": "R1", "name": "机器人一号", "start": [0, 0], "battery": 90, "load": 2},
            {"id": "R2", "name": "机器人二号", "start": [0, 4], "battery": 80, "load": 2},
        ],
        "tasks": [
            {"id": "T1", "type": "inspection", "title": "顶部巡检", "priority": 2, "targets": [[5, 0]]},
            {
                "id": "T2",
                "type": "delivery",
                "title": "物资配送",
                "priority": 3,
                "pickup": [0, 0],
                "dropoff": [5, 4],
                "demand": 1,
            },
        ],
        "dynamic": {
            "triggerTime": 6,
            "blockedCells": [[3, 2]],
            "failedRobots": [],
            "tasks": [
                {"id": "E1", "type": "emergency", "title": "突发巡查", "priority": 5, "target": [4, 4]}
            ],
        },
    }

