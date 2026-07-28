import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.inventory import (
    ShelfInventoryError,
    build_shelf_runtime_states,
    classify_shelf_task,
    complete_inbound_task,
    complete_outbound_pickup,
    initial_shelf_statuses,
    reserve_shelf_task,
)
from backend.app.schemas import Scenario, Task


def inventory_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "inventory",
            "name": "inventory",
            "description": "inventory",
            "width": 7,
            "height": 5,
            "obstacles": [[2, 2], [4, 2]],
            "zones": {
                "warehouse": [[0, 0]],
                "inspection": [[3, 0]],
                "delivery": [[6, 4]],
                "charging": [],
            },
            "shelves": [
                {"id": "S01", "cell": [2, 2], "serviceCell": [2, 1], "initialOccupied": False},
                {"id": "S02", "cell": [4, 2], "serviceCell": [4, 1], "initialOccupied": True},
            ],
            "robots": [{"id": "R1", "name": "R1", "start": [0, 4], "battery": 100, "load": 1}],
            "tasks": [],
            "dynamic": {"triggerTime": 10, "blockedCells": [], "failedRobots": [], "tasks": []},
        }
    )


def inbound_task(task_id: str = "IN") -> Task:
    return Task.model_validate(
        {
            "id": task_id,
            "type": "delivery",
            "title": task_id,
            "priority": 2,
            "pickup": [0, 0],
            "dropoff": [2, 1],
            "demand": 1,
        }
    )


def outbound_task(task_id: str = "OUT") -> Task:
    return Task.model_validate(
        {
            "id": task_id,
            "type": "delivery",
            "title": task_id,
            "priority": 2,
            "pickup": [4, 1],
            "dropoff": [6, 4],
            "demand": 1,
        }
    )


def test_inventory_classifies_reserves_and_completes_shelf_tasks() -> None:
    scenario = inventory_scenario()
    statuses = initial_shelf_statuses(scenario)
    bindings = {}

    assert statuses == {"S01": "empty", "S02": "occupied"}
    assert classify_shelf_task(scenario, inbound_task()).kind == "inbound"
    assert classify_shelf_task(scenario, outbound_task()).kind == "outbound"

    reserve_shelf_task(scenario, statuses, bindings, inbound_task())
    reserve_shelf_task(scenario, statuses, bindings, outbound_task())
    assert statuses == {"S01": "inboundReserved", "S02": "outboundReserved"}

    complete_outbound_pickup(statuses, bindings, "OUT")
    complete_inbound_task(statuses, bindings, "IN")
    assert statuses == {"S01": "occupied", "S02": "empty"}

    runtime = build_shelf_runtime_states(scenario, statuses)
    assert [(item.shelfId, item.status) for item in runtime] == [("S01", "occupied"), ("S02", "empty")]


def test_inventory_rejects_wrong_direction_and_duplicate_reservation() -> None:
    scenario = inventory_scenario()
    statuses = initial_shelf_statuses(scenario)
    bindings = {}
    reserve_shelf_task(scenario, statuses, bindings, inbound_task())

    with pytest.raises(ShelfInventoryError, match="入库货架已被预订"):
        reserve_shelf_task(scenario, statuses, bindings, inbound_task("IN-2"))

    invalid = outbound_task("INVALID").model_copy(update={"pickup": [2, 1]})
    with pytest.raises(ShelfInventoryError, match="出库货架已被预订：S01"):
        reserve_shelf_task(scenario, statuses, bindings, invalid)

    fresh_statuses = initial_shelf_statuses(scenario)
    fresh_bindings = {}
    with pytest.raises(ShelfInventoryError, match="出库货架为空"):
        reserve_shelf_task(scenario, fresh_statuses, fresh_bindings, invalid)


def test_inventory_does_not_restrict_delivery_when_shelves_are_absent() -> None:
    scenario = inventory_scenario().model_copy(update={"shelves": []})
    statuses = initial_shelf_statuses(scenario)
    bindings = {}

    assert reserve_shelf_task(scenario, statuses, bindings, inbound_task()) is None
    assert statuses == {}
    assert bindings == {}


def t0_inventory_session_scenario(*, outbound: bool) -> dict:
    task_id = "OUT" if outbound else "IN"
    return {
        "id": f"t0-inventory-{'outbound' if outbound else 'inbound'}",
        "name": "t0 inventory",
        "description": "T=0 库存完成转换",
        "width": 2,
        "height": 1,
        "obstacles": [[1, 0]],
        "zones": {
            "warehouse": [] if outbound else [[0, 0]],
            "inspection": [],
            "delivery": [[0, 0]] if outbound else [],
            "charging": [],
        },
        "shelves": [
            {
                "id": "S01",
                "cell": [1, 0],
                "serviceCell": [0, 0],
                "initialOccupied": outbound,
            }
        ],
        "robots": [
            {
                "id": "R1",
                "name": "R1",
                "start": [0, 0],
                "battery": 100,
                "load": 1,
            }
        ],
        "tasks": [
            {
                "id": task_id,
                "type": "delivery",
                "title": task_id,
                "priority": 2,
                "pickup": [0, 0],
                "dropoff": [0, 0],
                "demand": 1,
            }
        ],
        "dynamic": {
            "triggerTime": 10,
            "blockedCells": [],
            "failedRobots": [],
            "tasks": [],
        },
    }


def test_t0_inventory_inbound_completion_is_applied_once() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": t0_inventory_session_scenario(outbound=False),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert created.status_code == 200
    payload = created.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "IN")
    shelf_state = next(state for state in payload["shelfStates"] if state["shelfId"] == "S01")
    assert task_state["status"] == "completed"
    assert shelf_state["status"] == "occupied"
    created_events = [
        event
        for event in payload["result"]["eventLog"]
        if event["text"] in {"任务 IN 已完成", "货架 S01 已放货"}
    ]
    assert created_events == [
        {"time": 0, "text": "任务 IN 已完成"},
        {"time": 0, "text": "货架 S01 已放货"},
    ]

    ticked = client.post(
        f"/api/sessions/{payload['sessionId']}/tick",
        json={"currentTime": 1},
    )

    assert ticked.status_code == 200
    relevant_events = [
        event
        for event in ticked.json()["result"]["eventLog"]
        if event["text"] in {"任务 IN 已完成", "货架 S01 已放货"}
    ]
    assert relevant_events == [
        {"time": 0, "text": "任务 IN 已完成"},
        {"time": 0, "text": "货架 S01 已放货"},
    ]


def test_t0_inventory_outbound_completion_is_applied_once() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={
            "scenario": t0_inventory_session_scenario(outbound=True),
            "options": {"avoidConflicts": True, "includeDynamic": False},
        },
    )

    assert created.status_code == 200
    payload = created.json()
    task_state = next(state for state in payload["taskStates"] if state["taskId"] == "OUT")
    shelf_state = next(state for state in payload["shelfStates"] if state["shelfId"] == "S01")
    assert task_state["status"] == "completed"
    assert shelf_state["status"] == "empty"
    created_events = [
        event
        for event in payload["result"]["eventLog"]
        if event["text"] in {"货架 S01 已取货", "任务 OUT 已完成"}
    ]
    assert created_events == [
        {"time": 0, "text": "货架 S01 已取货"},
        {"time": 0, "text": "任务 OUT 已完成"},
    ]

    ticked = client.post(
        f"/api/sessions/{payload['sessionId']}/tick",
        json={"currentTime": 1},
    )

    assert ticked.status_code == 200
    relevant_events = [
        event
        for event in ticked.json()["result"]["eventLog"]
        if event["text"] in {"货架 S01 已取货", "任务 OUT 已完成"}
    ]
    assert relevant_events == [
        {"time": 0, "text": "货架 S01 已取货"},
        {"time": 0, "text": "任务 OUT 已完成"},
    ]
