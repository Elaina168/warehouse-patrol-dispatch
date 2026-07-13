import scenariosData from "./scenarios.json";
import type { Scenario } from "./types";

export const fixedDemoScenarioIds = ["integrated-demo"] as const;

export const scenarios = (scenariosData as unknown as Scenario[]).filter((scenario) =>
  (fixedDemoScenarioIds as readonly string[]).includes(scenario.id)
);
