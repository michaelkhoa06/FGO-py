"""NA standard-three-slot target highlight, independent of enemy animations.

Detect the cyan outline around the selected enemy's class diamond in the HUD.
Only a unique, spatially anchored outline on a known living enemy is actionable.
Unknown layouts and modal/card screens never authorize a target tap.
"""
from __future__ import annotations


def guard_enemy_presence(image, enemies, *, grid):
    """Keep OCR evidence but withhold actionable HP without a class HUD outline.

    An empty background can OCR as a valid integer. A missing outline means
    unknown presence, not a claim that an enemy is dead (overlays also hide it).
    """
    import cv2
    import numpy as np
    if grid != 0 or image.shape[:2] != (720, 1280):
        return enemies
    yy, xx = np.indices((80, 85))
    distance = np.abs(xx-44) + np.abs(yy-42)
    outline = (distance >= 29) & (distance <= 39)
    center = distance < 24
    quadrants = [(xx<44)&(yy<42), (xx>=44)&(yy<42),
                 (xx<44)&(yy>=42), (xx>=44)&(yy>=42)]
    guarded = []
    for enemy in enemies:
        item = dict(enemy)
        slot = item.get('slot')
        if type(slot) is not int or slot not in (1,2,3):
            item.update(hp_ocr=item.get('hp'), hp=None, present=None)
        else:
            hsv = cv2.cvtColor(image[:80,(slot-1)*250:(slot-1)*250+85], cv2.COLOR_BGR2HSV)
            # Bronze, silver and gold enemy ranks share geometry, not color.
            # Require four bright border segments and some dark icon background.
            # Large class symbols (e.g. Assassin's skull) fill much of the center.
            border = (hsv[:,:,1] < 210) & (hsv[:,:,2] > 115)
            coverage = [float(border[outline&q].mean()) for q in quadrants]
            present = min(coverage) >= .25 and float(border[center].mean()) < .85
            item.update(present=True if present else None,
                        presence_evidence={'method':'standard NA class-diamond bright outline and dark center',
                                           'quadrant_coverage':coverage}, hp_ocr=item.get('hp'))
            if not present:
                item['hp'] = None
        guarded.append(item)
    return guarded


def target_signals(image, enemies, *, phase, grid):
    import cv2
    import numpy as np

    result = {"enemy_layout": "standard3" if grid == 0 else "unsupported",
              "selected_enemy": None, "enemy_target_verifiable": False,
              "enemy_target_method": "cyan class-diamond outline in standard NA enemy HUD"}
    if phase != "tactical" or grid != 0 or image.shape[:2] != (720, 1280):
        return result
    # An anchored diamond, rather than blue pixel count, rejects blue scenery.
    # HUD center and vertices are shared across class symbols and enemy sprites.
    yy, xx = np.indices((80, 85))
    distance = np.abs(xx - 43) + np.abs(yy - 39)
    outline = (distance >= 34) & (distance <= 43)
    inner = distance < 25
    quadrants = [(xx < 43) & (yy < 39), (xx >= 43) & (yy < 39),
                 (xx < 43) & (yy >= 39), (xx >= 43) & (yy >= 39)]
    scores, candidates = [], []
    for slot in range(1, 4):
        hsv = cv2.cvtColor(image[:80, (slot-1)*250:(slot-1)*250+85], cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, np.array([90, 100, 140]), np.array([115, 255, 255])) > 0
        coverage = [float(blue[outline & q].mean()) for q in quadrants]
        inner_fraction = float(blue[inner].mean())
        # The class label overlaps the lower-right outline. Require both upper
        # edges and one lower edge, while rejecting a filled blue background.
        matched = (min(coverage[:2]) >= .35 and max(coverage[2:]) >= .18
                   and sum(coverage)/4 >= .32 and inner_fraction < .12)
        scores.append({"slot": slot, "quadrant_coverage": coverage,
                       "inner_blue_fraction": inner_fraction, "matched": matched})
        if matched:
            candidates.append(slot)
    result["enemy_target_scores"] = scores
    if len(candidates) == 1:
        chosen = candidates[0]
        enemy = next((e for e in enemies if e.get("slot") == chosen), None)
        if enemy and type(enemy.get("hp")) is int and enemy["hp"] > 0:
            result.update(selected_enemy=chosen, enemy_target_verifiable=True)
    return result
