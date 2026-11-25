"""What the equipment can do: the dumbbells' loads and the step's limit."""

from dataclasses import dataclass

from reps.library import Exercise

# Bowflex 552, pounds on the dial: the 15 settings Bowflex lists (its model 52 has the same).
DIAL_LOADS = (5, 7.5, 10, 12.5, 15, 17.5, 20, 22.5, 25, 30, 35, 40, 45, 50, 52.5)

KG_PER_LB = 0.45359237  # body weight is in kilograms, the step's limit in pounds
UNCONFIRMED_MARGIN = 0.8


@dataclass(frozen=True)
class Gym:
    step_limit_lb: float = 300
    step_confirmed: bool = False

    @property
    def step_allows_lb(self) -> float:
        return self.step_limit_lb * (1 if self.step_confirmed else UNCONFIRMED_MARGIN)

    def loads(self, exercise: Exercise, body_kg: float | None) -> tuple[float, ...]:
        """Return the dial settings this person may use for an exercise."""
        if exercise.kind != "load":
            return ()
        if not exercise.on_step:
            return DIAL_LOADS
        if not body_kg:
            return ()
        room = self.step_allows_lb - body_kg / KG_PER_LB
        return tuple(load for load in DIAL_LOADS if load * exercise.dumbbells <= room)


def pounds(load: float) -> str:
    return f"{load:g} lb"
