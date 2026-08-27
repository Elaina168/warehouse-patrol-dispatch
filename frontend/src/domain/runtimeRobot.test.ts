import { describe, expect, it } from "vitest";

import {
  applyMapPickToRuntimeRobot,
  buildRuntimeRobot,
  createRuntimeRobotForm,
  getVisibleRobotCells,
  mergeRuntimeRobotStates,
  validateRuntimeRobotForm
} from "./runtimeRobot";
import type { Scenario } from "./types";

const scenario = {
  id: "runtime-robot",
  name: "运行时机器人",
  description: "运行时机器人测试",
  width: 6,
  height: 4,
  obstacles: [[1, 1]],
  zones: { warehouse: [[0, 0]], inspection: [[5, 0]], delivery: [[5, 3]], charging: [[5, 3]] },
  shelves: [],
  robots: [{ id: "R1", name: "初始机器人", start: [0, 0], battery: 100, load: 1 }],
  tasks: [],
  dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
} satisfies Scenario;

describe("runtime robot form", () => {
  it("requires identity, an in-range position, and at least one capability", () => {
    const form = createRuntimeRobotForm(scenario);
    expect(validateRuntimeRobotForm({ ...form, id: "", name: "", start: "9, 9", capabilities: [] }, scenario))
      .toEqual(["请输入机器人 ID。", "请输入机器人名称。", "请输入地图范围内的整数接入坐标。", "至少选择一种机器人能力。"]);
  });

  it("normalizes numeric robot values and builds the exact robot model", () => {
    const form = {
      ...createRuntimeRobotForm(scenario),
      id: " R5 ",
      name: "  运行时机器人 ",
      start: "2, 0",
      battery: "200",
      batteryCapacity: "100",
      load: "-4",
      moveTicks: "9",
      capabilities: ["inspection"] as const
    };

    expect(buildRuntimeRobot(form, scenario)).toEqual({
      id: "R5",
      name: "运行时机器人",
      start: [2, 0],
      battery: 100,
      batteryCapacity: 100,
      load: 0,
      moveTicks: 4,
      capabilities: ["inspection"]
    });
  });

  it("writes a map-picked cell into the onboarding form", () => {
    const form = createRuntimeRobotForm(scenario);
    expect(applyMapPickToRuntimeRobot(form, [3, 2]).start).toBe("3, 2");
  });
});

describe("runtime robot session synchronization", () => {
  it("merges the current complete robot set and reset response removes joined robots", () => {
    const joined = mergeRuntimeRobotStates(scenario, [{
      robotId: "R1",
      name: "初始机器人",
      start: [0, 0],
      position: [0, 0],
      status: "idle",
      battery: 90,
      batteryCapacity: 100,
      load: 1,
      moveTicks: 1,
      capabilities: ["delivery"],
      joinedAt: 0,
      currentTaskId: null
    }, {
      robotId: "R5",
      name: "运行时机器人",
      start: [2, 0],
      position: [2, 0],
      status: "idle",
      battery: 80,
      batteryCapacity: 100,
      load: 0,
      moveTicks: 2,
      capabilities: ["inspection"],
      joinedAt: 12,
      currentTaskId: null
    }]);
    expect(joined.robots).toHaveLength(2);
    expect(joined.robots[1]).toMatchObject({ id: "R5", start: [2, 0], battery: 80, capabilities: ["inspection"] });

    const reset = mergeRuntimeRobotStates(joined, [joined.robots[0] && {
      robotId: "R1",
      name: "初始机器人",
      start: [0, 0],
      position: [0, 0],
      status: "idle",
      battery: 100,
      batteryCapacity: 100,
      load: 1,
      moveTicks: 1,
      capabilities: ["delivery"],
      joinedAt: 0,
      currentTaskId: null
    }]);
    expect(reset.robots.map((robot) => robot.id)).toEqual(["R1"]);
  });

  it("hides a runtime robot path before its real join time", () => {
    const paths = { R1: [[0, 0], [1, 0]], R5: [[2, 0], [3, 0]] } as Record<string, [number, number][]>;
    expect(getVisibleRobotCells(paths, { R1: 0, R5: 12 }, 11)).toEqual(new Map([["R1", [1, 0]]]));
    expect(getVisibleRobotCells(paths, { R1: 0, R5: 12 }, 12)).toEqual(new Map([["R1", [1, 0]], ["R5", [2, 0]]]));
  });
});
