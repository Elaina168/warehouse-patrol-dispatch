import { describe, expect, it } from "vitest";

import scenariosData from "./scenarios.json";
import { fixedDemoScenarioIds, scenarios } from "./scenarios";
import { cellKey, getRobotStateAt } from "./view";
import type { Cell, Scenario, Task } from "./types";

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
});

describe("scenario data", () => {
  it("exposes one integrated demo scenario for the main simulation", () => {
    expect(fixedDemoScenarioIds).toEqual(["integrated-demo"]);
    expect(scenarios.map((scenario) => scenario.id)).toEqual(fixedDemoScenarioIds);
    expect(scenariosData.map((scenario) => scenario.id)).toEqual(["integrated-demo"]);

    const byId = new Map(scenarios.map((scenario) => [scenario.id, scenario]));
    const integrated = byId.get("integrated-demo");
    expect(integrated).toBeDefined();

    expect(integrated?.robots.length).toBeGreaterThanOrEqual(4);
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
