import type { Cell, Scenario, Shelf, ShelfRuntimeState } from "./types";
import { cellKey } from "./view";

export type ShelfCellPresentation = {
  classNames: string[];
  label: string;
};

export type WarehouseDeliveryCandidate = {
  kind: "inbound" | "outbound";
  pickup: Cell;
  dropoff: Cell;
  signature: string;
};

export function buildShelfCellPresentations(
  shelves: Shelf[],
  states: ShelfRuntimeState[]
): Map<string, ShelfCellPresentation> {
  const stateById = new Map(states.map((state) => [state.shelfId, state]));
  return new Map(shelves.map((shelf) => {
    const status = stateById.get(shelf.id)?.status;
    const stocked = status === "occupied" || status === "outboundReserved";
    const statusLabel = status === undefined ? "状态未知" : {
      empty: "空",
      inboundReserved: "入库已预订",
      occupied: "已有货物",
      outboundReserved: "出库已预订"
    }[status];
    return [
      cellKey(shelf.cell),
      {
        classNames: stocked ? ["shelf-cell", "shelf-stocked"] : ["shelf-cell"],
        label: `货架 ${shelf.id} · ${statusLabel}`
      }
    ];
  }));
}

export function buildWarehouseDeliveryCandidates(
  scenario: Scenario,
  states: ShelfRuntimeState[]
): WarehouseDeliveryCandidate[] {
  return states.flatMap<WarehouseDeliveryCandidate>((state) => {
    if (state.status === "empty") {
      return scenario.zones.warehouse.map((pickup) => ({
        kind: "inbound" as const,
        pickup,
        dropoff: state.serviceCell,
        signature: `delivery:${cellKey(pickup)}>${cellKey(state.serviceCell)}`
      }));
    }
    if (state.status === "occupied") {
      return scenario.zones.delivery.map((dropoff) => ({
        kind: "outbound" as const,
        pickup: state.serviceCell,
        dropoff,
        signature: `delivery:${cellKey(state.serviceCell)}>${cellKey(dropoff)}`
      }));
    }
    return [];
  });
}
