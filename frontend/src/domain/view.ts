import type { Cell, Robot, Scenario } from "./types";

export type ZoneCellPresentation = {
  classNames: string[];
  label: string;
};

const ZONE_CELL_STYLES = [
  ["charging", "charging-cell", "充"],
  ["warehouse", "warehouse-cell", "进"],
  ["delivery", "delivery-cell", "出"],
  ["inspection", "inspection-cell", "巡"]
] as const;

export function cellKey(cell: Cell): string {
  return `${cell[0]},${cell[1]}`;
}

export function buildZoneCellPresentations(zones: Scenario["zones"]): Map<string, ZoneCellPresentation> {
  const presentations = new Map<string, ZoneCellPresentation>();
  for (const [zoneName, className, label] of ZONE_CELL_STYLES) {
    for (const cell of zones[zoneName] ?? []) {
      const key = cellKey(cell);
      const current = presentations.get(key);
      presentations.set(key, {
        classNames: [...(current?.classNames ?? []), className],
        label: current?.label ?? label
      });
    }
  }
  return presentations;
}

function pathAt(path: Cell[], time: number): Cell | null {
  if (path.length === 0) return null;
  return path[Math.min(time, path.length - 1)];
}

export function getRobotStateAt(robot: Robot, path: Cell[], time: number, unavailableRobotIds: string[]) {
  const failed = unavailableRobotIds.includes(robot.id);
  const position = pathAt(path, time) ?? robot.start;
  const progress = path.length <= 1 ? 1 : Math.min(1, time / (path.length - 1));
  return {
    ...robot,
    position,
    progress,
    status: failed ? "故障" : progress >= 1 ? "已完成" : "执行中"
  };
}
