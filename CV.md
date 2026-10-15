# Computer vision: what we tried and what worked (v10)

The night before the hackathon (15 to 16 October 2026). Pilot area: tile BA31_1000_4245,
Mt Albert, the 103 property boundaries a flow path crosses in it.

## The problem

v8 sorted each boundary from the laser alone: the share of standing points the surveyor
left "unclassified" (a fence or wall lands there; a hedge lands in vegetation). It found
most fences but called too much planting a fence: 9 of its 28 fence calls on the
development set were wrong. And a fence buried in a hedge read as hedge.

Healthy Waters' brief says their own machine learning on aerial photos, using shadows,
didn't work. So the laser says where something stands, and the photo only helps say what
it is.

## Evidence for every line

`make_chips.py` draws each line turned to run left to right, 4 m either side: the 2024
7.5 cm aerial photo, Council's colour infrared photo, the laser points from above and the
laser points from the side (`mtalbert/chips/`). These are what a reviewer looks at, and
what the labels below were made from.

## Labels

- **Human checks** (`mtalbert/checks/`): 9 lines checked by eye with the aerial photo,
  Street View and 3D, in `check.html`. Street View sees fences under hedges, which nothing
  from the air can. These are the test. They are too few; 40 more are planned.
- **AI reference labels** (`mtalbert/reference/claude_labels.csv`): all 101 lines labelled
  by Claude from the evidence pictures, with a main and a second structure, a "how sure"
  and a note: 25 fence, 26 hedge, 30 trees, 11 nothing, 3 building, 4 unknown, 1 gate,
  1 low kerb. They are AI-made and unchecked, and see only what shows from above, so they
  are used to fit and develop, never as the score. The 9 human-checked lines were labelled
  after seeing the human answers and are left out of the fit.

## Measurements tried (`cv_features.py`)

How well each separates built from planting on the AI labels (area under the curve; 0.5 is
chance, 1 perfect, below 0.5 means the reverse):

| Measurement | Source | AUC |
| --- | --- | --- |
| Share of standing points unclassified | laser | 0.92 |
| Straight edge: the sharpest edge within 1.5 m that holds along the line | photo | 0.84 |
| Thin: share of standing points within 20 cm of their middle | laser | 0.83 |
| Leafy on the line (near infrared against green) | infrared | 0.19 (0.81 reversed) |
| Greenness on the edge (2G - R - B) | photo | 0.19 (0.81 reversed) |
| Laser line and photo edge in the same place | both | 0.20 (0.80 reversed) |
| Edge continuity (share of 3 m pieces where it holds) | photo | 0.79 |
| Texture | photo | 0.47 (no use) |

Dropped earlier (v8, v9): infrared at 15 cm sampled around each point (too coarse: it
picked up the lawn beside a fence), laser brightness, returns per pulse, depth across the
line, and the 2016 survey's classes. None separated the hand checks.

## The model (`classify_cv.py`)

A logistic regression on three measurements, unclassified share, thin and log(edge), gives
a chance of built. Two things made it work:

1. **Pieces.** Each line is judged in pieces about 6 m long, and is a fence where any piece
   reaches 80%. Whole-line scores missed fences that show only where the planting thins out.
2. **Building outlines.** LINZ outlines are left out: photo columns where a roof (outline
   plus 0.5 m eaves) comes within 0.6 m of the line, and laser points inside an outline.
   Roof edges make the sharpest straight edges in the photo. A wider mask (any building
   within 1.8 m) also hid fences that run beside houses, so it was narrowed.

Each line is scored by a model fitted without it.

## Results

| | Human checks (9) | AI labels (76, fitted without each) |
| --- | --- | --- |
| v8 (laser share only) | 6 right, 2 can't tell, 1 wrong | 19 of 23 fences found; 9 of 28 fence calls wrong |
| Whole-line model | 5 right, 1 can't tell, 3 wrong | 15 of 23 found; 0 of 15 wrong |
| **v10: 6 m pieces, best piece >= 80%** | **9 right, 6 of 6 fences found** | **26 of 31 lines with a fence found (84%); 9 of 35 calls wrong (26%)** |
| v10, high confidence (best piece >= 95%) | | 3 of 23 calls wrong (13%) |

The memo's proposed targets are 80% found and 15% or less wrong at high confidence. Both
are met on the AI labels, which is a development check, not the test.

## Caveats

- 9 human checks is too few for a score. And the building mask was narrowed after seeing
  one of them (a wall beside a house), so 9 of 9 is optimistic.
- The AI labels and the method both look from above, so they share blind spots.
- On lines 4 m off every boundary it calls a fence on 50 of 202 (25%). Some are real
  (garden fences, sheds, retaining walls); that hasn't been checked.
- Cars on driveways read as built (unclassified, sharp edges).
- Fence and wall can't be told apart, and nothing here says solid or see-through.

## Next

- 40 more human checks from `check.html`, then rerun `classify_cv.py`: it scores against
  every CSV in `mtalbert/checks/`.
- Check a sample of the off-boundary fence calls by eye.
- Run the flow model with every detected fence standing, against bare ground plus
  buildings (the memo's terrain-only baseline).
- Look past the parcel boundaries: the same pieces along any straight run of standing
  points near a flow path.
