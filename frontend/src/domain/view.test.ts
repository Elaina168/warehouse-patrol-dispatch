import { describe, expect, it } from "vitest";

import scenariosData from "./scenarios.json";
import { fixedDemoScenarioIds, scenarios } from "./scenarios";
import { buildZoneCellPresentations, cellKey, getRobotStateAt } from "./view";
import type { Cell, Scenario, Task } from "./types";
import { parseScenario } from "../main";

function taskWaypoints(task: Task): Cell[] {
  if (task.type === "inspection") return task.targets;
  if (task.type === "delivery") return [task.pickup, task.dropoff];
  return [task.target];
}

function expectInsideScenario(scenario: Scenario, cell: Cell): void {
  expect(cell[0]).toBeGreaterThanOrEqual(0);
  expect(cell[0]).toBeLessThan(scenario.width);
  expect(cell[1]).toBeGreaterThanOrEqual(0);
  expect(cell[1]).toBeLessThan(scenario.height);
}

function expectedShelfCells(): Cell[] {
  const xGroups = [[2, 3, 4], [7, 8, 9], [12, 13, 14], [17, 18, 19]];
  const yGroups = [[3, 4], [7, 8], [11, 12]];
  return yGroups.flatMap((ys) => ys.flatMap((y) => xGroups.flatMap((xs) => xs.map((x) => [x, y] as Cell))));
}

function expectedShelves(): Scenario["shelves"] {
  const xs = [2, 3, 4, 7, 8, 9, 12, 13, 14, 17, 18, 19];
  const rows = [
    { y: 3, serviceY: 2 },
    { y: 4, serviceY: 5 },
    { y: 7, serviceY: 6 },
    { y: 8, serviceY: 9 },
    { y: 11, serviceY: 10 },
    { y: 12, serviceY: 13 }
  ];
  const occupied = new Set(["3,3", "8,3", "13,3", "18,3", "3,7", "8,7", "13,7", "18,7", "3,11", "8,11", "13,11", "18,11"]);
  return rows.flatMap(({ y, serviceY }, rowIndex) => xs.map((x, columnIndex) => ({
    id: `S${String(rowIndex * xs.length + columnIndex + 1).padStart(2, "0")}`,
    cell: [x, y] as Cell,
    serviceCell: [x, serviceY] as Cell,
    initialOccupied: occupied.has(`${x},${y}`)
  })));
}

function cellSet(cells: Cell[]): Set<string> {
  return new Set(cells.map(cellKey));
}

const HORIZONTAL_INSPECTION_TARGETS: Cell[] = [
  [0, 1], [24, 1], [24, 2], [0, 2], [0, 5], [24, 5], [24, 6], [0, 6],
  [0, 9], [24, 9], [24, 10], [0, 10], [0, 13], [24, 13], [24, 14], [0, 14]
];

const VERTICAL_INSPECTION_TARGETS: Cell[] = [
  [0, 1], [0, 14], [1, 14], [1, 1], [5, 1], [5, 14], [6, 14], [6, 1],
  [10, 1], [10, 14], [11, 14], [11, 1], [15, 1], [15, 14], [16, 14], [16, 1],
  [20, 1], [20, 14], [21, 14], [21, 1], [22, 1], [22, 14], [23, 14], [23, 1],
  [24, 1], [24, 14]
];

function reachableCellKeys(scenario: Scenario, start: Cell): Set<string> {
  const obstacles = cellSet(scenario.obstacles);
  const visited = new Set<string>([cellKey(start)]);
  const queue: Cell[] = [start];
  for (let index = 0; index < queue.length; index += 1) {
    const [x, y] = queue[index];
    const neighbours: Cell[] = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
    for (const neighbour of neighbours) {
      const key = cellKey(neighbour);
      if (
        neighbour[0] < 0 || neighbour[0] >= scenario.width
        || neighbour[1] < 0 || neighbour[1] >= scenario.height
        || obstacles.has(key) || visited.has(key)
      ) continue;
      visited.add(key);
      queue.push(neighbour);
    }
  }
  return visited;
}

describe("domain view helpers", () => {
  it("uses stable cell keys", () => {
    expect(cellKey([3, 7])).toBe("3,7");
  });

  it("reads robot position from path and clamps progress", () => {
    const robot = { id: "R1", name: "R1", start: [0, 0] as Cell, battery: 90, load: 1 };
    const state = getRobotStateAt(robot, [[0, 0], [1, 0], [2, 0]], 9, []);

    expect(state.position).toEqual([2, 0]);
    expect(state.progress).toBe(1);
  });

  it("builds single-cell zone classes and labels with stable overlap priority", () => {
    const presentations = buildZoneCellPresentations({
      warehouse: [[2, 0]],
      inspection: [[3, 1], [4, 1]],
      delivery: [[5, 15]],
      charging: [[4, 1], [25, 3]]
    });

    expect(presentations.get("2,0")).toEqual({ classNames: ["warehouse-cell"], label: "进" });
    expect(presentations.get("3,1")).toEqual({ classNames: ["inspection-cell"], label: "巡" });
    expect(presentations.get("5,15")).toEqual({ classNames: ["delivery-cell"], label: "出" });
    expect(presentations.get("25,3")).toEqual({ classNames: ["charging-cell"], label: "充" });
    expect(presentations.get("4,1")).toEqual({
      classNames: ["charging-cell", "inspection-cell"],
      label: "充"
    });
  });
});

describe("scenario data", () => {
  it("normalizes legacy imports without shelves", () => {
    const legacy = structuredClone(scenarios[0]) as Omit<Scenario, "shelves"> & Partial<Pick<Scenario, "shelves">>;
    delete legacy.shelves;
    expect(parseScenario(legacy).shelves).toEqual([]);
  });

  it("rejects invalid explicit shelf fields", () => {
    const invalidShape = structuredClone(scenarios[0]) as unknown as { shelves: unknown };
    invalidShape.shelves = [{ id: "S01", cell: [2, 3], serviceCell: [2, 2], initialOccupied: "yes" }];
    expect(() => parseScenario(invalidShape)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects shelf coordinates outside the map", () => {
    const outOfBounds = structuredClone(scenarios[0]);
    outOfBounds.shelves[0].serviceCell = [outOfBounds.width, 2];
    expect(() => parseScenario(outOfBounds)).toThrow("坐标超出地图范围：(26, 2)");
  });

  it("exposes one integrated demo scenario for the main simulation", () => {
    expect(fixedDemoScenarioIds).toEqual(["integrated-demo"]);
    expect(scenarios.map((scenario) => scenario.id)).toEqual(fixedDemoScenarioIds);
    expect(scenariosData.map((scenario) => scenario.id)).toEqual(["integrated-demo"]);

    const byId = new Map(scenarios.map((scenario) => [scenario.id, scenario]));
    const integrated = byId.get("integrated-demo");
    expect(integrated).toBeDefined();

    expect(integrated?.robots).toHaveLength(4);
    for (const robot of integrated?.robots ?? []) {
      expect(robot.capabilities).toEqual(["inspection", "delivery", "emergency"]);
    }
    expect(integrated?.tasks.some((task) => task.type === "inspection")).toBe(true);
    expect(integrated?.tasks.filter((task) => task.type === "delivery").length).toBeGreaterThanOrEqual(2);
    const emergencyTask = integrated?.tasks.find((task) => task.id === "E1");
    expect(emergencyTask?.type).toBe("emergency");
    expect(emergencyTask?.releaseTime).toBe(12);
    expect(integrated?.dynamic.tasks).toEqual([]);
    expect(integrated?.dynamic.blockedCells).toEqual([]);
    expect(integrated?.dynamic.failedRobots).toEqual([]);
    expect(integrated?.description).toContain("避碰");
    expect(integrated?.description).toContain("在线任务");
  });

  it("protects the realistic warehouse grid and two-cell spacing", () => {
    const integrated = scenarios[0];
    expect(integrated.id).toBe("integrated-demo");
    expect([integrated.width, integrated.height]).toEqual([26, 16]);
    expect(cellSet(integrated.obstacles)).toEqual(cellSet(expectedShelfCells()));
    expect(integrated.obstacles).toHaveLength(72);
    expect(integrated.shelves).toEqual(expectedShelves());
    expect(integrated.shelves).toHaveLength(72);
    expect(integrated.shelves.filter((shelf) => shelf.initialOccupied)).toHaveLength(12);
    expect(new Set(integrated.shelves.map((shelf) => cellKey(shelf.serviceCell))).size).toBe(72);

    expect(integrated.zones.warehouse).toEqual([[2, 0], [5, 0], [8, 0], [11, 0], [14, 0], [17, 0]]);
    expect(integrated.zones.delivery).toEqual([[2, 15], [5, 15], [8, 15], [11, 15], [14, 15], [17, 15]]);
    expect(integrated.zones.charging).toEqual([[25, 3], [25, 6], [25, 9], [25, 12]]);
    expect(cellSet(integrated.zones.inspection)).toEqual(
      cellSet([...HORIZONTAL_INSPECTION_TARGETS, ...VERTICAL_INSPECTION_TARGETS])
    );
    expect(integrated.zones.inspection).toHaveLength(38);
    expect(integrated.robots.map((robot) => robot.start)).toEqual([[22, 3], [22, 6], [22, 9], [22, 12]]);

    const obstacleKeys = cellSet(integrated.obstacles);
    const specialCells = [
      ...integrated.zones.warehouse,
      ...integrated.zones.inspection,
      ...integrated.zones.delivery,
      ...(integrated.zones.charging ?? []),
      ...integrated.robots.map((robot) => robot.start)
    ];
    expect(specialCells.every((cell) => !obstacleKeys.has(cellKey(cell)))).toBe(true);

    const reachable = reachableCellKeys(integrated, [0, 0]);
    expect(reachable.size).toBe(integrated.width * integrated.height - integrated.obstacles.length);
  });

  it("protects the six default tasks and calibrated warehouse timing", () => {
    const integrated = scenarios[0];
    const tasks = new Map(integrated.tasks.map((task) => [task.id, task]));
    expect([...tasks.keys()]).toEqual(["T1", "T2", "T3", "T4", "T5", "E1"]);

    expect(tasks.get("T1")).toMatchObject({ pickup: [2, 0], dropoff: [2, 5], deadline: 500 });
    expect(tasks.get("T2")).toMatchObject({ pickup: [13, 6], dropoff: [2, 15], deadline: 500 });
    expect(tasks.get("T4")).toMatchObject({ pickup: [8, 0], dropoff: [12, 9], releaseTime: 4, deadline: 500 });
    expect(tasks.get("E1")).toMatchObject({ target: [11, 10], releaseTime: 12, deadline: 300 });
    expect(tasks.get("T3")).toMatchObject({ targets: HORIZONTAL_INSPECTION_TARGETS, deadline: 400 });
    expect(tasks.get("T5")).toMatchObject({
      targets: VERTICAL_INSPECTION_TARGETS,
      releaseTime: 8,
      deadline: 700
    });

    expect(integrated.robots.map((robot) => [robot.battery, robot.batteryCapacity])).toEqual([
      [512, 512], [510, 512], [508, 512], [506, 512]
    ]);
    expect(integrated.dynamic).toEqual({ triggerTime: 12, blockedCells: [], failedRobots: [], tasks: [] });
  });

  it("keeps ids unique and map coordinates inside bounds", () => {
    for (const scenario of scenarios) {
      expect(scenario.width).toBeGreaterThan(0);
      expect(scenario.height).toBeGreaterThan(0);
      expect(scenario.dynamic.triggerTime).toBeGreaterThanOrEqual(0);

      const robotIds = new Set(scenario.robots.map((robot) => robot.id));
      const taskIds = new Set([...scenario.tasks, ...scenario.dynamic.tasks].map((task) => task.id));
      expect(robotIds.size).toBe(scenario.robots.length);
      expect(taskIds.size).toBe(scenario.tasks.length + scenario.dynamic.tasks.length);

      for (const cell of [
        ...scenario.obstacles,
        ...scenario.zones.warehouse,
        ...scenario.zones.inspection,
        ...scenario.zones.delivery,
        ...scenario.dynamic.blockedCells
      ]) {
        expectInsideScenario(scenario, cell);
      }

      for (const robot of scenario.robots) {
        expect(robot.battery).toBeGreaterThanOrEqual(0);
        expect(robot.load).toBeGreaterThanOrEqual(0);
        expectInsideScenario(scenario, robot.start);
      }

      for (const task of [...scenario.tasks, ...scenario.dynamic.tasks]) {
        expect(task.priority).toBeGreaterThanOrEqual(0);
        if (task.releaseTime !== undefined) expect(task.releaseTime).toBeGreaterThanOrEqual(0);
        if (task.deadline !== undefined) expect(task.deadline).toBeGreaterThanOrEqual(0);
        if (task.type === "delivery") expect(task.demand).toBeGreaterThan(0);
        for (const waypoint of taskWaypoints(task)) {
          expectInsideScenario(scenario, waypoint);
        }
      }
    }
  });
});
