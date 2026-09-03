import re
import time

from core.recalibrate import recalibrate
from usecases.lock_evidence import note_if_locked

from core.core import (
    req_ocr,
    req_text,
    ensure_screen,
    tap_on_text,
    req_temp_match,
    tap_on_template,
    tap_on_templates_batch
)
from cmd_program.screen_action import(
    tap_screen,
    swipe_screen,
    input_text
)

ADVENTURE_ATTEMPTS_KEY = "Home.Pet.BeastCage.Adventure.RemainingAttempt"
ADVENTURE_GROUND_KEY = "Home.Pet.BeastCage.Adventrue.AdventureGround"


def _read_remaining_attempts():
    """Remaining adventure attempts, or None when the read is not a number.

    Read on its OWN roi. Reading it alongside AdventureGround (which spans
    almost the whole content area) put both ROIs' lines in one flat list with no
    ROI attribution, and req_ocr drops ROIs that return nothing — so index 0
    slid silently from the digit box into the other ROI's first line. That is
    how int() came to be handed a mail subject.
    """
    res = req_text(ADVENTURE_ATTEMPTS_KEY, read_kind="value")
    if not res:
        return None
    text = str(res[0][0]).strip()
    # First run of digits, not every digit stripped together: the recorded field
    # is a bare '1', but the moment OCR picks up a neighbouring glyph and returns
    # '1/3', concatenating gives 13 attempts out of thin air. The sibling
    # Labyrinth field reads 'Remaining attempts today: 4' and works either way.
    match = re.search(r"\d+", text)
    if not match:
        print(f"Remaining-attempts read was not a number: {text!r}")
        return None
    return int(match.group())


def _count_adventuring():
    """How many pets are already out, counted by their HH:MM:SS timers."""
    res = req_text(ADVENTURE_GROUND_KEY, read_kind="value") or []
    return sum(1 for t in res if len(str(t[0]).split(":")) == 3)


def collect_ally_treasure(player_id=None):
    recalibrate()
    status = tap_on_template("Home.Pet", wait=2)
    if not status:
        print("Pet icon not found on the homepage, ending the task...")
        return None
    tap_on_text("Home.Pet.Skill.BeastCage", sleep=1, wait=2)
    tap_on_text("Home.Pet.BeastCage.Adventure", wait=2)
    # Arrival check: a mis-tapped icon still returns True above, and every tap
    # below ignores its own return value — so without this the task silently
    # no-ops against whatever screen it actually landed on.
    if not ensure_screen("Home.Pet.BeastCage.Adventure.Title", "Pet Adventure"):
        # A lock overlay also stops the title reading "Pet Adventure", so this
        # message used to misreport a locked feature as a navigation failure —
        # the same misdiagnosis core/recalibrate.py:48-53 removed for the home
        # screen. Check before blaming navigation.
        note_if_locked(player_id, "beast_cage", "pet adventure arrival check")
        print("Did not reach the Pet Adventure screen, ending the task...")
        return None
    tap_on_text("Home.Pet.BeastCage.Adventure.AllyTreasure", wait=2, align=[0, -50])
    tap_on_text("Home.Pet.BeastCage.Adventure.AllyTreasure.AllianceShares", wait=2, sleep=0.5)
    tap_on_text("Home.Pet.BeastCage.Adventure.AllyTreasure.AllianceShares.ClaimAll", wait=2)
    tap_on_text("Tap anywhere to exit", sleep=1)
    tap_on_text("Home.Pet.BeastCage.Adventure.AllyTreasure.MyShares", wait=2)
    tap_on_text("Home.Pet.BeastCage.Adventure.AllyTreasure.MyShares.Share", wait=2, sleep=1)
    return True



def start_pet_exploration(player_id=None):
    exploration_roi = [0, 16.26, 100, 89.43]

    def center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    
    def distance(c1, c2):
        return ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2)**0.5

    recalibrate()

    status = tap_on_template("Home.Pet", wait=2)
    if not status:
        print("Pet icon not found on the homepage, ending the task...")
        return None

    tap_on_text("Home.Pet.Skill.BeastCage", sleep=1, wait=2)
    tap_on_text("Home.Pet.BeastCage.Adventure", wait=2, sleep=2)
    if not ensure_screen("Home.Pet.BeastCage.Adventure.Title", "Pet Adventure"):
        # A lock overlay also stops the title reading "Pet Adventure", so this
        # message used to misreport a locked feature as a navigation failure —
        # the same misdiagnosis core/recalibrate.py:48-53 removed for the home
        # screen. Check before blaming navigation.
        note_if_locked(player_id, "beast_cage", "pet adventure arrival check")
        print("Did not reach the Pet Adventure screen, ending the task...")
        return None

    remaining_attempts = _read_remaining_attempts()
    if remaining_attempts is None:
        print("Could not read the remaining attempts, ending the task...")
        return None
    adventuring = _count_adventuring()

    status = True
    while(status):
        status = tap_on_template("Home.Pet.BeastCage.Adventure.CompletedAdventure", wait=2)
        if not status:
            print("No adventure Completed")
            break
        if tap_on_text("Home.Pet.BeastCage.Adventure.Completed", wait=2, tap=False):
            tap_screen(51.85, 62.60)
            tap_on_text("Tap anywhere to exit", wait=4, sleep=0.5)
            tap_on_template("Global.Close", wait=2)

    while(adventuring<3 and remaining_attempts>0):
        fresh = _read_remaining_attempts()
        if fresh is None:
            print("Could not read the remaining attempts, assuming one was spent...")
            adventuring += 1
            remaining_attempts -= 1
        else:
            remaining_attempts = fresh
            adventuring = _count_adventuring()
        print(f"Remaining Attempt: {remaining_attempts}, Adventuring: {adventuring}")

        treasure_boxs = [
            "Home.Pet.BeastCage.Adventure.RedTreasure",
            "Home.Pet.BeastCage.Adventure.PurpleTreasure",
            "Home.Pet.BeastCage.Adventure.BlueTreasure"
        ]

        boxes = []
        for treasure_box in treasure_boxs:
            r = req_temp_match(treasure_box)
            if r:
                for item in r:
                    boxes.append(item)
        
        text = req_text("Home.Pet.BeastCage.Adventrue.AdventureGround", read_kind="value")
        treasures = []
        for box in boxes:
            valid = True
            for t in text:
                d = distance(center(box["box"]), center(t[1]))
                if d < 200:
                    valid = False
            if valid:
                treasures.append(box)

        for treasure in treasures:
            treasure = center(treasure["box"])
            tap_screen(treasure)
            time.sleep(0.5)

            status = tap_on_text("Home.Pet.BeastCage.Adventure.Treasure.InAdventure", wait=2, tap=False)
            if status:
                tap_on_template("Global.Close", wait=2)
                continue
            status = tap_on_text("Home.Pet.BeastCage.Adventure.SelectPet", wait=2, sleep=1)
            if status:
                status = tap_on_text("Home.Pet.BeastCage.Adventure.SelectPet.Start", wait=2)
                if not status:
                    status = tap_on_text("Home.Pet.BeastCage.Adventure.SelectPet.InsuffiecientAdventureAttempts", wait=2, tap=False)
                    if status:
                        print("Insufficint Adventure Attempts")
                        tap_on_template("Global.Close", wait=2, sleep=0.5)
                        tap_on_template("Global.Close", wait=2)
                        continue
                    continue
                adventuring += 1
                remaining_attempts -= 1
                tap_on_template("Global.Close", wait=2)
                continue
            status = tap_on_text("Home.Pet.BeastCage.Adventure.Completed", wait=2, tap=False)
            if status:
                tap_screen(51.85, 62.60)
                tap_on_text("Tap anywhere to exit", wait=2, sleep=0.5)
                tap_on_template("Global.Close", wait=2)
                continue
            else:
                print("Something went wrong")

    print("Task - Pet Exploration Completed, Returning to homepage...")




def activate_reward_pet_skill():
    return

def activate_war_pet_skill():
    return
