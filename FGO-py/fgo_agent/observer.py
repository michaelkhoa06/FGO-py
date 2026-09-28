"""One-frame NA observations using FGO-py's templates and OCR.

The legacy recursive retry/capture decorators are deliberately bypassed. Every
value in an observation comes from its saved frame, and failures stay unknown.
No legacy battle loop is imported or invoked.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path
import re
import time

from .models import Observation

# The NA "N Turn(s)" counter. The number is left-aligned at x~853 and a second
# digit ends near x=877; "Turn(s)" starts at x~882 after one digit, x~890 after
# two. A narrower crop read turn 10 as 1, so the turn seemed not to advance.
_TURN_COUNTER = (846, 81, 880, 111)

# NA "Stunned" overlay on a stunned servant's command and NP cards. Upstream's
# CHARASEALED template is the CN text, so it never matched NA (seen live: a stunned
# Castoria's cards were picked and the taps did nothing). Cut from a live frame;
# stunned cards score >= 0.8 and 7,400+ other frames at most 0.4.
_STUNNED_TEMPLATE = Path(__file__).parent / "assets" / "cards" / "stunned.png"
_STUNNED_MATCH = 0.7
_STUNNED_TEMPLATE_IMAGE = None


def _stunned(image, region) -> bool:
    import cv2
    global _STUNNED_TEMPLATE_IMAGE
    if _STUNNED_TEMPLATE_IMAGE is None:
        _STUNNED_TEMPLATE_IMAGE = cv2.imread(str(_STUNNED_TEMPLATE))
    x0, y0, x1, y1 = region
    return float(cv2.matchTemplate(image[y0:y1, x0:x1], _STUNNED_TEMPLATE_IMAGE,
                                   cv2.TM_CCOEFF_NORMED).max()) >= _STUNNED_MATCH


# NA "NP Sealed" label on an NP card (upstream HOUGUSEALED is CN text). Cut from a
# live frame, 2026-09-27: sealed cards scored >= 0.76, 268 other card screens
# (ready, stunned and absent NPs) <= 0.39. The seal was applied by the enemy's
# attack, after the ally's Status tab had been read, so only the card showed it.
# Same place, NA "Immobilized" (sleep; Zzz icon), cut live 2026-09-27 (Oberon):
# Tezcatlipoca's NP was tapped, nothing happened, and the plan repeated the chain.
# Immobilized cards scored >= 0.82 (one transition frame 0.67), others <= 0.33.
_NP_LABEL_TEMPLATES = [Path(__file__).parent / "assets" / "cards" / name
                       for name in ("np_sealed.png", "np_immobilized.png")]
_NP_SEALED_MATCH = 0.6
_NP_LABEL_IMAGES = None


def _np_sealed_label(image, index: int) -> bool:
    """An NP card carrying a label that makes it unusable ("NP Sealed", "Immobilized")."""
    import cv2
    global _NP_LABEL_IMAGES
    if _NP_LABEL_IMAGES is None:
        _NP_LABEL_IMAGES = [cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in _NP_LABEL_TEMPLATES]
    x = 431 + 220*index
    region = cv2.cvtColor(image[172:245, x-130:x+140], cv2.COLOR_BGR2GRAY)
    return any(float(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED).max()) >= _NP_SEALED_MATCH
               for template in _NP_LABEL_IMAGES)


# A multi-bar enemy shows one diamond per HP bar under its name, left of the HP
# number: lit (purple, blue) for bars still to come, dark once used. Measured on
# the standard 3-enemy row, 2026-09-27: centres 14 px left of the HP box, 17.5 px
# apart, y 62. Barghest showed 2 lit on bar 1, 1 lit on bar 2, 0 on its last bar;
# the agent had broken bar 2 thinking the fight was won.
_BAR_DIAMOND_X, _BAR_DIAMOND_PITCH, _BAR_DIAMOND_Y = -14, 17.5, 62


def _bars_remaining(image, index: int) -> int:
    """Lit bar diamonds under a standard-row enemy: HP bars left after this one."""
    import cv2
    import numpy
    def colour(x):
        patch = cv2.cvtColor(image[_BAR_DIAMOND_Y-1:_BAR_DIAMOND_Y+2, x-1:x+2], cv2.COLOR_BGR2HSV).reshape(-1, 3)
        hues = patch[:, 0].astype(int)
        # Hue wraps at 180: a red diamond mixes 0-5 and 175-179.
        spread = min(numpy.ptp(hues), numpy.ptp((hues + 90) % 180))
        return [float(numpy.median(patch[:, c])) for c in range(3)], float(spread)

    count = 0
    for k in range(6):
        x = int(round(100 + 250*index + _BAR_DIAMOND_X + _BAR_DIAMOND_PITCH*k))
        (hue, sat, val), spread = colour(x)
        # Any saturated colour: U-Olga Marie's five bars ran purple, blue, green,
        # yellow, orange (2026-09-27). Backgrounds are rejected by the rim check.
        if not (sat >= 120 and val >= 120 and spread <= 20):
            break
        # A diamond has a dark gold rim just past its side corners. Seen live
        # (Cernunnos): a pink sky next to three diamonds read as two more.
        sides = [colour(x + dx)[0] for dx in (-8, 8)]
        if any(abs(h - hue) < 20 and s >= 0.6*sat and v >= 0.75*val for h, s, v in sides):
            break
        count += 1
    return count


# NA "Turn(s)" label printed under a skill icon while it cools down. Averaged from
# 60 live cooling icons (min-channel image, so white text stands out on any icon
# art). Live 2026-09-24: ready icons scored <= 0.41, cooling ones >= 0.76.
_COOLDOWN_LABEL_TEMPLATE = Path(__file__).parent / "assets" / "skills" / "cooldown_turns.png"
_COOLDOWN_LABEL_PRESENT, _COOLDOWN_LABEL_ABSENT = 0.6, 0.5
_COOLDOWN_LABEL_IMAGE = None


def _cooldown_label_score(crop) -> float | None:
    """How strongly a skill's label area shows "Turn(s)"; crop is BGR (offset+24..86, y596..622)."""
    import cv2
    import numpy as np
    global _COOLDOWN_LABEL_IMAGE
    if _COOLDOWN_LABEL_IMAGE is None:
        _COOLDOWN_LABEL_IMAGE = cv2.imread(str(_COOLDOWN_LABEL_TEMPLATE), cv2.IMREAD_GRAYSCALE)
    if crop is None or crop.size == 0 or crop.ndim != 3:
        return None
    window = crop.min(axis=2)
    if window.shape[0] < _COOLDOWN_LABEL_IMAGE.shape[0] or window.shape[1] < _COOLDOWN_LABEL_IMAGE.shape[1]:
        return None
    score = float(cv2.matchTemplate(window, _COOLDOWN_LABEL_IMAGE, cv2.TM_CCOEFF_NORMED).max())
    return score if np.isfinite(score) else None


def strict_integer(text: str, maximum: int = 9_999_999) -> int | None:
    """Parse a displayed integer, rejecting OCR punctuation and mixed text."""
    text = str(text).strip()
    if not re.fullmatch(r"(?:0|[1-9]\d{0,2}(?:,\d{3})*|[1-9]\d*)%?", text):
        return None
    value = int(text.rstrip("%").replace(",", ""))
    return value if value <= maximum else None


def strict_integer_consensus(readings, maximum: int = 9_999_999) -> int | None:
    """Return a value only when multiple strict OCR readings agree exactly.

    This deliberately does not repair punctuation.  Callers use it only after a
    full-crop OCR result failed strict parsing, so a malformed reading cannot
    become a value unless distinct preprocessed images each parse strictly.
    """
    values = [strict_integer(text, maximum) for text in readings]
    values = [value for value in values if value is not None]
    return values[0] if len(values) >= 2 and len(set(values)) == 1 else None


def corroborated_integer(raw_text: str, readings, maximum: int = 9_999_999) -> int | None:
    """Accept a consensus only when the original OCR preserves its digit sequence."""
    value = strict_integer_consensus(readings, maximum)
    raw_digits = "".join(re.findall(r"\d", str(raw_text)))
    return value if value is not None and raw_digits == str(value) else None


def _pixels_key(image) -> tuple:
    return (image.shape, hashlib.blake2b(image.tobytes(), digest_size=16).digest())


class _MemoOcr:
    """OCR that answers again from memory when the crop's pixels are identical.

    OCR is deterministic, so identical pixels give identical text. Between two
    tactical frames most HP, NP and turn digits have not changed; re-reading them
    cost about 1 s of every 1.9 s observation (profiled 2026-09-25).
    """

    _LIMIT = 4096

    def __init__(self):
        self.engine = None
        self.memory: dict[tuple, object] = {}

    def ocrText(self, image):
        key = _pixels_key(image)
        if key not in self.memory:
            if len(self.memory) >= self._LIMIT:
                self.memory.clear()
            self.memory[key] = self.engine.ocrText(image)
        return self.memory[key]


class LegacyObserver:
    def __init__(self, directory: Path, *, detector_module=None, cooldown_ocr: bool = False):
        if detector_module is None:
            # fgoAgent sets cwd to the upstream asset directory before this import.
            import fgoDetect as detector_module
        self.module = detector_module
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index = 0
        self.cooldown_ocr = cooldown_ocr
        self.last_tactical_allies = []
        self.last_tactical_context = None
        self._servant_index = None
        self._name_identities: dict[tuple, tuple] = {}
        self._memo_ocr = _MemoOcr()
        self._last_ally_hp: dict[int, tuple] = {}
        self._last_ally_np: dict[int, tuple] = {}
        self._enemy_classes: dict[tuple, str | None] = {}
        self._identities: dict[tuple, tuple] = {}

    @staticmethod
    def _hp_fraction(image, index: int) -> float | None:
        """Fraction of the ally HP bar that is filled, read from colour, not digits.

        The bar fills from the left in saturated blue, or red when low. Measured on
        live frames: a full bar fills 134 px; 880 HP of about 15,000 filled 6 px.
        """
        import cv2
        # The bar starts at x=161 in slot 1 but one pixel later in slots 2 and 3,
        # so count filled columns over a slightly wider window, not a run.
        x0 = 156 + 317 * index
        band = cv2.cvtColor(image[644:651, x0:x0 + 146], cv2.COLOR_BGR2HSV)
        filled = int((((band[..., 1] > 90) & (band[..., 2] > 120)).mean(axis=0) > 0.5).sum())
        return round(min(1.0, filled / 134), 3)

    def observe(self, image) -> Observation:
        import cv2
        import numpy as np
        captured_at = datetime.now(timezone.utc).isoformat()
        self.index += 1
        observation_id = f"frame-{self.index:06d}"
        filename = self.directory / f"{observation_id}.png"
        if image.shape[:2] != (720, 1280):
            raise ValueError("Observer expects a normalized 1280x720 frame.")
        if not cv2.imwrite(str(filename), image):
            raise OSError(f"Could not save screenshot {filename}")
        detector = object.__new__(self.module.XDetectNA)
        detector.im, detector.time = image, time.time()
        if self._memo_ocr.engine is None:
            self._memo_ocr.engine = type(detector).ocr
        detector.ocr = self._memo_ocr
        errors: list[str] = []

        def read(name, *args):
            try:
                # Bypass both legacy retries and logging wrappers without taking another frame.
                value = inspect.unwrap(getattr(type(detector), name))(detector, *args)
                return value.item() if isinstance(value, np.generic) else value
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                return None

        numeric_repairs = []

        def number(rect, label, maximum=9_999_999, repair_variants=(), padded_variants=()):
            try:
                crop = detector._crop(rect)
                text = detector.ocr.ocrText(crop)
                value = strict_integer(text, maximum)
                if value is None and repair_variants:
                    # The outlined NA HP/NP font can turn a digit stroke into a
                    # punctuation mark.  Do not remove punctuation from that OCR
                    # text: independently OCR two contrast transforms and require
                    # both resulting *strict* integers to agree.
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    variants = []
                    for block_size, constant in repair_variants:
                        threshold = cv2.adaptiveThreshold(
                            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                            cv2.THRESH_BINARY, block_size, constant)
                        variant = cv2.cvtColor(threshold, cv2.COLOR_GRAY2BGR)
                        variants.append(detector.ocr.ocrText(variant))
                    candidate = strict_integer_consensus(variants, maximum)
                    repair = {"field": label, "full_crop": text,
                              "method": "adaptive-threshold consensus",
                              "variant_readings": variants}
                    if candidate is None and padded_variants:
                        padded_readings = []
                        for block_size, constant, border_value in padded_variants:
                            threshold = cv2.adaptiveThreshold(
                                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, block_size, constant)
                            variant = cv2.cvtColor(threshold, cv2.COLOR_GRAY2BGR)
                            variant = cv2.copyMakeBorder(
                                variant, 1, 1, 1, 1, cv2.BORDER_CONSTANT,
                                value=(border_value, border_value, border_value))
                            padded_readings.append(detector.ocr.ocrText(variant))
                        candidate = corroborated_integer(text, padded_readings, maximum)
                        repair["padded_fallback"] = {
                            "method": "one-pixel border adaptive-threshold consensus with raw digit corroboration",
                            "variant_readings": padded_readings,
                            "raw_digits": "".join(re.findall(r"\d", str(text))),
                        }
                    repair["accepted"] = candidate is not None
                    numeric_repairs.append(repair)
                    value = candidate
                if value is None:
                    errors.append(f"{label}: unreadable numeric text {text!r}")
                return value
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
                return None

        signals = {"sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
                   "region": "NA", "resolution": [1280, 720],
                   "skill_readiness_method": "NA STILL score plus icon brightness; heuristic, calibrated on one lineup",
                   "cooldown_ocr_enabled": self.cooldown_ocr, "numeric_repairs": numeric_repairs}
        phase = "unknown"
        target_count = None
        completion = self._completion_screen(image)
        if completion:
            phase = "result"
            signals["completion_evidence"] = completion
        for name, state in (("isNetworkError", "network_error"), ("isBattleDefeated", "defeat"),
                            ("isBattleContinue", "defeat"), ("isBattleFinished", "result"),
                            ("isSkillCastFailed", "skill_error"), ("isSkillNone", "skill_unavailable")):
            if phase == "unknown" and read(name) is True:
                phase = state
                break
        colors = []
        if phase in ("unknown", "skill_error", "skill_unavailable"):
            # The enemy and ally status dialogs carry a close X too; recognize them
            # before the unrecognized-modal refusal below. Upstream's isSkillNone
            # also fires on the ally dialog (seen live), so a recognized dialog wins.
            from .enemy_profile import read_status_dialog
            try:
                dialog = read_status_dialog(image, detector.ocr.ocrText)
            except Exception as exc:
                dialog = None
                errors.append(f"status dialog recognition: {type(exc).__name__}: {exc}")
            if dialog is not None and dialog["subject"] == "enemy":
                phase = "enemy_profile"
                signals["enemy_profile"] = {key: dialog[key] for key in
                                            ("name", "sub_attribute", "effects", "possibly_truncated", "scroll")}
            elif dialog is not None:
                phase = "ally_status"
                signals["ally_status"] = dialog
        if phase == "unknown":
            # The Order Change member screen also carries the generic close X, so
            # it must be recognized before the unrecognized-modal refusal below.
            from .order_change import read_order_change
            try:
                member_screen = read_order_change(image, detector.ocr.ocrText)
            except Exception as exc:
                member_screen = None
                errors.append(f"order change recognition: {type(exc).__name__}: {exc}")
            if member_screen is not None:
                phase = "order_change"
                signals["order_change"] = member_screen
        if phase == "unknown":
            # Attack remains visible behind target dialogs. Modal recognition must
            # win before tactical recognition, or dimmed background icons look spent.
            target_count = read("getSkillTargetCount")
            if target_count in (1, 2, 3):
                phase = "target_selection"
            elif detector._compare(detector.tmpl.CROSS, (980, 0, 1280, 300)):
                target_count = None
                errors.append("Unrecognized modal dialog; background battle controls are not actionable.")
            elif read("isTurnBegin") is True:
                target_count = None
                phase = "tactical"
                from .order_change import read_palette
                try:
                    palette = read_palette(image)
                except Exception as exc:
                    palette = None
                    errors.append(f"master palette recognition: {type(exc).__name__}: {exc}")
                if palette is not None:
                    signals["master_palette"] = palette
            else:
                target_count = None
                for i in range(5):
                    try:
                        colors.append(detector._select((detector.tmpl.ARTS, detector.tmpl.QUICK, detector.tmpl.BUSTER),
                                                       (80+257*i, 537, 131+257*i, 581)))
                    except Exception as exc:
                        colors.append(None)
                        errors.append(f"card {i+1}: {exc}")
                if sum(c is not None for c in colors) >= 3:
                    phase = "cards"
        allies, enemies, cards = [], [], []
        wave = turn = None
        if phase in ("tactical", "cards"):
            wave = number((884, 14, 902, 37), "wave", 99)
            if phase == "cards":
                # A ready NP portrait can overlap the NA turn counter.  Its OCR may
                # be malformed or, worse, a plausible digit from the portrait, so
                # card-screen OCR is evidence only and never authoritative.
                try:
                    card_turn_text = detector.ocr.ocrText(detector._crop((846, 81, 872, 111)))
                    raw_card_turn = strict_integer(card_turn_text, 999)
                except Exception as exc:
                    card_turn_text = None
                    raw_card_turn = None
                    errors.append(f"card turn OCR: {type(exc).__name__}: {exc}")
                signals["card_turn_ocr"] = {"text": card_turn_text, "parsed": raw_card_turn}
                if (self.last_tactical_context is not None and wave is not None
                        and wave == self.last_tactical_context[0]
                        and self.last_tactical_context[1] is not None):
                    turn = self.last_tactical_context[1]
                    signals["turn_source"] = "last tactical frame in the same wave; card counter obscured or occluded"
                else:
                    signals["turn_source"] = "unknown; card-screen counter is untrusted without same-wave tactical context"
            else:
                turn = number(_TURN_COUNTER, "turn", 999)
                # The crop is wide enough for two digits, so after one digit it can
                # catch the edge of "Turn(s)". Seen live: "7 Turn(s)" read as 71 after
                # turn 6. The counter only stays or advances by one, so a jump that
                # is the plausible value plus a stray trailing digit is repaired.
                previous = self.last_tactical_context[1] if self.last_tactical_context else None
                if (turn is not None and type(previous) is int and turn > previous + 1 and turn >= 10
                        and turn // 10 in (previous, previous + 1)):
                    signals["turn_repair"] = {"read": turn, "used": turn // 10, "previous": previous}
                    turn //= 10
            signals["stage_total"] = number((912, 13, 932, 38), "wave total", 99)
            if phase == "tactical":
                # "ENEMY Remaining : N". Neither FGA nor upstream reads it. It falls
                # when enemies die, so a horde respawn can be told from a boss HP
                # refill. OCR adds stray marks ("96)", "3.", "942"): at most 2 digits.
                try:
                    remaining_text = str(detector.ocr.ocrText(detector._crop((865, 45, 1005, 72))))
                    found = re.search(r":\s*(\d{1,2})", remaining_text)
                    signals["enemies_remaining"] = int(found.group(1)) if found else None
                except Exception as exc:
                    errors.append(f"enemies remaining: {type(exc).__name__}: {exc}")
            if phase == "tactical":
                for i in range(3):
                    hp = number((200+317*i, 620, 293+317*i, 644), f"ally {i+1} HP",
                                repair_variants=((7, 0), (7, 6)),
                                padded_variants=((7, 0, 0), (7, 6, 0)))
                    # Seen live: 16,922 read as 116,922 (and 15,417 as 115,417) on some
                    # frames, the crop's left edge adding a "1". A servant's HP is five
                    # digits at most, so a six-digit value led by 1 drops that digit.
                    if type(hp) is int and 100_000 <= hp < 200_000 and hp % 100_000 > 0:
                        signals.setdefault("hp_repairs", []).append({"slot": i + 1, "read": hp, "used": hp % 100_000})
                        hp %= 100_000
                    # The reverse also happens on bright backgrounds: 14,266 read as
                    # 4,266 on alternate frames. Within one turn, a reading equal to the
                    # previous one minus its first digit is that dropped digit.
                    previous_hp = self._last_ally_hp.get(i)
                    if (type(hp) is int and previous_hp is not None and previous_hp[0] == (wave, turn)
                            and len(str(previous_hp[1])) == len(str(hp)) + 1 and str(previous_hp[1])[1:] == str(hp)):
                        signals.setdefault("hp_repairs", []).append({"slot": i + 1, "read": hp, "used": previous_hp[1]})
                        hp = previous_hp[1]
                    if type(hp) is int:
                        self._last_ally_hp[i] = ((wave, turn), hp)
                    np_value = number((222+317*i, 653, 313+317*i, 681), f"ally {i+1} NP", 500,
                                      repair_variants=((7, 6), (9, 4)),
                                      padded_variants=((7, 6, 0), (7, 6, 255), (9, 4, 0)))
                    servant_id, match_score = self._servant_identity(detector, i, errors)
                    hp_fraction = self._hp_fraction(image, i)
                    identity_source, name_label = ("portrait" if servant_id is not None else None), None
                    # An empty (dead) slot shows no NP text, so a readable NP gauge also
                    # proves a servant is standing there. Seen live: a guest at 1 HP had
                    # an empty bar and unreadable digits, and was treated as dead for six
                    # turns with 300% NP and ready skills hidden.
                    present = hp_fraction or np_value is not None
                    if servant_id is None and (hp is not None or present):
                        servant_id, identity_source, name_label = self._identity_from_name(detector, i, errors)
                    # HP text that is present but unparseable (seen live: "1 15,417"
                    # for 15,417) still proves a living, identified servant. Keep the
                    # number unknown, but do not let the misread disable the servant.
                    alive = hp is not None and hp > 0
                    if not alive and hp is None and (servant_id is not None or name_label):
                        # Seen live: a real 880 HP read as ".880"/"1.880" and was
                        # discarded, which hid the servant's skills for four turns.
                        # A filled HP bar or a readable NP gauge proves a living
                        # servant without the digits.
                        alive = bool(present)
                    # Seen live (Red Dragon): the re-plan frame after a death read every
                    # NP as unknown, so the plan could not see two full gauges. Within
                    # one turn, the same servant's last readable NP stands in.
                    previous_np = self._last_ally_np.get(i)
                    if (np_value is None and alive and servant_id is not None and previous_np is not None
                            and previous_np[0] == (wave, turn, servant_id)):
                        np_value = previous_np[1]
                        signals.setdefault("np_carried", []).append(i + 1)
                    elif type(np_value) is int and servant_id is not None:
                        self._last_ally_np[i] = ((wave, turn, servant_id), np_value)
                    skills = []
                    for j in range(3):
                        readiness = self._skill_readiness(detector, i, j) if alive else {"ready": None}
                        ready = readiness["ready"]
                        cooldown = None
                        if self.cooldown_ocr and ready is False:
                            cooldown = number((86+318*i+88*j, 588, 112+318*i+88*j, 619), f"skill {i+1}.{j+1} cooldown", 30)
                            if cooldown == 0:
                                # A dark icon always shows at least 1. Seen live: a
                                # cooldown of 10 lost its leading digit in this narrow
                                # crop and read as 0, so 0 is a misread, not a value.
                                errors.append(f"skill {i+1}.{j+1} cooldown: impossible 0 on an unavailable icon")
                                cooldown = None
                        skills.append(dict(slot=j+1, **readiness, cooldown=cooldown))
                    allies.append(dict(slot=i+1, id=servant_id, identity_match_score=match_score,
                                       identity_source=identity_source, name_label=name_label,
                                       status_icons=self._status_icon_signature(image, i),
                                       hp=hp, hp_fraction=hp_fraction, alive=alive, np=np_value, skills=skills))
                self.last_tactical_allies = allies
                self.last_tactical_context = (wave, turn)
            grid = read("setupEnemyGird")
            # Upstream picks the 6-enemy grid when any small class icon scores under
            # 0.2. Seen live: Morgan's large Berserker icon let a small template pass
            # at ~0.19 on most frames, HP was read from the 6-enemy crops as
            # 3,610,802, and a false "HP refill" crisis re-plan followed. A large
            # standard-row icon that matches clearly (and better) overrules it.
            if grid == 2 and phase in ("tactical", "cards"):
                standard = self._class_icon_score(detector, 93, [(250*i, 0, 110+250*i, 100) for i in range(3)])
                small = self._class_icon_score(detector, 75, [(110+200*i, 1, 173+200*i, 48) for i in range(3)])
                if standard is not None and standard < 0.1 and (small is None or standard < small):
                    signals["enemy_grid_override"] = {"read": 2, "used": 0, "standard_icon": round(standard, 3),
                                                      "small_icon": None if small is None else round(small, 3)}
                    grid = 0
                    if hasattr(self.module, "XDetectBase"):
                        self.module.XDetectBase.enemyGird = 0
            for i in range(6 if grid == 2 else 3):
                rect = ((190+i%3*200-i//3*100, 28+i//3*99, 287+i%3*200-i//3*100, 53+i//3*99)
                        if grid == 2 else (100+250*i, 40, 222+250*i, 65))
                enemies.append(dict(slot=i+1, hp=number(rect, f"enemy {i+1} HP"), charge=read("getEnemyNp", i),
                                    status_icons=self._enemy_icon_signature(image, i) if grid != 2 else None))
            from .enemy_priority import observe_priority_labels
            from .enemy_target import guard_enemy_presence, target_signals
            enemies = guard_enemy_presence(image, enemies, grid=grid)
            # Enemy class from its icon, for the class-affinity term of damage
            # estimates. Unmatched icons (beasts, bosses with custom icons) stay None.
            for i, enemy in enumerate(enemies):
                if enemy.get("hp"):
                    enemy["class_name"] = self._enemy_class(detector, i, grid)
                    if grid != 2:
                        try:
                            enemy["bars_remaining"] = _bars_remaining(image, i)
                        except Exception as exc:
                            errors.append(f"enemy {i+1} bars: {type(exc).__name__}: {exc}")
            enemies = observe_priority_labels(image, enemies, grid=grid)
            signals.update(target_signals(image, enemies, phase=phase, grid=grid))
            if phase == "cards":
                carried_allies = (wave, turn) == self.last_tactical_context and wave is not None and turn is not None
                if carried_allies:
                    # The HP/NP panel is hidden by the command cards. Carry the last
                    # same-turn snapshot explicitly, never present it as fresh OCR.
                    allies = self.last_tactical_allies
                    signals["allies_source"] = "last tactical frame of the same wave/turn"
                sealed = read("isCardSealed")
                try:
                    stunned = [_stunned(image, (60+257*i, 470, 250+257*i, 550)) for i in range(5)]
                except Exception as exc:
                    stunned = [False] * 5
                    errors.append(f"card stun check: {type(exc).__name__}: {exc}")
                if isinstance(sealed, list) and len(sealed) == 5:
                    sealed = [bool(v) or stunned[i] for i, v in enumerate(sealed)]
                if any(stunned):
                    signals["stunned_cards"] = [i + 1 for i, v in enumerate(stunned) if v]
                affinity = read("getCardResist")
                critical = read("getCardCriticalRate")
                groups = read("getCardGroup")
                hints = [a["id"] for a in allies if a.get("id") is not None]
                identities = read("getCardServant", hints) if hints else None
                for i, color in enumerate(colors):
                    owners = [a["slot"] for a in allies if identities is not None and a.get("id") == identities[i]]
                    cards.append(dict(slot=i+1, type=("arts", "quick", "buster")[int(color)] if color is not None else None,
                                      servant=owners[0] if len(owners) == 1 else None,
                                      # Kept even when two copies make the slot ambiguous.
                                      servant_id=identities[i] if identities is not None else None,
                                      identity_source="upstream nearest portrait match" if len(owners) == 1 else "unknown or duplicate servant ID",
                                      group=int(groups[i]) if groups is not None else None,
                                      sealed=bool(sealed[i]) if sealed is not None else None,
                                      stunned=stunned[i],
                                      ready=color is not None and sealed is not None and not bool(sealed[i]),
                                      affinity=("neutral", "weak", "resist")[affinity[i]] if affinity is not None else None,
                                      critical_rate=int(critical[i])*10 if critical is not None else None))
                np_ready = read("isHouguReady", detector)  # never capture a second frame
                np_readiness = []
                for i in range(3):
                    try:
                        np_sealed = (any(detector._compare(
                            template, (313+231*i, 194, 515+231*i, 270), .52)
                            for template in (detector.tmpl.HOUGUSEALED, detector.tmpl.CHARASEALED))
                            or _stunned(image, (300+231*i, 170, 530+231*i, 270))
                            or _np_sealed_label(image, i))
                    except Exception as exc:
                        np_sealed = None
                        errors.append(f"NP {i+1} seal check: {type(exc).__name__}: {exc}")
                    bar_ready = bool(np_ready[i]) if isinstance(np_ready, (list, tuple)) and len(np_ready) > i else None
                    carried_ally = next((ally for ally in allies if ally.get("slot") == i+1), None)
                    carried_full_np = (carried_allies and carried_ally is not None
                                       and carried_ally.get("np") is not None and carried_ally["np"] >= 100)
                    if np_sealed is True:
                        ready, source = False, "NP or Servant seal template"
                    elif np_sealed is None:
                        ready, source = None, "seal template unknown"
                    elif bar_ready is True:
                        ready, source = True, "charge bar brightness"
                    elif carried_full_np:
                        # The legacy method normally ORs this frame with a second
                        # frame to survive the full-NP blink.  Observations never
                        # capture a second frame, so carry only same-turn OCR.
                        ready, source = True, "same-turn carried ally NP >= 100"
                    elif bar_ready is False:
                        ready, source = False, "charge bar brightness"
                    else:
                        ready, source = None, "unknown"
                    np_readiness.append({"slot": i+1, "ready": ready, "source": source,
                                         "sealed": np_sealed,
                                         "carried_np": carried_ally.get("np") if carried_ally is not None else None})
                    cards.append(dict(slot=i+6, type="np", servant=i+1, ready=ready,
                                      affinity=("neutral", "weak", "resist")[affinity[i+5]] if affinity is not None else None,
                                      sealed=False if ready is True else None))
                signals["np_readiness"] = np_readiness
        return Observation(id=observation_id, phase=phase, captured_at=captured_at,
                           screen_path=str(filename.resolve()), allies=allies, enemies=enemies, cards=cards,
                           wave=wave, turn=turn, target_count=target_count, errors=errors, signals=signals)

    @staticmethod
    def _completion_screen(image):
        import cv2
        directory = Path(__file__).parent / "assets/fga-en"
        regions = {"result": (50, 150, 400, 250), "master_exp": (640, 175, 840, 230),
                   "master_lvl_up": (995, 80, 1120, 215), "bond": (1000, 300, 1060, 500)}
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        for name, (x1, y1, x2, y2) in regions.items():
            template = cv2.imread(str(directory / (name + ".png")), cv2.IMREAD_GRAYSCALE)
            crop = gray[y1:y2, x1:x2]
            if template is None or template.shape[0] > crop.shape[0] or template.shape[1] > crop.shape[1]:
                continue
            score = float(cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1])
            if score >= .90:
                return {"template": name, "similarity": score}
        return None

    @staticmethod
    def _skill_readiness(detector, ally, skill):
        import cv2
        import numpy as np
        offset = 318*ally + 88*skill
        score = float(detector._loc(detector.tmpl.STILL, (41+offset, 607, 74+offset, 614))[0])
        icon = detector._crop((48+offset, 552, 101+offset, 604))
        values = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)[..., 2]
        mean = float(np.mean(values))
        bright_fraction = float(np.mean(values > 160))
        # The upstream 0.6 cutoff marks three visibly available golden icons as
        # unavailable in the verified fixture. Keep both visual signals instead.
        ready = None
        if np.isfinite(score):
            if mean > 170 and bright_fraction > .60 and score > .35:
                ready = True
            elif mean < 150 and bright_fraction < .25:
                ready = False
        # Brightness cannot judge an icon whose art is itself dark. Seen live:
        # Melusine's ready "Blue Horizon" (mean 165) stayed unknown all battle, so
        # its 50% charge was never offered and a 3-turn clear took 7 turns. A
        # cooling skill always prints "Turn(s)" under its icon; a ready one never.
        label = _cooldown_label_score(detector._crop((24+offset, 596, 86+offset, 622)))
        if label is not None:
            if label >= _COOLDOWN_LABEL_PRESENT:
                ready = False
            elif ready is None and label < _COOLDOWN_LABEL_ABSENT and np.isfinite(score):
                ready = True
        return dict(ready=ready, still_match_score=score if np.isfinite(score) else None,
                    icon_brightness=mean, bright_fraction=bright_fraction, cooldown_label_score=label)

    @staticmethod
    def _enemy_icon_signature(image, index: int) -> list[int]:
        """Coarse picture of an enemy's buff icon row (standard layout), to notice a
        boss cycling its self-buffs between reads."""
        import cv2
        x0 = 8 + 250 * index
        row = cv2.cvtColor(image[100:130, x0:x0 + 140], cv2.COLOR_BGR2GRAY)
        return [int(v) for v in cv2.resize(row, (14, 3), interpolation=cv2.INTER_AREA).flatten()]

    @staticmethod
    def _status_icon_signature(image, index: int) -> list[int]:
        """A coarse picture of the ally's buff/debuff icon row, to notice when it changes."""
        import cv2
        x0 = 30 + 318 * index
        row = cv2.cvtColor(image[515:545, x0:x0 + 150], cv2.COLOR_BGR2GRAY)
        return [int(v) for v in cv2.resize(row, (15, 3), interpolation=cv2.INTER_AREA).flatten()]

    def _identity_from_name(self, detector, slot, errors):
        """(id, source, label) from the battle name label, for when the portrait fails."""
        from .identity import FGOPY_CLASS_NAMES, name_label_region, resolve_battle_name
        try:
            if self._servant_index is None:
                from .catalog import load_servant_index
                self._servant_index = load_servant_index()
            klass = inspect.unwrap(type(detector).getFieldServantClassRank)(detector, slot)
            class_name = FGOPY_CLASS_NAMES.get(klass[0]) if klass else None
            label = str(detector.ocr.ocrText(detector._crop(name_label_region(slot)))).strip() or None
            key = (label, class_name)
            if key not in self._name_identities:
                self._name_identities[key] = resolve_battle_name(label, class_name, self._servant_index)
            servant_id, source = self._name_identities[key]
            return servant_id, source, label
        except Exception as exc:
            errors.append(f"ally {slot+1} name identity: {type(exc).__name__}: {exc}")
            return None, None, None

    def _servant_identity(self, detector, slot, errors):
        """Reject weak nearest-neighbour matches instead of always naming a Servant."""
        import cv2
        import numpy as np
        try:
            # The same class icon and portrait pixels always give the same answer;
            # matching every portrait of the class cost 0.6 s per frame.
            key = (slot, _pixels_key(detector._crop((13+318*slot, 618, 117+318*slot, 702))),
                   _pixels_key(detector._crop((120+318*slot, 421, 207+318*slot, 490))))
            if key not in self._identities:
                if len(self._identities) >= 1024:
                    self._identities.clear()
                self._identities[key] = self._match_servant(detector, slot)
            return self._identities[key]
        except Exception as exc:
            errors.append(f"ally {slot+1} identity: {type(exc).__name__}: {exc}")
            return None, None

    def _enemy_class(self, detector, index, grid):
        from .identity import FGOPY_CLASS_NAMES
        rect = ((110+200*(index % 3)-(index//3)*100, 1+99*(index//3), 173+200*(index % 3)-(index//3)*100,
                 48+99*(index//3)) if grid == 2 else (250*index, 0, 110+250*index, 100))
        try:
            key = (grid, index, _pixels_key(detector._crop(rect)))
            if key not in self._enemy_classes:
                if len(self._enemy_classes) >= 512:
                    self._enemy_classes.clear()
                found = detector._select(self.module.CLASS[75 if grid == 2 else 93], rect, .12)
                klass = self.module.classImg[0][found] if found is not None else None
                self._enemy_classes[key] = FGOPY_CLASS_NAMES.get(klass[0]) if klass else None
            return self._enemy_classes[key]
        except Exception:
            return None

    def _class_icon_score(self, detector, scale, rects):
        """Best (lowest) class-icon template score at one icon size over the regions."""
        try:
            templates = self.module.CLASS[scale]
            return min(float(detector._loc(t, rect)[0]) for rect in rects for t in templates)
        except Exception:
            return None

    def _match_servant(self, detector, slot):
        import cv2
        import numpy as np
        klass = inspect.unwrap(type(detector).getFieldServantClassRank)(detector, slot)
        if klass is None:
            return None, None
        crop = detector._crop((120+318*slot, 421, 207+318*slot, 490))
        scores = []
        for servant_id, (_, portraits, _) in self.module.servantImg.items():
            if self.module.servantData[servant_id][0] == klass[0]:
                score = min(float(np.min(cv2.matchTemplate(crop, p[0], cv2.TM_SQDIFF_NORMED, mask=p[1]))) for p in portraits)
                if np.isfinite(score):
                    scores.append((score, servant_id))
        scores.sort()
        if not scores:
            return None, None
        score, servant_id = scores[0]
        # Conservative starting threshold, not a calibrated confidence probability.
        if score > .08 or (len(scores) > 1 and scores[1][0] - score < .005):
            return None, score
        return int(servant_id), score
