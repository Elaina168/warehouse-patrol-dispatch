from pathlib import Path

import inspect

import pytest
from fastapi import HTTPException

from backend.app import sessions
from backend.app.schemas import Conflict
import backend.competition.runners as runners_module
from backend.competition.manifests import ManifestValidationError, load_manifest
from backend.competition.runners import _validate_main_acceptance, run_main_demo, run_safety_demo
from backend.tests.test_sessions import _forced_safety_gate_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_MANIFEST_PATH = PROJECT_ROOT / "competition" / "3s" / "manifests" / "main-demo.json"
SAFETY_MANIFEST_PATH = PROJECT_ROOT / "competition" / "3s" / "manifests" / "safety-demo.json"
INVALID_MANIFESTS_DIRECTORY = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "competition"


def test_main_demo_manifest_is_the_fixed_submission_contract() -> None:
    manifest = load_manifest(MAIN_MANIFEST_PATH)

    assert manifest["schemaVersion"] == 1
    assert manifest["scenarioId"] == "integrated-demo"
    assert manifest["options"] == {
        "avoidConflicts": True,
        "includeDynamic": True,
        "assignmentReplanWindow": 24,
        "adaptiveReplanWindow": False,
    }
    assert manifest["steps"] == [
        {
            "time": 12,
            "action": "addTask",
            "task": {
                "id": "DEMO-URGENT-12",
                "type": "emergency",
                "title": "高优先级冷链复核",
                "priority": 5,
                "releaseTime": 12,
                "deadline": 120,
                "serviceTime": 2,
                "target": [1, 4],
            },
        },
        {"time": 20, "action": "addBlockedCell", "cell": [10, 6]},
        {"time": 28, "action": "failRobot", "robotId": "R2"},
        {"time": 36, "action": "removeBlockedCell", "cell": [10, 6]},
        {"time": 36, "action": "restoreRobot", "robotId": "R2"},
        {"time": 700, "action": "accept"},
    ]


@pytest.mark.parametrize(
    "manifest_name",
    [
        "unknown-version.json",
        "wrong-step-order.json",
    ],
)
def test_manifest_loader_rejects_unknown_version_and_wrong_step_order(
    manifest_name: str,
) -> None:
    with pytest.raises(ManifestValidationError):
        load_manifest(INVALID_MANIFESTS_DIRECTORY / manifest_name)


def test_manifest_loader_rejects_main_demo_identifier_mismatch() -> None:
    with pytest.raises(ManifestValidationError, match="robotId"):
        load_manifest(INVALID_MANIFESTS_DIRECTORY / "wrong-robot-id.json")


def test_main_demo_runner_checks_each_contract_checkpoint_and_deletes_session() -> None:
    evidence = run_main_demo()

    assert list(evidence.checkpoints) == [12, 20, 28, 36, 700]
    assert evidence.checkpoints[12].currentTime == 12
    assert evidence.checkpoints[20].result.extraBlocked == [(10, 6)]
    assert evidence.checkpoints[28].result.unavailableRobotIds == ["R2"]
    assert evidence.checkpoints[36].result.extraBlocked == []
    assert evidence.checkpoints[36].result.unavailableRobotIds == []
    assert evidence.final.completedTaskCount == 7
    assert evidence.final.safetyIntervention is None
    assert evidence.final.result.metrics.conflictCount == 0
    assert evidence.final.result.metrics.failureCount == 0
    assert evidence.final.result.metrics.deadlineMissCount == 0

    with pytest.raises(HTTPException, match="调度会话不存在"):
        sessions.get_session(evidence.session_id)


def test_main_demo_runner_rejects_tick_that_does_not_reach_t12(monkeypatch) -> None:
    original_tick_session = runners_module.tick_session

    def tick_before_t12(session_id: str, request):
        result = original_tick_session(session_id, request)
        if request.currentTime == 12:
            return result.model_copy(update={"currentTime": 11})
        return result

    monkeypatch.setattr(runners_module, "tick_session", tick_before_t12)

    with pytest.raises(RuntimeError, match="T=12"):
        run_main_demo()


def test_main_demo_runner_keeps_distinct_t36_action_evidence_in_order() -> None:
    evidence = run_main_demo()

    t36_actions = [
        record
        for record in evidence.step_evidence
        if record.time == 36
    ]
    assert [record.action for record in t36_actions] == [
        "removeBlockedCell",
        "restoreRobot",
    ]
    assert t36_actions[0].result.result.extraBlocked == []
    assert t36_actions[0].result.result.unavailableRobotIds == ["R2"]
    assert t36_actions[1].result.result.extraBlocked == []
    assert t36_actions[1].result.result.unavailableRobotIds == []


def test_main_demo_runner_rejects_early_safety_intervention_cleared_before_t700(
    monkeypatch,
) -> None:
    original_tick_session = runners_module.tick_session

    def tick_with_early_intervention(session_id: str, request):
        result = original_tick_session(session_id, request)
        if request.currentTime == 12:
            return result.model_copy(
                update={
                    "safetyIntervention": Conflict(
                        time=12,
                        type="vertex",
                        robots=["R1", "R2"],
                        cell=(1, 4),
                    )
                }
            )
        return result

    monkeypatch.setattr(runners_module, "tick_session", tick_with_early_intervention)

    with pytest.raises(RuntimeError, match="safetyIntervention"):
        run_main_demo()


def test_main_demo_acceptance_rejects_a_remaining_active_conflict() -> None:
    final = run_main_demo().final
    metrics_history = [*final.metricsHistory]
    metrics_history[-1] = metrics_history[-1].model_copy(
        update={"activeConflictCount": 1}
    )
    unsafe_final = final.model_copy(update={"metricsHistory": metrics_history})

    with pytest.raises(RuntimeError, match="活动冲突"):
        _validate_main_acceptance(unsafe_final)


def test_safety_manifest_is_the_single_source_for_existing_forced_gate_fixture() -> None:
    manifest = load_manifest(SAFETY_MANIFEST_PATH)

    assert _forced_safety_gate_scenario() == manifest["scenario"]
    assert "load_safety_demo_scenario" in inspect.getsource(_forced_safety_gate_scenario)
    assert manifest["schemaVersion"] == 1
    assert manifest["scenario"]["id"] == "forced-safety-gate"
    assert manifest["scenario"]["width"] == 3
    assert manifest["scenario"]["height"] == 1
    assert [robot["id"] for robot in manifest["scenario"]["robots"]] == ["R1", "R2"]
    assert [task["id"] for task in manifest["scenario"]["tasks"]] == ["T1", "T2"]


def test_safety_runner_proves_execution_gate_and_collision_free_history() -> None:
    evidence = run_safety_demo()

    first = evidence.checkpoints[2]
    assert first.safetyIntervention is not None
    assert first.safetyIntervention.time == 2
    assert len({state.position for state in first.robotStates}) == len(first.robotStates)
    assert first.metricsHistory[-1].activeConflictCount == 0
    assert evidence.checkpoints[4].safetyStall is not None
    assert evidence.checkpoints[4].safetyStall.consecutiveCount == 3
    assert evidence.collision_free is True
