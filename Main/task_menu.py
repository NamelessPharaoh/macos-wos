from dataclasses import dataclass
import os
import re

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from usecases.exploration import (
    claim_exploration_idle_income,
    continue_exploring,
)
from usecases.alliance import (
    tech_contribution,
    auto_join,
    collect_chests,
    collect_triumph,
    help,
)
from usecases.vip import collect_vip_rewards
from usecases.free_claims import sweep_free_claims
from usecases.anchor_drift import report_anchor_drift
from usecases.heal import heal
from usecases.arena import arena
from usecases.mail import collect_mail_rewards
from usecases.training_troops import train
from usecases.collect import (
    collect_missions_reward,
    collect_life_essence,
)
from usecases.chief_order import activate_chief_order
from usecases.pet import collect_ally_treasure, start_pet_exploration
from usecases.labyrinth import labyrinth
from usecases.gather import gather
from core import capability
from core.player_profile import get_gather_flags, load_profile


@dataclass(frozen=True)
class TaskSpec:
    """One runnable routine.

    `gate` names the game feature this task needs, resolved against
    docs/knowledge/feature-unlocks.json, or one of the capability sentinels
    (ALWAYS = no game gate, UNKNOWN = a gate may exist but is unverified).
    Both sentinels fail open.

    It defaults to None only so existing positional construction keeps working
    (tests/test_chief_order.py builds TaskSpec with four positional args). Every
    entry in TASKS must still declare it explicitly — tests/test_capability.py
    fails if one is left at the default, so a new task cannot gate by accident.
    """

    key: str
    title: str
    description: str
    runner: object
    gate: str = None


console = Console()


def _normalize(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _run_gather(current_player_id):
    profile = load_profile(current_player_id)
    remove_hero, equalize = get_gather_flags(profile)
    gather(remove_hero=remove_hero, equalize=equalize, profile=profile)


TASKS = [
    TaskSpec("vip", "VIP Rewards", "Collect VIP rewards before anything else.", lambda _player_id: collect_vip_rewards(), gate="UNKNOWN"),
    TaskSpec("free_claims", "Free Claims Sweep", "Follow home-screen red dots and claim what is free.", lambda _player_id: sweep_free_claims(), gate="ALWAYS"),
    TaskSpec("anchor_drift", "Anchor Drift Check", "Measure how far the UI has moved from the recorded ROIs.", lambda _player_id: report_anchor_drift(), gate="ALWAYS"),
    TaskSpec("exploration_idle", "Exploration Idle Income", "Claim passive exploration income.", lambda _player_id: claim_exploration_idle_income(), gate="exploration"),
    TaskSpec("exploration_continue", "Continue Exploring", "Resume exploration progress.", lambda _player_id: continue_exploring(), gate="exploration"),
    TaskSpec("mail", "Mail Rewards", "Collect mailbox rewards.", lambda _player_id: collect_mail_rewards(), gate="UNKNOWN"),
    TaskSpec("life_essence", "Life Essence", "Collect life essence.", lambda _player_id: collect_life_essence(), gate="daybreak_island"),
    TaskSpec("training", "Train Troops", "Run the troop training routine.", lambda _player_id: train(), gate="infantry_camp"),
    TaskSpec("arena", "Arena", "Enter the arena routine.", lambda _player_id: arena(), gate="arena_of_glory"),
    TaskSpec("chief_order", "Chief Order", "Activate chief order tasks.", lambda _player_id: activate_chief_order(), gate="chiefs_house"),
    TaskSpec("pet_treasure", "Ally Treasure", "Collect ally treasure.", lambda _player_id: collect_ally_treasure(), gate="beast_cage"),
    TaskSpec("pet_exploration", "Pet Exploration", "Start pet exploration.", lambda _player_id: start_pet_exploration(), gate="beast_cage"),
    TaskSpec("labyrinth", "Labyrinth", "Run the labyrinth routine.", lambda _player_id: labyrinth(), gate="labyrinth"),
    TaskSpec("alliance_join", "Alliance Auto Join", "Auto-join alliance activity.", lambda _player_id: auto_join(), gate="alliance"),
    TaskSpec("alliance_chests", "Alliance Chests", "Collect alliance chests.", lambda _player_id: collect_chests(), gate="alliance"),
    TaskSpec("alliance_tech", "Alliance Tech", "Contribute to alliance tech.", lambda _player_id: tech_contribution(), gate="alliance"),
    TaskSpec("alliance_help", "Alliance Help", "Send alliance help.", lambda _player_id: help(), gate="UNKNOWN"),
    TaskSpec("alliance_triumph", "Alliance Triumph", "Collect triumph rewards.", lambda _player_id: collect_triumph(), gate="alliance"),
    TaskSpec("heal", "Heal", "Run healing workflow.", lambda _player_id: heal(), gate="infirmary"),
    TaskSpec("gather", "World Gather", "Gather resources with the current character rules.", _run_gather, gate="ALWAYS"),
    TaskSpec("missions", "Missions Reward", "Collect mission rewards.", lambda _player_id: collect_missions_reward(), gate="daily_missions"),
]


def _render_menu():
    table = Table(
        title="Available tasks",
        header_style="bold magenta",
        border_style="bright_blue",
        expand=False,
    )
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Task", style="bold white")
    table.add_column("Description", style="dim")

    for index, task in enumerate(TASKS, start=1):
        table.add_row(str(index), task.title, task.description)

    console.print(
        Panel.fit(
            "[bold cyan]Choose which tasks to run.[/bold cyan]\n"
            "Enter numbers or names separated by commas.\n"
            "[bold green]Press Enter for the full default list.[/bold green]",
            title="[bold magenta]Task Selector[/bold magenta]",
            border_style="bright_blue",
        )
    )
    console.print(table)


def _select_tasks(raw_input):
    """Returns (tasks, was_explicit).

    was_explicit distinguishes "the user typed these task names" from "run the
    default list", which the capability gate needs: an explicitly named task is
    a human overriding a guess made from community data, so it warns and runs
    instead of skipping. Without the flag the two cases are indistinguishable —
    an empty input and a full enumeration both yield the same 21 tasks.
    """
    normalized = raw_input.strip().lower()
    if not normalized or normalized in {"all", "default", "*"}:
        return TASKS, False

    selected_indexes = []
    seen = set()
    invalid_tokens = []

    tokens = [token for token in re.split(r"[,\s]+", normalized) if token.strip()]
    for token in tokens:
        token = token.strip()
        matched_index = None

        if token.isdigit():
            numeric_index = int(token) - 1
            if 0 <= numeric_index < len(TASKS):
                matched_index = numeric_index

        if matched_index is None:
            normalized_token = _normalize(token)
            for index, task in enumerate(TASKS):
                if normalized_token in {_normalize(task.key), _normalize(task.title)}:
                    matched_index = index
                    break

        if matched_index is None:
            invalid_tokens.append(token)
            continue

        if matched_index not in seen:
            selected_indexes.append(matched_index)
            seen.add(matched_index)

    if invalid_tokens:
        raise ValueError(f"Unknown selection: {', '.join(invalid_tokens)}")

    if not selected_indexes:
        raise ValueError("No valid task selections were provided.")

    return [TASKS[index] for index in selected_indexes], True


def prompt_task_selection():
    while True:
        _render_menu()
        raw_input = Prompt.ask(
            "[bold yellow]Task selection[/bold yellow]",
            default="",
            show_default=False,
        )

        try:
            selected_tasks, was_explicit = _select_tasks(raw_input)
        except ValueError as exc:
            console.print(f"[bold red]❌ {exc}[/bold red]")
            continue

        selected_panel = "\n".join(
            f"[bold cyan]{index + 1}.[/bold cyan] {task.title}"
            for index, task in enumerate(selected_tasks)
        )
        console.print(
            Panel.fit(
                selected_panel,
                title="[bold green]Selected Task Plan[/bold green]",
                border_style="green",
            )
        )
        return selected_tasks, was_explicit


GATE_ENV = "WOS_CAPABILITY_GATE"


def gating_enabled():
    """The gate is on unless WOS_CAPABILITY_GATE=0.

    Rollback switch: if a wrong knowledge-base value or a bad furnace read makes
    the gate skip something it should not, this restores today's behaviour
    without a revert. Same convention as OCR_CAPTURE_TOOL / OCR_RAM_CAP_GB.
    """
    return os.environ.get(GATE_ENV, "1").strip() != "0"


def run_selected_tasks(current_player_id, selected_tasks, was_explicit=False):
    gate_on = gating_enabled()
    profile = load_profile(current_player_id) if gate_on else None
    table = None

    if gate_on:
        table, warnings = capability.load_table()
        # capability.evaluate is pure and prints nothing, so its warnings ride
        # out on the verdict and get drained here.
        for warning in warnings:
            console.print(f"[bold yellow]{warning}[/bold yellow]")
    else:
        console.print(f"[bold yellow]{GATE_ENV}=0 — capability gate off, "
                      f"running every selected task[/bold yellow]")

    for task in selected_tasks:
        if gate_on:
            verdict = capability.evaluate(task.gate, profile, table=table)
            if not verdict.should_run:
                if was_explicit:
                    # A human naming the task outranks a guess made from
                    # community-sourced data. Say what the gate thought, then
                    # run it: this is also how you test a suspect gate entry.
                    console.print(
                        f"[bold yellow]{task.key}: {verdict.reason} — "
                        f"you asked for it explicitly, running anyway[/bold yellow]"
                    )
                else:
                    # A skipped task otherwise produces no output at all, which
                    # is indistinguishable from never having been selected.
                    console.print(
                        f"[yellow]SKIP {task.key}[/yellow] — {verdict.reason} "
                        f"[dim](source: {verdict.source})[/dim]"
                    )
                    continue
            elif verdict.source == "table" and any(
                    status == "unknown" for _, status, _ in verdict.checked):
                # Failing open on a half-readable gate is a decision worth
                # seeing; "unlocked, running" is not, so it stays quiet.
                console.print(f"[dim]RUN  {task.key} — {verdict.reason}[/dim]")

        console.print(
            Panel.fit(
                f"[bold white]Running[/bold white] [bold cyan]{task.title}[/bold cyan]",
                border_style="bright_blue",
            )
        )
        try:
            task.runner(current_player_id)
        except Exception:
            # One broken task must not kill the whole run (observed live:
            # chief_order TypeError ended the session mid-list). Log loudly,
            # move on; the traceback stays in the run log for diagnosis.
            import traceback
            traceback.print_exc()
            console.print(
                f"[bold red]Task '{task.title}' crashed — continuing with the "
                f"remaining tasks.[/bold red]"
            )