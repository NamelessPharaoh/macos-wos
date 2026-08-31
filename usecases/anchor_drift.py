"""Diagnostic task: report how far the UI has drifted from the recorded ROIs.

Thin on purpose. The measurement lives in core/anchor_drift.py because two
callers need it and neither is a game chore -- recalibrate() uses it to explain a
failed homepage search, and this task exists so a human can ask the same question
on demand while retuning the screen-adaptation slider.

Running it through the task menu rather than as a standalone script is what makes
it cheap: run.sh already gates the framebuffer, starts the OCR server on the right
port, and tears it down again. A separate entry point would have to duplicate all
three.
"""
from core.recalibrate import recalibrate

from core.anchor_drift import format_report, measure_drift


def report_anchor_drift():
    # Anchors are home-screen labels, so get there first -- otherwise the read is
    # measuring whatever page the emulator happened to be parked on.
    recalibrate()
    report = measure_drift()
    print(format_report(report))
    return report.verdict
