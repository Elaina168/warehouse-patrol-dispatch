import type { Cell, Robot } from "./types";

export function cellKey(cell: Cell): string {
  return `${cell[0]},${cell[1]}`;
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
