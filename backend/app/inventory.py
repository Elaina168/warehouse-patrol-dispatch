from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.schemas import Scenario, ShelfRuntimeState, ShelfStatus, Task

ShelfTaskKind = Literal["inbound", "outbound"]


@dataclass(frozen=True)
class ShelfTaskBinding:
    kind: ShelfTaskKind
    shelf_id: str


class ShelfInventoryError(ValueError):
    pass


def initial_shelf_statuses(scenario: Scenario) -> dict[str, ShelfStatus]:
    return {
        shelf.id: "occupied" if shelf.initialOccupied else "empty"
        for shelf in scenario.shelves
    }


def classify_shelf_task(scenario: Scenario, task: Task) -> ShelfTaskBinding | None:
    if not scenario.shelves or task.type != "delivery":
        return None
    shelves_by_service = {tuple(shelf.serviceCell): shelf for shelf in scenario.shelves}
    pickup = tuple(task.pickup) if task.pickup is not None else None
    dropoff = tuple(task.dropoff) if task.dropoff is not None else None
    inbound_cells = {tuple(cell) for cell in scenario.zones.warehouse}
    outbound_cells = {tuple(cell) for cell in scenario.zones.delivery}
    if pickup in inbound_cells:
        shelf = shelves_by_service.get(dropoff)
        if shelf is None:
            raise ShelfInventoryError(f"进货任务的放货作业格没有对应货架：{task.id}")
        return ShelfTaskBinding("inbound", shelf.id)
    if dropoff in outbound_cells:
        shelf = shelves_by_service.get(pickup)
        if shelf is None:
            raise ShelfInventoryError(f"出货任务的取货作业格没有对应货架：{task.id}")
        return ShelfTaskBinding("outbound", shelf.id)
    raise ShelfInventoryError(f"取送任务不是合法的进货到货架或货架到出货组合：{task.id}")


def reserve_shelf_task(
    scenario: Scenario,
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task: Task,
) -> ShelfTaskBinding | None:
    binding = classify_shelf_task(scenario, task)
    if binding is None:
        return None
    status = statuses[binding.shelf_id]
    if binding.kind == "inbound" and status != "empty":
        reason = "已有货物" if status == "occupied" else "已被预订"
        raise ShelfInventoryError(f"入库货架{reason}：{binding.shelf_id}")
    if binding.kind == "outbound" and status != "occupied":
        reason = "为空" if status == "empty" else "已被预订"
        raise ShelfInventoryError(f"出库货架{reason}：{binding.shelf_id}")
    statuses[binding.shelf_id] = "inboundReserved" if binding.kind == "inbound" else "outboundReserved"
    bindings[task.id] = binding
    return binding


def complete_inbound_task(
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task_id: str,
) -> bool:
    binding = bindings.get(task_id)
    if binding is None or binding.kind != "inbound":
        return False
    statuses[binding.shelf_id] = "occupied"
    bindings.pop(task_id, None)
    return True


def complete_outbound_pickup(
    statuses: dict[str, ShelfStatus],
    bindings: dict[str, ShelfTaskBinding],
    task_id: str,
) -> bool:
    binding = bindings.get(task_id)
    if binding is None or binding.kind != "outbound":
        return False
    statuses[binding.shelf_id] = "empty"
    bindings.pop(task_id, None)
    return True


def build_shelf_runtime_states(
    scenario: Scenario,
    statuses: dict[str, ShelfStatus],
) -> list[ShelfRuntimeState]:
    return [
        ShelfRuntimeState(
            shelfId=shelf.id,
            cell=shelf.cell,
            serviceCell=shelf.serviceCell,
            status=statuses[shelf.id],
        )
        for shelf in scenario.shelves
    ]
