from dataclasses import dataclass

from backend.app.schemas import DispatchOptions


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationVariant:
    variant_id: str
    assignment_replan_window: int
    adaptive_replan_window: bool

    def options(self) -> DispatchOptions:
        return DispatchOptions(
            avoidConflicts=True,
            includeDynamic=True,
            assignmentReplanWindow=self.assignment_replan_window,
            adaptiveReplanWindow=self.adaptive_replan_window,
        )


_VARIANTS = (
    AdaptiveCalibrationVariant("fixed-4", 4, False),
    AdaptiveCalibrationVariant("fixed-24", 24, False),
    AdaptiveCalibrationVariant("fixed-48", 48, False),
    AdaptiveCalibrationVariant("adaptive-current-24", 24, True),
)


def adaptive_calibration_variants(
    variant_ids: tuple[str, ...] | None = None,
) -> tuple[AdaptiveCalibrationVariant, ...]:
    if variant_ids is None:
        return _VARIANTS
    if not variant_ids:
        raise ValueError("校准变体必须至少包含一项")
    known = {variant.variant_id for variant in _VARIANTS}
    unknown = sorted(set(variant_ids) - known)
    if unknown:
        raise ValueError(f"未知校准变体: {', '.join(unknown)}")
    return tuple(
        variant
        for variant in _VARIANTS
        if variant.variant_id in variant_ids
    )
