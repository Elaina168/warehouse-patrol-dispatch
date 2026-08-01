import type { Cell, Scenario, Shelf, Task, TaskType } from "./types";
import { cellKey } from "./view";

export const MAX_SCENARIO_AXIS_LENGTH = 64;
export const MAX_SCENARIO_CELL_COUNT = 1024;
export const MAX_SCENARIO_ROBOTS = 32;
export const MAX_SCENARIO_TASKS = 128;
export const MAX_TASK_TARGETS = 64;
export const MAX_TASK_SERVICE_TIME = 10_000;
export const MAX_SCENARIO_CHARGE_TIME = 10_000;
export const MAX_SESSION_CURRENT_TIME = 10_000;

type ImportedShelf = Omit<Shelf, "initialOccupied"> & {
  initialOccupied?: boolean;
};

type ImportedScenario = Omit<Scenario, "shelves"> & {
  shelves?: ImportedShelf[];
};

export function parseScenario(value: unknown): Scenario {
  if (!isScenario(value)) {
    throw new Error("JSON 必须是 Scenario 对象，并包含 id、name、description、width、height、obstacles、zones、robots、tasks、dynamic");
  }

  const scenario: Scenario = {
    ...value,
    shelves: (value.shelves ?? []).map((shelf) => ({
      ...shelf,
      initialOccupied: shelf.initialOccupied ?? false
    }))
  };
  assertScenarioCellsInside(scenario);
  const diagnostics = diagnoseScenario(scenario);
  if (diagnostics.length > 0) {
    throw new Error(diagnostics.join("；"));
  }
  return scenario;
}

function isScenario(value: unknown): value is ImportedScenario {
  if (!isRecord(value)
    || !isString(value.id)
    || !isString(value.name)
    || !isString(value.description)
    || !isIntegerInRange(value.width, 1, MAX_SCENARIO_AXIS_LENGTH)
    || !isIntegerInRange(value.height, 1, MAX_SCENARIO_AXIS_LENGTH)
    || value.width * value.height > MAX_SCENARIO_CELL_COUNT
    || !Array.isArray(value.obstacles)
    || !value.obstacles.every(isCell)
    || !isRecord(value.zones)
    || !Array.isArray(value.zones.warehouse)
    || !value.zones.warehouse.every(isCell)
    || !Array.isArray(value.zones.inspection)
    || !value.zones.inspection.every(isCell)
    || !Array.isArray(value.zones.delivery)
    || !value.zones.delivery.every(isCell)
    || !(value.zones.charging === undefined
      || (Array.isArray(value.zones.charging) && value.zones.charging.every(isCell)))
    || !(value.shelves === undefined
      || (Array.isArray(value.shelves) && value.shelves.every(isShelf)))
    || !Array.isArray(value.robots)
    || value.robots.length > MAX_SCENARIO_ROBOTS
    || !value.robots.every(isRobot)
    || !Array.isArray(value.tasks)
    || !value.tasks.every(isTask)
    || !isDynamicEvent(value.dynamic)
    || value.tasks.length + value.dynamic.tasks.length > MAX_SCENARIO_TASKS
    || !(value.chargeTime === undefined
      || isIntegerInRange(value.chargeTime, 1, MAX_SCENARIO_CHARGE_TIME))
  ) {
    return false;
  }

  const cellCount = value.width * value.height;
  return value.obstacles.length <= cellCount
    && (value.shelves?.length ?? 0) <= cellCount
    && value.zones.warehouse.length <= cellCount
    && value.zones.inspection.length <= cellCount
    && value.zones.delivery.length <= cellCount
    && (value.zones.charging?.length ?? 0) <= cellCount
    && value.dynamic.blockedCells.length <= cellCount;
}

function isShelf(value: unknown): value is ImportedShelf {
  return isRecord(value)
    && isString(value.id)
    && isCell(value.cell)
    && isCell(value.serviceCell)
    && (value.initialOccupied === undefined || typeof value.initialOccupied === "boolean");
}

function isRobot(value: unknown): value is Scenario["robots"][number] {
  if (!isRecord(value)
    || !isString(value.id)
    || !isString(value.name)
    || !isCell(value.start)
    || !isIntegerInRange(value.battery, 0, Number.POSITIVE_INFINITY)
    || !isIntegerInRange(value.load, 0, Number.POSITIVE_INFINITY)
    || !(value.moveTicks === undefined || isIntegerInRange(value.moveTicks, 1, 4))
    || !(value.capabilities === undefined || isCapabilityList(value.capabilities))
  ) {
    return false;
  }

  const batteryCapacity = value.batteryCapacity === undefined ? 100 : value.batteryCapacity;
  return isIntegerInRange(batteryCapacity, 1, Number.POSITIVE_INFINITY)
    && value.battery <= batteryCapacity;
}

function isTaskType(value: unknown): value is TaskType {
  return value === "inspection" || value === "delivery" || value === "emergency";
}

function isCapabilityList(value: unknown): value is TaskType[] {
  return Array.isArray(value)
    && value.length > 0
    && value.every(isTaskType)
    && new Set(value).size === value.length;
}

function isDynamicEvent(value: unknown): value is Scenario["dynamic"] {
  return isRecord(value)
    && isIntegerInRange(value.triggerTime, 0, MAX_SESSION_CURRENT_TIME)
    && Array.isArray(value.blockedCells)
    && value.blockedCells.length <= MAX_SCENARIO_CELL_COUNT
    && value.blockedCells.every(isCell)
    && Array.isArray(value.failedRobots)
    && value.failedRobots.length <= MAX_SCENARIO_ROBOTS
    && value.failedRobots.every(isString)
    && Array.isArray(value.tasks)
    && value.tasks.length <= MAX_SCENARIO_TASKS
    && value.tasks.every(isTask);
}

function isTask(value: unknown): value is Task {
  if (!isRecord(value)
    || !isString(value.id)
    || !isString(value.title)
    || !isIntegerInRange(value.priority, 0, 5)
    || !isNullableOptionalInteger(value.releaseTime, 0, MAX_SESSION_CURRENT_TIME)
    || !isNullableOptionalInteger(value.deadline, 0, Number.POSITIVE_INFINITY)
    || !isNullableOptionalInteger(value.serviceTime, 0, MAX_TASK_SERVICE_TIME)
    || !isNullableOptionalCell(value.pickup)
    || !isNullableOptionalCell(value.dropoff)
    || !isNullableOptionalCell(value.target)
    || !isNullableOptionalTargets(value.targets)
    || !isNullableOptionalInteger(value.demand, 1, Number.POSITIVE_INFINITY)
  ) {
    return false;
  }

  if (value.type === "inspection") {
    return Array.isArray(value.targets)
      && value.targets.length <= MAX_TASK_TARGETS
      && value.targets.every(isCell);
  }
  if (value.type === "delivery") {
    return isCell(value.pickup)
      && isCell(value.dropoff)
      && isIntegerInRange(value.demand, 1, Number.POSITIVE_INFINITY);
  }
  if (value.type === "emergency") {
    return isCell(value.target);
  }
  return false;
}

function isNullableOptionalInteger(
  value: unknown,
  minimum: number,
  maximum: number
): boolean {
  return value === undefined
    || value === null
    || isIntegerInRange(value, minimum, maximum);
}

function isNullableOptionalCell(value: unknown): boolean {
  return value === undefined || value === null || isCell(value);
}

function isNullableOptionalTargets(value: unknown): boolean {
  return value === undefined
    || value === null
    || (Array.isArray(value)
      && value.length <= MAX_TASK_TARGETS
      && value.every(isCell));
}

function isIntegerInRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === "number"
    && Number.isInteger(value)
    && value >= minimum
    && value <= maximum;
}

function assertScenarioCellsInside(scenario: Scenario): void {
  const cells = [
    ...scenario.obstacles,
    ...scenario.shelves.flatMap((shelf) => [shelf.cell, shelf.serviceCell]),
    ...scenario.zones.warehouse,
    ...scenario.zones.inspection,
    ...scenario.zones.delivery,
    ...(scenario.zones.charging ?? []),
    ...scenario.robots.map((robot) => robot.start),
    ...scenario.dynamic.blockedCells,
    ...scenario.tasks.flatMap(scenarioTaskWaypoints),
    ...scenario.dynamic.tasks.flatMap(scenarioTaskWaypoints)
  ];
  const outOfBounds = cells.find(
    (cell) => cell[0] < 0
      || cell[0] >= scenario.width
      || cell[1] < 0
      || cell[1] >= scenario.height
  );
  if (outOfBounds) {
    throw new Error(`坐标超出地图范围：(${outOfBounds[0]}, ${outOfBounds[1]})`);
  }
}

function diagnoseScenario(scenario: Scenario): string[] {
  const fixedBlocked = new Set(scenario.obstacles.map(cellKey));
  return [
    ...duplicateDiagnostics("机器人 ID", scenario.robots.map((robot) => robot.id)),
    ...duplicateRobotStartDiagnostics(scenario),
    ...duplicateDiagnostics("任务 ID", [...scenario.tasks, ...scenario.dynamic.tasks].map((task) => task.id)),
    ...duplicateDiagnostics(
      "障碍/封锁坐标",
      [...scenario.obstacles, ...scenario.dynamic.blockedCells].map(cellKey)
    ),
    ...blockedPointDiagnostics(scenario, fixedBlocked)
  ];
}

function duplicateDiagnostics(label: string, values: string[]): string[] {
  const seen = new Set<string>();
  const duplicates: string[] = [];
  for (const value of values) {
    if (seen.has(value) && !duplicates.includes(value)) duplicates.push(value);
    seen.add(value);
  }
  return duplicates.map((value) => `${label} 重复：${value}`);
}

function duplicateRobotStartDiagnostics(scenario: Scenario): string[] {
  const seen = new Set<string>();
  const duplicates: Cell[] = [];
  for (const robot of scenario.robots) {
    const key = cellKey(robot.start);
    if (seen.has(key) && !duplicates.some((cell) => cellKey(cell) === key)) {
      duplicates.push(robot.start);
    }
    seen.add(key);
  }
  return duplicates.map((cell) => `机器人起点重复：(${cell[0]}, ${cell[1]})`);
}

function blockedPointDiagnostics(scenario: Scenario, fixedBlocked: Set<string>): string[] {
  const points = [
    ...scenario.robots.map((robot) => ({ label: `机器人 ${robot.id} 起点`, cell: robot.start })),
    ...[...scenario.tasks, ...scenario.dynamic.tasks].flatMap((task) =>
      scenarioTaskWaypoints(task).map((cell, index) => ({
        label: `任务 ${task.id} 目标 ${index + 1}`,
        cell
      }))
    )
  ];
  return points
    .filter((point) => fixedBlocked.has(cellKey(point.cell)))
    .map((point) => `${point.label} 位于障碍或封锁单元：(${point.cell[0]}, ${point.cell[1]})`);
}

function scenarioTaskWaypoints(task: Task): Cell[] {
  if (task.type === "inspection") return task.targets;
  if (task.type === "delivery") return [task.pickup, task.dropoff];
  return [task.target];
}

function isCell(value: unknown): value is Cell {
  return Array.isArray(value)
    && value.length === 2
    && isIntegerInRange(value[0], 0, Number.POSITIVE_INFINITY)
    && isIntegerInRange(value[1], 0, Number.POSITIVE_INFINITY);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}
