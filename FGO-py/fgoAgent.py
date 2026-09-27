"""English entry point for the observable NA battle controller."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from fgo_agent.models import Action


def _prune_old_runs(args, session):
    """Retention never fails a run: report what was removed, or why it could not be."""
    from fgo_agent.retention import prune_runs
    try:
        removed = prune_runs(args.out, keep=getattr(args, 'keep_runs', 5), current=session)
        if removed:
            print(f'Removed {len(removed)} old run folder(s); keeping the newest {args.keep_runs}.', flush=True)
    except Exception as exc:
        print(f'Old run cleanup skipped: {type(exc).__name__}: {exc}', flush=True)
from fgo_agent.trace import TraceWriter
from fgo_agent.transport import AdbEmulator, find_adb, list_devices

ROOT = Path(__file__).resolve().parent.parent


def load_plan(path: Path) -> list[Action]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not 1 <= len(data) <= 12:
        raise ValueError("A plan must be a JSON array of 1 to 12 actions.")
    actions = []
    for item in data:
        if not isinstance(item, dict) or item.get("kind") not in {"skill", "attack", "cards", "enemy_target"}:
            raise ValueError("Plan contains an unsupported action; skill_target is internal only.")
        if set(item) - {"kind", "servant", "skill", "target", "cards", "reason"}:
            raise ValueError("Plan contains an unknown field.")
        for field in ("servant", "skill", "target"):
            if item.get(field) is not None and (type(item[field]) is not int or not 1 <= item[field] <= 3):
                raise ValueError(f"{field} must be an integer from 1 to 3.")
        cards = item.get("cards", [])
        if not isinstance(cards, list) or any(type(c) is not int or not 1 <= c <= 8 for c in cards):
            raise ValueError("Card slots must be integers from 1 to 8.")
        if not isinstance(item.get("reason", ""), str):
            raise ValueError("Action reason must be text.")
        if item["kind"] == "skill" and (item.get("servant") is None or item.get("skill") is None):
            raise ValueError("Skill actions require servant and skill slots.")
        if item["kind"] == "cards" and (len(cards) != 3 or len(set(cards)) != 3):
            raise ValueError("A cards action requires three distinct slots.")
        if item["kind"] == "enemy_target" and item.get("target") is None:
            raise ValueError("An enemy_target action requires a target slot.")
        actions.append(Action(**{**item, "cards": tuple(cards)}))
    return actions


PARTY_FILE = ROOT / "agent-runs" / "party-latest.json"
PARTY_MAX_AGE_SECONDS = 4 * 3600


def read_party(images, detector_module) -> dict | None:
    """Merge Confirm Party reads from several frames; None if the screen is absent.

    Seen live: the skill/NP line alternates with append-skill levels every few
    seconds, so each field takes its most common valid reading across frames.
    """
    from collections import Counter
    from fgo_agent.party import read_confirm_party
    reads = []
    for image in images:
        detector = object.__new__(detector_module.XDetectNA)
        detector.im = image
        read = read_confirm_party(image, detector_module.XDetectNA.ocr.ocrText, detector)
        if read is not None:
            reads.append(read)
    if not reads:
        return None
    party = {"members": [], "frames": len(reads)}
    for index in range(len(reads[0]["members"])):
        member = {}
        for key in reads[0]["members"][index]:
            values = [json.dumps(r["members"][index].get(key)) for r in reads
                      if r["members"][index].get(key) is not None]
            member[key] = json.loads(Counter(values).most_common(1)[0][0]) if values else None
        if member.get("atk") is not None:  # an empty or locked column reads as noise
            party["members"].append(member)
    return party


def save_party(party: dict) -> None:
    from datetime import datetime, timezone
    party["captured_at"] = datetime.now(timezone.utc).isoformat()
    PARTY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PARTY_FILE.write_text(json.dumps(party, indent=2), encoding="utf-8")


def capture_frames(device, count=6, pause=0.7):
    import time
    frames = []
    for index in range(count):
        frames.append(device.screenshot())
        if index + 1 < count:
            time.sleep(pause)
    return frames


def read_party_command(args) -> int:
    """Read-only: save the Confirm Party numbers for the next battle to use."""
    import cv2
    if args.image:
        images = [cv2.imread(str(args.image))]
    else:
        if not args.serial:
            raise ValueError("Select --serial explicitly; the agent never chooses the first device.")
        device = AdbEmulator(args.serial, find_adb(args.adb), allow_input=False)
        device.connect()
        images = capture_frames(device)
    os.chdir(Path(__file__).resolve().parent)
    import fgoDetect
    party = read_party(images, fgoDetect)
    if party is None:
        print(json.dumps({"status": "not_confirm_party", "reason": "Confirm Party screen not recognized"}))
        return 1
    save_party(party)
    print(json.dumps(party, indent=2))
    return 0


TO_BATTLE_BUTTON = (1165, 690)


def start_from_party(device, observer, trace, timeout=120.0) -> str:
    """On Confirm Party: read the party, tap To Battle, wait for the battle screen.

    The user starts the agent from Confirm Party on purpose (GUI "Start"), so the
    To Battle tap is their start command; it is logged like any input. Anywhere
    else nothing is tapped and the battle already on screen is played.
    """
    import time
    frames = capture_frames(device)
    party = read_party(frames, observer.module)
    if party is None:
        trace.record("start_from_party", {"status": "not_on_confirm_party"})
        return "not_on_confirm_party"
    save_party(party)
    trace.record("start_from_party", {"status": "party_read", "frames": party.get("frames")})
    trace.record("input", {"x": TO_BATTLE_BUTTON[0], "y": TO_BATTLE_BUTTON[1],
                           "reason": "To Battle on Confirm Party (user started the agent from that screen)"})
    device.tap(*TO_BATTLE_BUTTON)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(2)
        observation = observer.observe(device.screenshot())
        if observation.phase == "tactical" and observation.turn is not None:
            trace.record("start_from_party", {"status": "battle_screen_reached", "observation_id": observation.id})
            return "battle_screen_reached"
    trace.record("start_from_party", {"status": "battle_screen_not_reached"})
    return "battle_screen_not_reached"


def load_recent_party() -> list | None:
    """The last Confirm Party read, if it is recent enough to be this battle's party."""
    from datetime import datetime, timezone
    try:
        data = json.loads(PARTY_FILE.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["captured_at"])).total_seconds()
        return data["members"] if 0 <= age <= PARTY_MAX_AGE_SECONDS else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parser():
    result = argparse.ArgumentParser(description="FGO NA observable controller for MuMu. Observation is read-only.")
    sub = result.add_subparsers(dest="command", required=True)
    devices = sub.add_parser("devices", help="List ADB connections without sending game input")
    devices.add_argument("--adb")
    catalog = sub.add_parser('catalog', help='Refresh cached English NA servant descriptions; no game input')
    wanted = catalog.add_mutually_exclusive_group(required=True)
    wanted.add_argument('--ids', nargs='+', type=int)
    wanted.add_argument('--all', action='store_true', help='Every playable NA servant from one Atlas export')
    party = sub.add_parser('party', help='Read the Confirm Party screen (ATK, levels, NP levels); no game input')
    party.add_argument('--serial')
    party.add_argument('--adb')
    party.add_argument('--image', type=Path, help='Read a saved 1280x720 screenshot instead of the device')
    replay = sub.add_parser('replay', help='Compare independent snapshots with Sol and the baseline; no device input')
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument('--suite', type=Path)
    source.add_argument('--trace', type=Path)
    replay.add_argument('--limit', type=int, default=20)
    replay.add_argument('--out', type=Path, default=ROOT / 'agent-runs')
    replay.add_argument('--keep-runs', type=int, default=5, help='Keep only the newest N replay folders')
    replay.add_argument('--objective', choices=['farm_fast', 'survive_and_win'], default='survive_and_win')
    replay.add_argument('--model', default='gpt-6-sol')
    replay.add_argument('--reasoning', default='low', choices=['low', 'medium', 'high'])
    replay.add_argument('--fast', action=argparse.BooleanOptionalAction, default=True)
    replay.add_argument('--planner-timeout', type=float, default=60)
    replay.add_argument('--codex-home', type=Path, help='Separate Codex account directory; default uses current development login')
    replay.add_argument('--account', choices=['development', 'dedicated'],
                        help="Named saved account instead of spelling out --codex-home")
    for command in ("observe", "execute", "battle"):
        p = sub.add_parser(command, help={"observe": "Capture battle state", "execute": "Execute one bounded action plan and record evidence",
                                          "battle": "Run the baseline policy for one already-started battle"}[command])
        p.add_argument("--serial", help="Explicit MuMu ADB serial, e.g. 127.0.0.1:16384")
        p.add_argument("--adb")
        p.add_argument("--out", type=Path, default=ROOT / "agent-runs")
        p.add_argument("--keep-runs", type=int, default=5,
                       help="Keep only the newest N run folders (a folder holding a KEEP file is never deleted)")
        p.add_argument("--cooldown-ocr", action="store_true", help="Enable experimental NA cooldown OCR; verify against saved screenshots")
        if command == "observe":
            p.add_argument("--image", type=Path, help="Analyze an existing 1280x720 PNG without connecting to a device")
        elif command == "execute":
            p.add_argument("--plan", type=Path, required=True)
            p.add_argument("--timeout", type=float, default=40.0, help="Post-action polling budget in seconds")
            p.add_argument("--max-polls", type=int, default=15)
        else:
            p.add_argument('--policy', choices=['legacy', 'codex'], default='legacy')
            p.add_argument('--notes', type=Path,
                           help='Stage notes written after a failed attempt; sent to the AI on every decision')
            p.add_argument('--start-from-party', action=argparse.BooleanOptionalAction, default=True,
                           help='If Confirm Party is showing: read the party, tap To Battle, then play the battle. '
                                'Anywhere else nothing is tapped.')
            p.add_argument('--party', action=argparse.BooleanOptionalAction, default=True,
                           help='Use the last Confirm Party read (fgoAgent party) if under 4 hours old')
            p.add_argument('--speed-taps', action=argparse.BooleanOptionalAction, default=True,
                           help='Tap the turn panel during skill and NP animations to speed them up')
            p.add_argument('--plan-ahead', action='store_true',
                           help='Codex plans the whole battle up front and replans only on deviation')
            p.add_argument('--inspect-enemies', action=argparse.BooleanOptionalAction, default=True,
                           help='Long-press enemies to read their Status Changes for the AI (codex policy only)')
            p.add_argument('--order-change', action=argparse.BooleanOptionalAction, default=True,
                           help='Allow Order Change (Chaldea Combat Uniform). Reads the back line once per battle; '
                                'an empty back line or another Mystic Code just leaves it unavailable.')
            p.add_argument('--objective', choices=['farm_fast', 'survive_and_win'], default='survive_and_win',
                           help='What the AI policy optimizes for; farm_fast trades resources for fewer turns')
            p.add_argument('--model', default='gpt-6-sol')
            p.add_argument('--reasoning', default='low', choices=['low', 'medium', 'high'])
            p.add_argument('--fast', action=argparse.BooleanOptionalAction, default=True,
                           help='Codex Fast service tier (about 1.5x faster decisions); --no-fast for standard')
            p.add_argument('--planner-timeout', type=float, default=60)
            p.add_argument('--codex-home', type=Path, help='Separate Codex account directory; default uses current development login')
            p.add_argument('--account', choices=['development', 'dedicated'],
                           help="Named saved account instead of spelling out --codex-home; 'dedicated' is the separate app login")
            p.add_argument("--max-actions", type=int, default=80)
            p.add_argument("--max-turns", type=int, default=30)
            p.add_argument("--battle-timeout", type=float, default=900)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "devices":
        print(json.dumps(list_devices(find_adb(args.adb)), indent=2))
        return 0
    if args.command == 'party':
        return read_party_command(args)
    if args.command == 'catalog':
        from fgo_agent.catalog import refresh_catalog, refresh_full_catalog
        if args.all:
            records = refresh_full_catalog()
            print(json.dumps({'servants': len(records)}, indent=2))
            return 0
        records = refresh_catalog(args.ids)
        print(json.dumps({str(i): records[i]['name'] for i in args.ids}, ensure_ascii=False, indent=2))
        return 0
    if hasattr(args, 'planner_timeout') and not 1 <= args.planner_timeout <= 120:
        raise ValueError('Planner timeout must be 1..120 seconds')
    if args.command == 'replay':
        from fgo_agent.catalog import load_catalog
        from fgo_agent.codex_backend import CodexBackend
        from fgo_agent.planner import PlannerPolicy
        from fgo_agent.evaluation import load_cases, load_legacy_metadata, evaluate
        cases = load_cases(args.trace or args.suite, args.limit, trace=args.trace is not None)
        session = args.out.resolve() / ('replay-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        session.mkdir(parents=True, exist_ok=False)
        _prune_old_runs(args, session)
        data, catalog = load_legacy_metadata(), load_catalog()
        codex_home = args.codex_home
        if args.account:
            from fgo_agent.codex_account import account_home
            if codex_home is not None:
                raise ValueError('Pass either --account or --codex-home, not both.')
            codex_home = account_home(args.account)
        backend = CodexBackend(session / 'model-calls', model=args.model, effort=args.reasoning, timeout=args.planner_timeout, codex_home=codex_home,
                         service_tier='priority' if getattr(args, 'fast', True) else None)
        trace = TraceWriter(session)
        print(f'Session: {session}', flush=True)
        outcome = evaluate(cases, data, lambda: PlannerPolicy(data, backend, trace=trace, strategy_catalog=catalog,
                                                              objective=args.objective), session)
        print(json.dumps(outcome, indent=2))
        return 0 if outcome['metrics']['planner']['errors'] == 0 and outcome['metrics']['planner']['legal'] == len(cases) else 2
    image_path = getattr(args, "image", None)
    if image_path and args.serial:
        raise ValueError("Use --image or --serial, not both.")
    if not image_path and not args.serial:
        raise ValueError("Select --serial explicitly; the agent never chooses the first device.")
    plan = load_plan(args.plan.resolve()) if args.command == "execute" else None
    if args.command == "battle" and (not 1 <= args.max_actions <= 300 or not 1 <= args.max_turns <= 100
                                     or not 0 < args.battle_timeout <= 3600):
        raise ValueError("Battle bounds: 1..300 actions, 1..100 turns, 0..3600 seconds (boss fights ran 30 min).")
    if plan and (not 0 < args.timeout <= 120 or not 1 <= args.max_polls <= 60):
        raise ValueError("Use a timeout of 0..120 seconds and 1..60 polls.")
    image_path = image_path.resolve() if image_path else None
    session = args.out.resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    session.mkdir(parents=True, exist_ok=False)
    _prune_old_runs(args, session)
    trace = TraceWriter(session)
    trace.record("session", {"mode": args.command, "serial": args.serial, "region": "NA",
                             "source_image": str(image_path) if image_path else None,
                             "cooldown_ocr": args.cooldown_ocr or args.command == "battle",
                             "policy": getattr(args, 'policy', None),
                             "objective": getattr(args, 'objective', None),
                             "account": getattr(args, 'account', None),
                             "order_change": getattr(args, 'order_change', None),
                             "plan_ahead": getattr(args, 'plan_ahead', None),
                             "start_from_party": getattr(args, 'start_from_party', None),
                             "notes": str(args.notes) if getattr(args, 'notes', None) else None,
                             "model": getattr(args, 'model', None), "reasoning": getattr(args, 'reasoning', None)})
    print(f"Session: {session}", flush=True)
    try:
        device = None
        if image_path is None:
            device = AdbEmulator(args.serial, find_adb(args.adb), allow_input=args.command in {"execute", "battle"})
            device.connect()
        # Upstream asset paths are relative to this directory. No updater is run.
        os.chdir(Path(__file__).resolve().parent)
        os.environ["NO_COLOR"] = "1"
        from fgo_agent.observer import LegacyObserver
        from fgo_agent.driver import BattleDriver
        observer = LegacyObserver(session / "screenshots", cooldown_ocr=args.cooldown_ocr or args.command == "battle")
        if args.command == "observe":
            if image_path:
                import cv2
                image = cv2.imread(str(image_path))
                if image is None:
                    raise ValueError(f"Could not read {image_path}")
            else:
                image = device.screenshot()
            observation = observer.observe(image)
            trace.record("observation", observation)
            (session / "observation.json").write_text(json.dumps(asdict(observation), indent=2), encoding="utf-8")
            summary = f"# Observation\n\nPhase: **{observation.phase}**. Wave: {observation.wave}. Turn: {observation.turn}.\n\n"
            summary += "No input was sent. Numeric values and readiness are detector observations, not ground truth.\n\n"
            summary += f"- Screenshot: [frame]({Path(observation.screen_path).as_uri()})\n- Full state: observation.json\n- Detector issues: {len(observation.errors)}\n"
            trace.write_summary(summary)
            print(json.dumps(asdict(observation), indent=2))
            return 0
        from fgo_agent.controller import BattleController
        order_change = bool(getattr(args, 'order_change', False))
        servant_index = {}
        if order_change:
            from fgo_agent.catalog import load_servant_index
            servant_index = load_servant_index()
        # Enemy inspection only informs the AI policy; legacy never reads it.
        inspect_enemies = (args.command == 'battle' and getattr(args, 'policy', None) == 'codex'
                           and bool(getattr(args, 'inspect_enemies', False)))
        driver = BattleDriver(device, observer, trace, order_change=order_change, servant_index=servant_index,
                              inspect_enemies=inspect_enemies)
        if args.command == "battle":
            from fgo_agent.legacy_policy import LegacyPolicy
            from fgo_agent.battle_runner import BattleRunner
            # A frame now takes ~0.9 s (raw capture + memoized reads), so a 1 s pause
            # between frames was most of every wait. The 120 s timeout stays the bound.
            controller = BattleController(driver, trace=trace, timeout=120, max_polls=200, poll_interval=0.4,
                                          speed_taps=bool(getattr(args, 'speed_taps', True)))
            if getattr(args, 'start_from_party', False):
                started = start_from_party(device, observer, trace)
                print(f"Start from party: {started}", flush=True)
                if started == "battle_screen_not_reached":
                    raise RuntimeError("To Battle was tapped but the battle screen did not appear within 2 minutes "
                                       "(close any popup in the game, then press Start again).")
            policy = LegacyPolicy(observer.module.servantData)
            if args.policy == 'codex':
                from fgo_agent.codex_backend import CodexBackend
                from fgo_agent.planner import PlannerPolicy
                from fgo_agent.catalog import load_catalog
                codex_home = args.codex_home
                if getattr(args, 'account', None):
                    from fgo_agent.codex_account import account_home
                    if codex_home is not None:
                        raise ValueError('Pass either --account or --codex-home, not both.')
                    codex_home = account_home(args.account)
                backend = CodexBackend(session / 'model-calls', model=args.model, effort=args.reasoning, timeout=args.planner_timeout, codex_home=codex_home,
                         service_tier='priority' if getattr(args, 'fast', True) else None)
                notes = args.notes.read_text(encoding='utf-8-sig') if getattr(args, 'notes', None) else None
                party = load_recent_party() if getattr(args, 'party', True) else None
                trace.record("party", {"members": party} if party else {"members": None})
                policy = PlannerPolicy(observer.module.servantData, backend, trace=trace, strategy_catalog=load_catalog(),
                                       objective=args.objective, notes=notes,
                                       plan_ahead=bool(getattr(args, 'plan_ahead', False)), party=party)
            reconnoitre = ((lambda: controller.reconnoitre_order_change(time_budget=90))
                           if order_change else None)
            scout = ((lambda observation: controller.scout_enemies(observation, time_budget=60))
                     if inspect_enemies else None)
            outcome = BattleRunner(controller, policy, trace, reconnoitre=reconnoitre, scout=scout).run(
                max_actions=args.max_actions, max_turns=args.max_turns, battle_timeout=args.battle_timeout)
            (session / "battle-result.json").write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            trace.write_summary(f"# One-battle {args.policy} run\n\n```json\n" + json.dumps(outcome, indent=2) + "\n```\n")
            print(json.dumps(outcome, indent=2))
            return 0 if outcome.get("status") == "completed" else 2
        controller = BattleController(driver, trace=trace, timeout=args.timeout, max_polls=args.max_polls, poll_interval=.5)
        trace.record("plan", plan)
        results = controller.run(plan)
        (session / "results.json").write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
        summary = "# Bounded action run\n\n"
        summary += "\n".join(f"- **{r.status}** {r.action.kind}: {r.reason or '; '.join(r.evidence)}; skill consumed={r.skill_consumed}, effect observed={r.effect_observed}" for r in results)
        summary += f"\n\nAttempted {len(results)} of {len(plan)} planned actions. Polling stops on uncertainty; actions are never automatically retried.\n"
        trace.write_summary(summary)
        print(json.dumps([asdict(r) for r in results], indent=2))
        return 0 if len(results) == len(plan) and all(r.status == "confirmed" for r in results) else 2
    except (Exception, KeyboardInterrupt) as exc:
        trace.record("error", {"type": type(exc).__name__, "message": str(exc)})
        trace.write_summary(f"# Run stopped\n\n{type(exc).__name__}: {exc}\n\nInspect trace.jsonl and screenshots before making another attempt.\n")
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
