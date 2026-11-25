"""How to do each exercise: steps, what to watch for, and a page at ACE where there is one.

The links were opened on 2026-11-24. ACE is the American Council on Exercise.
"""

from dataclasses import dataclass

_ACE = "https://www.acefitness.org/resources/everyone/exercise-library/"


@dataclass(frozen=True)
class Guide:
    steps: tuple[str, ...]
    watch: str
    link: str = ""
    source: str = ""

    def as_dict(self) -> dict:
        return {"steps": self.steps, "watch": self.watch,
                "link": self.link or _ACE, "source": self.source or "ACE exercise library"}


def _guide(*steps: str, watch: str, page: str = "", source: str = "") -> Guide:
    return Guide(steps, watch, _ACE + page if page else "", f"ACE: {source}" if source else "")


GUIDES = {
    "goblet-squat": _guide(
        "Hold one dumbbell upright at your chest, elbows down.",
        "Feet a little wider than your hips, toes slightly out.",
        "Sit down between your knees, chest up, as low as your back stays flat.",
        "Drive through your whole foot to stand.",
        watch="Heels lifting, knees falling in, or the lower back rounding.",
        page="362/goblet-squat/", source="goblet squat"),
    "front-squat": _guide(
        "Rest a dumbbell on the front of each shoulder, chest up, middle braced.",
        "Feet slightly wider than your hips.",
        "Hips back and down, back flat, until your thighs are about level.",
        "Push up through your heels, hips and chest rising together.",
        watch="Elbows dropping, chest tipping forward, or heels lifting. Stop before they do.",
        page="22/front-squat/", source="front squat"),
    "air-squat": _guide(
        "Feet a little wider than your hips, arms out in front.",
        "Hips back and down, chest up, for about three seconds.",
        "Keep your knees in line with your toes.",
        "Stand up quickly and squeeze at the top.",
        watch="Heels lifting or knees falling in.",
        page="135/bodyweight-squat/", source="bodyweight squat"),
    "romanian-deadlift": _guide(
        "Stand tall, dumbbells in front of your thighs, knees soft.",
        "Push your hips straight back; the dumbbells slide down your legs.",
        "Stop when your hamstrings are tight, around mid-shin, back flat.",
        "Push your hips forward to stand.",
        watch="Rounding your back, or bending the knees into a squat."),
    "deadlift": _guide(
        "Dumbbells on the floor beside your feet, feet hip-width.",
        "Hips back, knees bent, back flat, and take hold.",
        "Brace, then push the floor away, dumbbells close to your legs.",
        "Stand tall, then lower the same way.",
        watch="A rounded back, or hips shooting up before the chest.",
        page="6/deadlift/", source="deadlift (with a barbell)"),
    "one-leg-deadlift": _guide(
        "Stand on one leg, knee soft, dumbbells in front.",
        "Tip forward from the hip as the other leg goes back.",
        "Stop when your back is nearly level, hips square to the floor.",
        "Squeeze the standing side to come up.",
        watch="Twisting the hips open. Touch a toe down for balance."),
    "glute-bridge": _guide(
        "Lie on your back, knees bent, feet flat and close to you.",
        "Press through your heels and lift your hips.",
        "Squeeze for a second, body straight from knee to shoulder.",
        "Lower slowly.",
        watch="Arching the lower back instead of squeezing the glutes.",
        page="145/glute-bridge-single-leg-progression/", source="single-leg glute bridge"),
    "split-squat": _guide(
        "Long stance, one foot in front, back heel up, dumbbells at your sides.",
        "Drop your back knee straight down, body upright.",
        "Stop just above the floor.",
        "Push up through the front foot.",
        watch="The front knee falling in, or a stance too short to balance.",
        page="366/bulgarian-split-squat/", source="split squat, back foot raised"),
    "step-up": _guide(
        "Step flat and steady, dumbbells at your sides.",
        "Put your whole foot on the step.",
        "Lean in a little and stand up on that leg.",
        "Lower under control, same leg.",
        watch="Bouncing off the back foot, or the step sliding: check its feet first."),
    "bodyweight-lunge": _guide(
        "Stand tall, feet hip-width.",
        "Step one foot back and drop that knee toward the floor.",
        "Keep your front foot flat and chest up.",
        "Push through the front foot to stand.",
        watch="A narrow stance that wobbles, or the front knee falling in.",
        page="94/forward-lunge/", source="forward lunge"),
    "floor-press": _guide(
        "Sit with the dumbbells on your thighs, then lie back with them at your chest.",
        "Knees bent, upper arms on the floor, elbows about 45 degrees out.",
        "Press up until your arms are straight.",
        "Lower until your upper arms touch the floor, and pause.",
        watch="Elbows flared wide, or wrists bending back.",
        page="19/chest-press/", source="chest press (on a bench)"),
    "step-press": _guide(
        "Back rest flat and locked. Lie with head and back supported, feet planted.",
        "Dumbbells at your chest, elbows about 45 degrees out, shoulder blades back.",
        "Press up without locking hard.",
        "Lower to chest height under control.",
        watch="Feet lifting, or weight beyond the step's limit.",
        page="19/chest-press/", source="chest press"),
    "tower-push-up": _guide(
        "Hands on the tower's handles, feet back, body in one line.",
        "Lower until your chest is level with your hands.",
        "Elbows about 45 degrees from your body.",
        "Press up, middle tight.",
        watch="Hips sagging, or going lower than is comfortable for your shoulders.",
        page="13/bent-knee-push-up/", source="push-up, knees down"),
    "push-up": _guide(
        "Hands a little wider than your shoulders, body in one line.",
        "Lower your chest toward the floor, elbows about 45 degrees out.",
        "Press up, middle tight.",
        "Knees down if your body stops being straight.",
        watch="Hips sagging or neck poking forward.",
        page="13/bent-knee-push-up/", source="push-up, knees down"),
    "feet-up-push-up": _guide(
        "Feet on the step, hands on the floor, body in one line.",
        "Lower your chest, elbows about 45 degrees out.",
        "Keep your middle tight so your hips stay level.",
        "Press up.",
        watch="Hips sagging, or the step sliding."),
    "slow-push-up": _guide(
        "Set up as for a push-up.",
        "Lower for three seconds.",
        "Pause just above the floor.",
        "Press up smoothly.",
        watch="Rushing the last part, or hips sagging."),
    "shoulder-press": _guide(
        "Stand tall, dumbbells at your shoulders, middle and glutes tight.",
        "Press up until your arms are straight by your ears.",
        "Keep your ribs down.",
        "Lower to your shoulders under control.",
        watch="Leaning back. If you must, the weight is too heavy."),
    "seated-press": _guide(
        "Back rest up and locked. Sit against it, feet planted.",
        "Dumbbells at your shoulders.",
        "Press up until your arms are straight.",
        "Lower under control.",
        watch="Lower back arching off the rest, or weight beyond the step's limit."),
    "pike-push-up": _guide(
        "Hands and feet on the floor, hips high, in an upside-down V.",
        "Bend your elbows and lower your head toward the floor.",
        "Keep your hips high.",
        "Press back up.",
        watch="Turning it into a flat push-up, or landing on your head."),
    "one-arm-row": _guide(
        "Split stance, tip forward, free hand braced on your front knee.",
        "Back flat, dumbbell hanging under your shoulder.",
        "Pull it to your hip, elbow close.",
        "Lower slowly.",
        watch="Twisting to heave it up."),
    "bent-row": _guide(
        "Hips back, knees soft, back flat and close to level.",
        "Dumbbells hanging under your shoulders.",
        "Pull to your ribs and squeeze your shoulder blades.",
        "Lower slowly.",
        watch="Standing up as you pull, or rounding your back.",
        page="12/bent-over-row/", source="bent-over row (with a barbell)"),
    "supported-row": _guide(
        "Back rest raised and locked. Lie chest down on it, feet planted.",
        "Dumbbells hanging straight down.",
        "Pull to your ribs and squeeze your shoulder blades.",
        "Lower slowly.",
        watch="Lifting your chest to cheat, or weight beyond the step's limit."),
    "prone-raise": _guide(
        "Lie face down, arms overhead in a Y, thumbs up.",
        "Lift your arms a little, shoulder blades down and back.",
        "Hold for a second.",
        "Lower slowly.",
        watch="Shrugging, or lifting your chest instead of your arms."),
    "knee-raise": _guide(
        "Back against the tower, forearms on its pads.",
        "Curl your hips up and bring your knees to your chest.",
        "Pause.",
        "Lower slowly until your legs hang still.",
        watch="Swinging, or sinking into your shoulders."),
    "leg-raise": _guide(
        "Back against the tower, forearms on its pads.",
        "Legs nearly straight, lift them to hip height or above.",
        "Pause.",
        "Lower slowly.",
        watch="Swinging, or the lower back arching away. Bend your knees if it does."),
    "slow-leg-raise": _guide(
        "Back against the tower, forearms on its pads.",
        "Lift straight legs to hip height or above.",
        "Lower for three seconds.",
        "Stop fully before the next.",
        watch="Dropping the last part."),
    "dead-bug": _guide(
        "On your back, arms up, knees over hips.",
        "Press your lower back into the floor.",
        "Lower one arm and the opposite leg toward the floor.",
        "Come back, then the other side.",
        watch="Your lower back lifting. Go only as low as it stays down."),
    "plank": _guide(
        "Forearms on the floor, elbows under your shoulders.",
        "Legs back, body in one line from head to heels.",
        "Squeeze your glutes, brace your middle, breathe.",
        "Stop when your hips drop.",
        watch="Hips sagging or piking up.",
        page="32/front-plank/", source="plank"),
    "curl": _guide(
        "Stand tall, dumbbells at your sides, palms forward.",
        "Curl up with your elbows still.",
        "Squeeze at the top.",
        "Lower for two seconds.",
        watch="Swinging, or elbows drifting forward.",
        page="44/seated-biceps-curl/", source="biceps curl, seated"),
    "triceps-press": _guide(
        "Lie on your back, dumbbells over your shoulders, palms facing.",
        "Bend only your elbows; lower beside your head.",
        "Keep your upper arms still.",
        "Straighten your arms.",
        watch="Elbows flaring, or dumbbells over your face: keep them beside it.",
        page="36/lying-barbell-triceps-extensions/", source="lying triceps (with a barbell)"),
    "lateral-raise": _guide(
        "Stand tall, dumbbells at your sides, elbows slightly bent.",
        "Lift out to the sides, leading with your elbows.",
        "Stop at shoulder height.",
        "Lower slowly.",
        watch="Shrugging or swinging. Go lighter."),
    "pull-up": _guide(
        "Hang with hands a little wider than your shoulders.",
        "Pull your shoulder blades down, then your chest toward the bar.",
        "Chin over the bar.",
        "Lower slowly all the way. A small jump up is fine.",
        watch="Kicking, or stopping halfway down.",
        page="191/pull-ups/", source="pull-ups"),
    "rower": _guide(
        "Straps over the widest part of your feet. Sit tall, arms straight.",
        "Push with your legs first.",
        "Then swing your back, then pull the handle to your lower ribs.",
        "Go back in the opposite order: arms, back, legs.",
        watch="Pulling with the arms early, or a rounded back."),
    "bike": _guide(
        "Saddle about hip height when you stand beside it.",
        "At the bottom of the pedal stroke your knee is slightly bent.",
        "Handlebars where your shoulders stay relaxed.",
        "Push smoothly, hips still.",
        watch="Hips rocking (saddle too high) or sore knees (often too low)."),
    "treadmill": _guide(
        "Start slow, clip the safety cord on.",
        "Stand tall, eyes forward, shoulders relaxed.",
        "Short, quick steps under your hips.",
        "Slow down for a minute before stopping.",
        watch="Holding the rails, or stepping off while it moves."),
    "circuit": _guide(
        "30 seconds each: squats, push-ups, lunges, plank.",
        "Move straight to the next; rest only between rounds if you need to.",
        "Keep every rep tidy, even when tired.",
        "Ease off if your form goes.",
        watch="Speed over form."),
    "flow": _guide(
        "Ease into each stretch; mild tension, never pain.",
        "30 seconds each side: hips, hamstrings.",
        "Then chest and upper back.",
        "Breathe slowly throughout.",
        watch="Bouncing, or holding your breath."),
}


def guide_for(key: str) -> Guide | None:
    """Return the guide for an exercise or a session's item (``finisher-bike``, ``rower#2``)."""
    return GUIDES.get(key.split("#")[0].removeprefix("finisher-"))
