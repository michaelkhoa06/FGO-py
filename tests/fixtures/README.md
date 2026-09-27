# NA visual regression fixture

`na-tactical-turn1.png` is a normalized 1280x720 capture from the user's MuMu
instance on 2026-09-21. It was manually inspected before asserting these values:
wave 1/3, turn 1, three enemies at 1428 HP and 0/4 charge; allies at
13611/14081/14837 HP and 13/0/75 percent NP, with all nine skills available.
The visible lineup is support Koyanskaya of Light, Koyanskaya of Light, and Arjuna
(Alter). This fixture checks a single ordinary tactical screen, not arbitrary
story encounters, cooldown readings, or card screens. Game artwork belongs to its
respective rights holders; this is a local development regression capture.

`na-tactical-after-skill.png` and `na-tactical-after-skill-second.png` are distinct
post-action frames 13 and 16 from session `20260921T214517Z-dafb6ecf`. Arjuna's
second skill was tapped exactly once. The frames visibly show cooldown 5 and NP
100. The original live run reported **uncertain** before the cooldown crop was
corrected. An offline replay test reprocesses these images with the corrected
detector; it does not turn that original run into a claimed live controller pass.

`na-target-selection.png` is frame 4 from session `20260921T214750Z-8be0e776`.
The first targeted attempt stopped uncertain because the target dialog's still-
visible Attack button was classified as tactical. Modal recognition now has
priority. This regression prevents interpreting dimmed background skill icons as
consumed skills. The pending skill had not yet been targeted in this capture.

`na-enemy-selected-1.png` is frame 3 from `20260922T003053Z-46d98364`;
`na-enemy-selected-2.png` and `na-enemy-selected-3.png` are frames 3 and 6 from
`20260922T003228Z-97af4536`. These are unmodified, visually checked tactical frames
after target taps. Screen-left/middle/right class HUDs visibly have the selected
cyan diamond, respectively. The enemy lineup is Archer/Archer/Caster.

`na-enemy-removed-phantom-hp.png` is frame 39 from the interrupted model run
`20260922T003623Z-79305fbb`. Only screen-left Archer remains, at 724 HP. The
screen-right background falsely OCRed as 3 HP, motivating the independent HUD
presence guard. It must not create a target for that empty slot. Original bronze
Lancer/Assassin fixtures are also retained to prevent a silver-only HUD detector.
