import { describe, expect, it } from "vitest";

import { buildShelfCellPresentations, buildWarehouseDeliveryCandidates } from "./inventory";
import type { Scenario, ShelfRuntimeState } from "./types";

describe("shelf inventory view", () => {
  it("highlights only occupied and outbound-reserved shelves", () => {
    const presentations = buildShelfCellPresentations(
      [
        { id: "S01", cell: [2, 2], serviceCell: [2, 1], initialOccupied: false },
        { id: "S02", cell: [4, 2], serviceCell: [4, 1], initialOccupied: true }
      ],
      [
        { shelfId: "S01", cell: [2, 2], serviceCell: [2, 1], status: "inboundReserved" },
        { shelfId: "S02", cell: [4, 2], serviceCell: [4, 1], status: "outboundReserved" }
      ]
    );

    expect(presentations.get("2,2")?.classNames).toEqual(["shelf-cell"]);
    expect(presentations.get("4,2")?.classNames).toEqual(["shelf-cell", "shelf-stocked"]);
    expect(presentations.get("4,2")?.label).toBe("货架 S02 · 出库已预订");
  });

  it("keeps shelves neutral when runtime states are missing", () => {
    const presentations = buildShelfCellPresentations(
      [
        { id: "S01", cell: [2, 2], serviceCell: [2, 1], initialOccupied: false },
        { id: "S02", cell: [4, 2], serviceCell: [4, 1], initialOccupied: true }
      ],
      []
    );

    expect(presentations.get("2,2")).toEqual({
      classNames: ["shelf-cell"],
      label: "货架 S01 · 状态未知"
    });
    expect(presentations.get("4,2")).toEqual({
      classNames: ["shelf-cell"],
      label: "货架 S02 · 状态未知"
    });
  });

  it("highlights only shelves that have authoritative runtime states", () => {
    const presentations = buildShelfCellPresentations(
      [
        { id: "S01", cell: [2, 2], serviceCell: [2, 1], initialOccupied: false },
        { id: "S02", cell: [4, 2], serviceCell: [4, 1], initialOccupied: true }
      ],
      [
        { shelfId: "S01", cell: [2, 2], serviceCell: [6, 1], status: "occupied" }
      ]
    );

    expect(presentations.get("2,2")?.classNames).toEqual(["shelf-cell", "shelf-stocked"]);
    expect(presentations.get("4,2")).toEqual({
      classNames: ["shelf-cell"],
      label: "货架 S02 · 状态未知"
    });
  });
});

describe("warehouse delivery candidates", () => {
  it("uses only empty shelves for inbound deliveries and occupied shelves for outbound deliveries", () => {
    const scenario = buildShelfScenario();
    const states: ShelfRuntimeState[] = [
      { shelfId: "S01", cell: [2, 3], serviceCell: [2, 2], status: "empty" },
      { shelfId: "S02", cell: [3, 3], serviceCell: [3, 2], status: "occupied" },
      { shelfId: "S03", cell: [4, 3], serviceCell: [4, 2], status: "inboundReserved" },
      { shelfId: "S04", cell: [5, 3], serviceCell: [5, 2], status: "outboundReserved" }
    ];

    expect(buildWarehouseDeliveryCandidates(scenario, states)).toEqual([
      {
        kind: "inbound",
        pickup: [2, 0],
        dropoff: [2, 2],
        signature: "delivery:2,0>2,2"
      },
      {
        kind: "outbound",
        pickup: [3, 2],
        dropoff: [2, 15],
        signature: "delivery:3,2>2,15"
      }
    ]);
  });

  it("returns no candidates without authoritative runtime states", () => {
    expect(buildWarehouseDeliveryCandidates(buildShelfScenario(), [])).toEqual([]);
  });

  it("uses only the runtime state cell data when scenario shelves are missing states", () => {
    const candidates = buildWarehouseDeliveryCandidates(buildShelfScenario(), [
      { shelfId: "S01", cell: [6, 3], serviceCell: [6, 2], status: "empty" }
    ]);

    expect(candidates).toEqual([
      {
        kind: "inbound",
        pickup: [2, 0],
        dropoff: [6, 2],
        signature: "delivery:2,0>6,2"
      }
    ]);
  });
});

function buildShelfScenario(): Scenario {
  return {
    id: "inventory",
    name: "inventory",
    description: "",
    width: 8,
    height: 16,
    obstacles: [],
    zones: {
      warehouse: [[2, 0]],
      inspection: [[0, 1]],
      delivery: [[2, 15]]
    },
    shelves: [
      { id: "S01", cell: [2, 3], serviceCell: [2, 2], initialOccupied: false },
      { id: "S02", cell: [3, 3], serviceCell: [3, 2], initialOccupied: true },
      { id: "S03", cell: [4, 3], serviceCell: [4, 2], initialOccupied: false },
      { id: "S04", cell: [5, 3], serviceCell: [5, 2], initialOccupied: true }
    ],
    robots: [{ id: "R1", name: "R1", start: [0, 0], battery: 90, load: 2 }],
    tasks: [],
    dynamic: { triggerTime: 20, blockedCells: [], failedRobots: [], tasks: [] }
  };
}
