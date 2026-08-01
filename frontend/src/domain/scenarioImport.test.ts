import { describe, expect, it } from "vitest";

import { parseScenario } from "./scenarioImport";
import type { Scenario } from "./types";

function buildScenario(): Scenario {
  return {
    id: "scenario-import",
    name: "场景导入",
    description: "",
    width: 8,
    height: 8,
    obstacles: [],
    zones: {
      warehouse: [[0, 1]],
      inspection: [[1, 0]],
      delivery: [[2, 0]],
      charging: [[0, 2]]
    },
    shelves: [],
    robots: [{
      id: "R1",
      name: "机器人 1",
      start: [0, 0],
      battery: 100,
      load: 2
    }],
    tasks: [{
      id: "T1",
      type: "inspection",
      title: "巡检",
      priority: 3,
      releaseTime: 0,
      deadline: 20,
      serviceTime: 1,
      targets: [[1, 0]]
    }],
    dynamic: {
      triggerTime: 5,
      blockedCells: [],
      failedRobots: [],
      tasks: []
    },
    chargeTime: 4
  };
}

function addUnknownField(value: object): void {
  (value as Record<string, unknown>).unknownField = true;
}

describe("scenario import contract", () => {
  it("accepts nullable task timing fields", () => {
    const scenario = buildScenario();
    scenario.tasks[0].releaseTime = null;
    scenario.tasks[0].deadline = null;
    scenario.tasks[0].serviceTime = null;

    const parsed = parseScenario(scenario);

    expect(parsed.tasks[0]).toMatchObject({
      releaseTime: null,
      deadline: null,
      serviceTime: null
    });
  });

  it("accepts a base target that is blocked only by a future dynamic event", () => {
    const scenario = buildScenario();
    scenario.dynamic.blockedCells = [[1, 0]];

    expect(parseScenario(scenario).tasks[0]).toMatchObject({ targets: [[1, 0]] });
  });

  it("accepts backend-defaulted optional scenario fields", () => {
    const scenario = structuredClone(buildScenario()) as unknown as {
      shelves?: Scenario["shelves"];
      chargeTime?: number;
      zones: Scenario["zones"];
      robots: Scenario["robots"];
      tasks: Scenario["tasks"];
      [key: string]: unknown;
    };
    delete scenario.shelves;
    delete scenario.chargeTime;
    delete scenario.zones.charging;

    const parsed = parseScenario(scenario);

    expect(parsed.shelves).toEqual([]);
    expect(parsed.zones.charging).toBeUndefined();
    expect(parsed.chargeTime).toBeUndefined();
    expect(parsed.robots[0].batteryCapacity).toBeUndefined();
    expect(parsed.robots[0].moveTicks).toBeUndefined();
    expect(parsed.robots[0].capabilities).toBeUndefined();
  });

  it.each<[string, (scenario: Scenario) => void]>([
    ["Scenario", (scenario) => addUnknownField(scenario)],
    ["Zones", (scenario) => addUnknownField(scenario.zones)],
    ["Shelf", (scenario) => {
      scenario.shelves = [{
        id: "S1",
        cell: [4, 4],
        serviceCell: [4, 3],
        initialOccupied: false
      }];
      addUnknownField(scenario.shelves[0]);
    }],
    ["Robot", (scenario) => addUnknownField(scenario.robots[0])],
    ["DynamicEvent", (scenario) => addUnknownField(scenario.dynamic)],
    ["Task", (scenario) => addUnknownField(scenario.tasks[0])]
  ])("rejects unknown fields in %s objects", (_, mutate) => {
    const scenario = buildScenario();
    mutate(scenario);

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it.each([1.5, -1, 6])("rejects task priority %s outside the integer zero-to-five range", (priority) => {
    const scenario = buildScenario();
    scenario.tasks[0].priority = priority;

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects service time above 10000", () => {
    const scenario = buildScenario();
    scenario.tasks[0].serviceTime = 10_001;

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects charge time above 10000", () => {
    const scenario = buildScenario();
    scenario.chargeTime = 10_001;

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects release and dynamic trigger times above the session limit", () => {
    const lateTask = structuredClone(buildScenario());
    lateTask.tasks[0].releaseTime = 10_001;
    expect(() => parseScenario(lateTask)).toThrow("JSON 必须是 Scenario 对象");

    const lateDynamic = structuredClone(buildScenario());
    lateDynamic.dynamic.triggerTime = 10_001;
    expect(() => parseScenario(lateDynamic)).toThrow("JSON 必须是 Scenario 对象");
  });

  it.each(["width", "height"] as const)("rejects %s above 64", (axis) => {
    const scenario = buildScenario();
    scenario[axis] = 65;

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects maps above 1024 total cells", () => {
    const scenario = buildScenario();
    scenario.width = 33;
    scenario.height = 32;

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects more than 32 robots", () => {
    const scenario = buildScenario();
    scenario.robots = Array.from({ length: 33 }, (_, index) => ({
      id: `R${index + 1}`,
      name: `机器人 ${index + 1}`,
      start: [index % 8, Math.floor(index / 8)] as [number, number],
      battery: 100,
      load: 2
    }));

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects more than 128 initial and dynamic tasks in total", () => {
    const scenario = buildScenario();
    const task = scenario.tasks[0];
    scenario.tasks = Array.from({ length: 64 }, (_, index) => ({
      ...task,
      id: `T${index + 1}`
    }));
    scenario.dynamic.tasks = Array.from({ length: 65 }, (_, index) => ({
      ...task,
      id: `D${index + 1}`
    }));

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects more than 64 inspection targets", () => {
    const scenario = buildScenario();
    const task = scenario.tasks[0];
    if (task.type !== "inspection") throw new Error("expected inspection task");
    task.targets = Array.from({ length: 65 }, (_, index) => [
      index % scenario.width,
      Math.floor(index / scenario.width) % scenario.height
    ]);

    expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
  });

  it("rejects repeated robot start coordinates", () => {
    const scenario = buildScenario();
    scenario.robots.push({
      ...scenario.robots[0],
      id: "R2",
      name: "机器人 2"
    });

    expect(() => parseScenario(scenario)).toThrow("机器人起点重复：(0, 0)");
  });
});
