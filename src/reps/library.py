"""The exercises, and the equipment each one needs."""

from dataclasses import dataclass

EQUIPMENT = {
    "dumbbells": "Dumbbells",
    "step": "Reebok step",
    "tower": "Tower",
    "bar": "Pull-up bar",
    "treadmill": "Treadmill",
    "rower": "Air rower",
    "bike": "Spin bike",
}

PATTERNS = {
    "squat": "Squat",
    "hinge": "Hinge",
    "lunge": "Lunge",
    "hpush": "Push",
    "vpush": "Overhead push",
    "pull": "Pull",
    "core": "Core",
    "arms": "Arms",
    "bar": "Pull-up bar",
}


@dataclass(frozen=True)
class Exercise:
    key: str
    name: str
    pattern: str
    kind: str  # load, reps or time
    needs: frozenset[str] = frozenset()
    rank: int = 0  # lower is preferred; only the best rank available is used
    start: float = 10  # first load to try, pounds on each dial
    dumbbells: int = 2
    per_side: bool = False
    on_step: bool = False  # the person's weight and the dumbbells are on the step
    harder: str = ""
    cue: str = ""


def _exercise(key: str, name: str, pattern: str, kind: str, needs: str = "", **more) -> Exercise:
    return Exercise(key, name, pattern, kind, frozenset(needs.split()), **more)


_ALL = [
    _exercise("goblet-squat", "Goblet squat", "squat", "load", "dumbbells", start=15,
              dumbbells=1, harder="split-squat", cue="One dumbbell held at the chest."),
    _exercise("front-squat", "Dumbbell front squat", "squat", "load", "dumbbells", start=10,
              harder="split-squat", cue="Dumbbells at the shoulders, elbows up."),
    _exercise("air-squat", "Air squat", "squat", "reps", rank=2, cue="Slow down, quick up."),
    _exercise("romanian-deadlift", "Romanian deadlift", "hinge", "load", "dumbbells", start=20,
              harder="one-leg-deadlift", cue="Hips back, flat back, soft knees."),
    _exercise("deadlift", "Dumbbell deadlift", "hinge", "load", "dumbbells", rank=1, start=20,
              cue="Dumbbells beside the feet; flat back; stand tall."),
    _exercise("one-leg-deadlift", "One-leg deadlift", "hinge", "load", "dumbbells", rank=1,
              start=10, per_side=True, cue="Hips square; touch down if you wobble."),
    _exercise("glute-bridge", "Glute bridge", "hinge", "reps", rank=2,
              cue="Squeeze for a second at the top."),
    _exercise("split-squat", "Split squat", "lunge", "load", "dumbbells", start=10,
              per_side=True, cue="Back knee straight down."),
    _exercise("step-up", "Step-up", "lunge", "load", "dumbbells step", start=10,
              per_side=True, on_step=True, cue="Whole foot on the step; drive through the heel."),
    _exercise("bodyweight-lunge", "Reverse lunge", "lunge", "reps", rank=2, per_side=True,
              cue="Step back, knee straight down."),
    _exercise("floor-press", "Floor press", "hpush", "load", "dumbbells", start=15,
              cue="Elbows 45 degrees from the body; pause on the floor."),
    _exercise("step-press", "Press on the step", "hpush", "load", "dumbbells step", start=15,
              on_step=True, cue="Back rest flat. Feet planted."),
    _exercise("tower-push-up", "Push-up on the tower", "hpush", "reps", "tower", rank=1,
              harder="feet-up-push-up", cue="Body in one line."),
    _exercise("feet-up-push-up", "Feet-up push-up", "hpush", "reps", "step", rank=3,
              harder="slow-push-up", cue="Feet on the step, body in one line."),
    _exercise("slow-push-up", "Slow push-up", "hpush", "reps", rank=3,
              cue="Three seconds down."),
    _exercise("push-up", "Push-up", "hpush", "reps", rank=2, harder="feet-up-push-up",
              cue="Body in one line. Knees down if needed."),
    _exercise("shoulder-press", "Standing shoulder press", "vpush", "load", "dumbbells",
              start=10, cue="Ribs down; don't lean back."),
    _exercise("seated-press", "Seated press", "vpush", "load", "dumbbells step", start=10,
              on_step=True, cue="Back rest up. Feet planted."),
    _exercise("pike-push-up", "Pike push-up", "vpush", "reps", rank=2,
              cue="Hips high, head toward the floor."),
    _exercise("one-arm-row", "One-arm row", "pull", "load", "dumbbells", start=15,
              dumbbells=1, per_side=True, cue="Hand braced on your knee; pull to the hip."),
    _exercise("bent-row", "Bent-over row", "pull", "load", "dumbbells", start=15,
              cue="Flat back; pull to the ribs."),
    _exercise("supported-row", "Supported row", "pull", "load", "dumbbells step", start=15,
              on_step=True, cue="Chest on the raised back rest."),
    _exercise("prone-raise", "Prone Y-raise", "pull", "reps", rank=2,
              cue="Face down, thumbs up, lift the arms."),
    _exercise("knee-raise", "Knee raise", "core", "reps", "tower", harder="leg-raise",
              cue="Back against the tower; no swing."),
    _exercise("leg-raise", "Leg raise", "core", "reps", "tower", rank=3,
              harder="slow-leg-raise", cue="Legs straight; no swing."),
    _exercise("slow-leg-raise", "Slow leg raise", "core", "reps", "tower", rank=3,
              cue="Three seconds down."),
    _exercise("dead-bug", "Dead bug", "core", "reps", per_side=True,
              cue="Lower back stays on the floor."),
    _exercise("plank", "Plank", "core", "time", cue="Squeeze glutes; breathe."),
    _exercise("curl", "Curl", "arms", "load", "dumbbells", start=10, cue="Elbows still."),
    _exercise("triceps-press", "Lying triceps press", "arms", "load", "dumbbells", start=5,
              cue="On the floor; elbows still."),
    _exercise("lateral-raise", "Lateral raise", "arms", "load", "dumbbells", start=5,
              cue="Lead with the elbows, to shoulder height."),
    _exercise("pull-up", "Pull-up", "bar", "reps", "bar",
              cue="Lower slowly; a jump up is fine."),
]

EXERCISES = {exercise.key: exercise for exercise in _ALL}

MACHINES = {"bike": "Spin bike", "rower": "Air rower", "treadmill": "Treadmill"}
