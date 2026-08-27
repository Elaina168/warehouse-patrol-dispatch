import type {
  Cell,
  Robot,
  RobotRuntimeState,
  Scenario,
  TaskType
} from "./types";

export type RuntimeRobotForm = {
  id: string;
  name: string;
  start: string;
  battery: string;
  batteryCapacity: string;
  load: string;
  moveTicks: string;
  capabilities: readonly TaskType[];
};

const allTaskTypes: TaskType[] = ["inspection", "delivery", "emergency"];
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;

export function createRuntimeRobotForm(scenario: Scenario): RuntimeRobotForm {
  const cell = firstFreeRobotCell(scenario);
  return {
    id: nextRobotId(scenario.robots),
    name: "运行时机器人",
    start: formatCell(cell),
    battery: "100",
    batteryCapacity: "100",
    load: "0",
    moveTicks: "1",
    capabilities: ["inspection"]
  };
}

export function validateRuntimeRobotForm(
  form: RuntimeRobotForm,
  scenario: Pick<Scenario, "width" | "height">
): string[] {
  const errors: string[] = [];
  if (!form.id.trim()) errors.push("请输入机器人 ID。");
  if (!form.name.trim()) errors.push("请输入机器人名称。");
  if (parseRuntimeRobotCell(form.start, scenario) === null) {
    errors.push("请输入地图范围内的整数接入坐标。");
  }
  if (form.capabilities.length === 0) errors.push("至少选择一种机器人能力。");
  if (parseRuntimeRobotNumber(form.battery) === null) errors.push("请输入有效的机器人电量。");
  if (parseRuntimeRobotNumber(form.batteryCapacity) === null) errors.push("请输入有效的电池上限。");
  if (parseRuntimeRobotNumber(form.load) === null) errors.push("请输入有效的载重。");
  if (parseRuntimeRobotNumber(form.moveTicks) === null) errors.push("请输入有效的每格移动 tick。");
  return errors;
}

export function buildRuntimeRobot(
  form: RuntimeRobotForm,
  scenario: Pick<Scenario, "width" | "height">
): Robot | null {
  if (validateRuntimeRobotForm(form, scenario).length > 0) return null;
  const start = parseRuntimeRobotCell(form.start, scenario);
  const batteryCapacity = clampRuntimeRobotInteger(form.batteryCapacity, 1, MAX_SAFE_INTEGER);
  const battery = Math.min(
    clampRuntimeRobotInteger(form.battery, 0, MAX_SAFE_INTEGER),
    batteryCapacity
  );
  return {
    id: form.id.trim(),
    name: form.name.trim(),
    start: start as Cell,
    battery,
    batteryCapacity,
    load: clampRuntimeRobotInteger(form.load, 0, MAX_SAFE_INTEGER),
    moveTicks: clampRuntimeRobotInteger(form.moveTicks, 1, 4),
    capabilities: [...form.capabilities]
  };
}

export function applyMapPickToRuntimeRobot(
  form: RuntimeRobotForm,
  cell: Cell
): RuntimeRobotForm {
  return { ...form, start: formatCell(cell) };
}

export function mergeRuntimeRobotStates(
  scenario: Scenario,
  robotStates: RobotRuntimeState[]
): Scenario {
  if (robotStates.length === 0) return scenario;
  const originalRobots = new Map(scenario.robots.map((robot) => [robot.id, robot]));
  return {
    ...scenario,
    robots: robotStates.map((state) => {
      const original = originalRobots.get(state.robotId);
      return {
        id: state.robotId,
        name: state.name,
        start: state.start ?? original?.start ?? state.position,
        battery: state.battery,
        batteryCapacity: state.batteryCapacity ?? original?.batteryCapacity ?? 100,
        load: state.load,
        moveTicks: state.moveTicks,
        capabilities: state.capabilities ?? original?.capabilities ?? [...allTaskTypes]
      };
    })
  };
}

export function getVisibleRobotCells(
  paths: Record<string, Cell[]>,
  pathStartTimes: Record<string, number> | undefined,
  displayTime: number,
  robotStates: RobotRuntimeState[] = []
): Map<string, Cell> {
  const cells = new Map<string, Cell>();
  const stateByRobotId = new Map(robotStates.map((state) => [state.robotId, state]));
  for (const [robotId, path] of Object.entries(paths)) {
    if (path.length === 0) continue;
    const startTime = pathStartTimes?.[robotId] ?? 0;
    const state = stateByRobotId.get(robotId);
    const joinedAt = state?.joinedAt ?? startTime;
    const removedAt = state?.removedAt ?? null;
    if (
      displayTime < startTime
      || displayTime < joinedAt
      || (removedAt !== null && removedAt !== undefined && displayTime >= removedAt)
    ) continue;
    const cell = path[Math.min(displayTime - startTime, path.length - 1)];
    if (cell) cells.set(robotId, cell);
  }
  return cells;
}

export function runtimeRobotPathCell(
  path: Cell[],
  displayTime: number,
  pathStartTime = 0
): Cell | null {
  if (path.length === 0 || displayTime < pathStartTime) return null;
  return path[Math.min(displayTime - pathStartTime, path.length - 1)] ?? null;
}

function parseRuntimeRobotCell(
  value: string,
  scenario: Pick<Scenario, "width" | "height">
): Cell | null {
  const match = /^\s*(\d+)\s*,\s*(\d+)\s*$/.exec(value);
  if (!match) return null;
  const cell: Cell = [Number(match[1]), Number(match[2])];
  if (!Number.isSafeInteger(cell[0]) || !Number.isSafeInteger(cell[1])) return null;
  if (cell[0] >= scenario.width || cell[1] >= scenario.height) return null;
  return cell;
}

function parseRuntimeRobotNumber(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clampRuntimeRobotInteger(value: string, minimum: number, maximum: number): number {
  const parsed = parseRuntimeRobotNumber(value) ?? minimum;
  return Math.min(maximum, Math.max(minimum, Math.trunc(parsed)));
}

function firstFreeRobotCell(scenario: Scenario): Cell {
  const blocked = new Set([
    ...scenario.obstacles,
    ...scenario.shelves.map((shelf) => shelf.cell),
    ...scenario.dynamic.blockedCells
  ].map(formatCell));
  const occupied = new Set(scenario.robots.map((robot) => formatCell(robot.start)));
  const preferred = [
    ...(scenario.zones.charging ?? []),
    ...Array.from({ length: scenario.height }, (_, y) =>
      Array.from({ length: scenario.width }, (_, x) => [x, y] as Cell)
    ).flat()
  ];
  return preferred.find((cell) => !blocked.has(formatCell(cell)) && !occupied.has(formatCell(cell)))
    ?? [0, 0];
}

function nextRobotId(robots: Robot[]): string {
  const used = new Set(robots.map((robot) => robot.id));
  let index = robots.length + 1;
  while (used.has(`R${index}`)) index += 1;
  return `R${index}`;
}

function formatCell(cell: Cell): string {
  return `${cell[0]}, ${cell[1]}`;
}
