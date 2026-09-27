# Enemy priority labels

FGA's unmodified English `target_danger.png` and `target_servant.png` are
matched only in fixed 1280x720 screen-left/middle/right 250px HUD regions. The
result is an optional `danger` or `servant` label only when the existing
standard-three presence guard has already established a living enemy with a
positive HP reading. The observer retains template scores and source evidence;
the planner receives only the compact label.

FGA's `AutoChooseTarget` scans broad per-enemy top-screen regions and treats
either template as a priority cue. This implementation intentionally does not
reuse its retarget behavior. Original FGO-py's `fgoDetect.py` has no equivalent
Danger/Servant label detector; its enemy behavior is HP/charge based.

No match, missing templates, unguarded enemies, wrong resolution, and six-grid
layouts all yield `None`. That is unknown evidence, not proof that an enemy is
ordinary. The synthetic tests use generated composites and do not validate a
real boss encounter.
