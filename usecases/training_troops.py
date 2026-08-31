import time
from core.recalibrate import recalibrate

from core.core import (
    ensure_screen,
    side_panel_is_open,
    req_ocr,
    req_text,
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


side_panel = [0, 28.05, 62.04, 67.07]
training_menu = [23.15, 56.91, 86.11, 73.17]


def train():

    recalibrate()

    # threshold=0.5 matched the city map itself (measured 0.518 with no panel
    # open), so this "succeeded" and the Infantry search below ran against the
    # city view. Confirm the panel by reading a tab label.
    tap_on_template("Global.SidePanel", wait=2)
    if not side_panel_is_open():
        print("Side panel did not open, ending the task...")
        return None

    status = tap_on_text("Infantry", rois=[side_panel], wait=2, sleep=1)

    if not status:
        print("Infantry row not found in the side panel, ending the task...")
        return None
    
    for i in range(3):
        tap_screen(50, 48.78)
        time.sleep(0.3)
    tap_on_text("Train", rois = [training_menu], wait=2, sleep=0.5)
    if not ensure_screen("Home.TroopTraining.Title", "infantry"):
        tap_on_text("Click anywhere", wait=2, sleep=0.5)

    status = tap_on_text("Home.TroopTraining.Train", wait=2)
    if not status:
        tap_screen(50, 48.78)
        status = tap_on_text("Home.TroopTraining.Train", wait=2)
    if not status:
        print("Infantry Training is not finished yet, Skipping Infantry...")

    tap_on_text("Home.TroopTraining.LancerCamp", wait=2, sleep=0.5)
    if not ensure_screen("Home.TroopTraining.Title", "lancer"):
        tap_on_text("Click anywhere", wait=2, sleep=0.5)
    status = tap_on_text("Home.TroopTraining.Train", wait=2)
    if not status:
        print("Lancer Training is not finished yet, Skipping Lancer...")

    tap_on_text("Home.TroopTraining.MarksmanCamp", wait=2, sleep=0.5)
    if not ensure_screen("Home.TroopTraining.Title", "marksman"):
        tap_on_text("Click anywhere", wait=2, sleep=0.5)
    status = tap_on_text("Home.TroopTraining.Train", wait=2)
    if not status:
        print("Marksman Training is not finished yet, Skipping Marksman...")

    return True



def train_infantry(Amount):

    recalibrate()

    # threshold=0.5 scored 0.518 against the city map with no panel open, so
    # this "succeeded" and everything below ran on the wrong view. The template
    # is pinned at 0.85 in template_config.json; prove the panel by reading a tab.
    tap_on_template("Global.SidePanel", wait=2)
    if not side_panel_is_open():
        print("Side panel did not open, ending the task...")
        return None

    tap_on_text("Infantry", rois=[side_panel], wait=2)
    for i in range(3):
        tap_screen(50, 48.78)
        time.sleep(0.3)
    tap_on_text("Train", rois = [training_menu], wait=3)

    tap_screen(50.93, 44.72)            # Taping at the middle of the screen to remove the tutorial hand icon
    trained = 0

    while(trained < Amount):
        time.sleep(0.5)
        training_amount = req_text("Home.TroopTraining.TrainingAmount", read_kind="value")
        try:
            training_amount = int(training_amount[0][0])
            trained += training_amount
        except Exception as e:
            print(f"Training Amount can't be read, Only training for one time - {e}")
            tap_on_text("Home.TroopTraining.Train", wait=2)
            break

        tap_on_text("Home.TroopTraining.Train", wait=2)
        status = tap_on_text("Home.TroopTraining.Speedup", wait=2)

        if status:
            status = tap_on_text("Home.TroopTraining.Speedup.QuickUse", wait=2)
        if status:
            tap_on_text("Home.TroopTraining.Speedup.QuickUse.Use", wait=2)
        


def train_lancer(Amount):

    recalibrate()

    # threshold=0.5 scored 0.518 against the city map with no panel open, so
    # this "succeeded" and everything below ran on the wrong view. The template
    # is pinned at 0.85 in template_config.json; prove the panel by reading a tab.
    tap_on_template("Global.SidePanel", wait=2)
    if not side_panel_is_open():
        print("Side panel did not open, ending the task...")
        return None

    tap_on_text("Lancer", rois=[side_panel], wait=2)
    for i in range(3):
        tap_screen(50, 48.78)
        time.sleep(0.3)
    tap_on_text("Train", rois = [training_menu], wait=2)
    
    tap_screen(50.93, 44.72)            # Taping at the middle of the screen to remove the tutorial hand icon
    trained = 0

    while(trained < Amount):
        time.sleep(0.5)
        training_amount = req_text("Home.TroopTraining.TrainingAmount", read_kind="value")
        try:
            training_amount = int(training_amount[0][0])
            trained += training_amount
        except Exception as e:
            print(f"Training Amount can't be read, Only training for one time - {e}")
            tap_on_text("Home.TroopTraining.Train", wait=2)
            break

        tap_on_text("Home.TroopTraining.Train", wait=2)
        status = tap_on_text("Home.TroopTraining.Speedup", wait=2)

        if status:
            status = tap_on_text("Home.TroopTraining.Speedup.QuickUse", wait=2)
        if status:
            tap_on_text("Home.TroopTraining.Speedup.QuickUse.Use", wait=2)



def train_marksman(Amount):
    
    recalibrate()

    # threshold=0.5 scored 0.518 against the city map with no panel open, so
    # this "succeeded" and everything below ran on the wrong view. The template
    # is pinned at 0.85 in template_config.json; prove the panel by reading a tab.
    tap_on_template("Global.SidePanel", wait=2)
    if not side_panel_is_open():
        print("Side panel did not open, ending the task...")
        return None

    tap_on_text("Marksman", rois=[side_panel], wait=2)
    for i in range(3):
        tap_screen(50.0, 48.78)
        time.sleep(0.3)
    tap_on_text("Train", rois = [training_menu], wait=2)

    tap_screen(50.93, 44.72)         # remove the tutorial hand icon
    trained = 0

    while(trained < Amount):
        time.sleep(0.5)
        training_amount = req_text("Home.TroopTraining.TrainingAmount", read_kind="value")
        try:
            training_amount = int(training_amount[0][0])
            trained += training_amount
        except Exception as e:
            print(f"Training Amount can't be read, Only training for one time - {e}")
            tap_on_text("Home.TroopTraining.Train", wait=2)
            break

        tap_on_text("Home.TroopTraining.Train", wait=2)
        status = tap_on_text("Home.TroopTraining.Speedup", wait=2)

        if status:
            status = tap_on_text("Home.TroopTraining.Speedup.QuickUse", wait=2)
        if status:
            tap_on_text("Home.TroopTraining.Speedup.QuickUse.Use", wait=2)


