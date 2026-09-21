from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import psutil

from .execution import ExecutionManager
from .models import ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from .visual_match import bgra_bytes_to_bgr
from .win32_input import DesktopAutomationUnavailable, WindowInfo, WindowSnapshot, Win32DesktopInput


@dataclass(slots=True)
class TargetObservation:
    index: int
    label: str
    image_bgr: np.ndarray
    client_width: int
    client_height: int


@dataclass(slots=True)
class TargetSessionResult:
    request_id: str
    status: ExecutionStatus
    duration: float
    target_title: str
    client_width: int
    client_height: int
    actions_total: int
    actions_completed: int
    logs: list[str] = field(default_factory=list)
    observations: list[TargetObservation] = field(default_factory=list)
    note: str = ""
    browser_restored: bool = False
    target_left_open: bool = False


StatusCallback = Callable[[str], None]
DoneCallback = Callable[[TargetSessionResult], None]
StageCallback = Callable[[str], None]


class TargetSessionRunner:
    """Run a temporary UI interaction session against one launched process tree.

    The runner never accepts an arbitrary HWND from the LLM. The target window
    must belong to the process started for the current #Multiple directive or to
    one of its descendants.
    """

    def __init__(self, desktop: Win32DesktopInput, executor: ExecutionManager) -> None:
        self.desktop = desktop
        self.executor = executor
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._root_pid: int | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    @property
    def root_pid(self) -> int | None:
        with self._lock:
            return self._root_pid

    def cancel(self) -> None:
        """Stop remaining injected actions.

        Cancellation deliberately does not close the target application: a
        physical user intervention means the user has taken control and may want
        to inspect/interact with the visible program manually.
        """
        self._cancel.set()

    def start_async(
        self,
        request: ExecutionRequest,
        cwd: Path,
        actions: tuple[InteractionAction, ...],
        browser_snapshot: WindowSnapshot | None,
        *,
        window_timeout_seconds: float,
        launch_settle_seconds: float,
        action_delay_seconds: float,
        close_timeout_seconds: float,
        restore_delay_seconds: float,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_stage: StageCallback | None = None,
    ) -> None:
        if self.running:
            raise RuntimeError("Une session Target App est déjà en cours.")
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._worker,
            args=(
                request,
                cwd,
                actions,
                browser_snapshot,
                float(window_timeout_seconds),
                float(launch_settle_seconds),
                float(action_delay_seconds),
                float(close_timeout_seconds),
                float(restore_delay_seconds),
                on_status,
                on_done,
                on_stage,
            ),
            daemon=True,
            name="target-app-session",
        )
        self._thread.start()

    @staticmethod
    def _sleep_cancelable(seconds: float, cancel: threading.Event, deadline: float) -> bool:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if cancel.is_set() or time.monotonic() >= deadline:
                return False
            step = min(0.05, remaining)
            time.sleep(step)
            remaining -= step
        return not cancel.is_set() and time.monotonic() < deadline

    @staticmethod
    def _process_tree_pids(root_pid: int, known: set[int]) -> set[int]:
        pids = set(known)
        pids.add(int(root_pid))
        try:
            root = psutil.Process(root_pid)
            pids.update(child.pid for child in root.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return pids

    def _wait_for_window(
        self,
        root_pid: int,
        known_pids: set[int],
        timeout_seconds: float,
        deadline: float,
    ) -> tuple[WindowInfo | None, set[int]]:
        end = min(deadline, time.monotonic() + max(0.5, timeout_seconds))
        while time.monotonic() < end and not self._cancel.is_set():
            known_pids = self._process_tree_pids(root_pid, known_pids)
            window = self.desktop.best_window_for_pids(known_pids)
            if window is not None:
                return window, known_pids
            time.sleep(0.10)
        return None, known_pids

    @staticmethod
    def _terminate_known_processes(pids: set[int], timeout: float) -> None:
        processes: list[psutil.Process] = []
        for pid in sorted(pids, reverse=True):
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for proc in processes:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _gone, alive = psutil.wait_procs(processes, timeout=max(0.1, timeout))
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def _worker(
        self,
        request: ExecutionRequest,
        cwd: Path,
        actions: tuple[InteractionAction, ...],
        browser_snapshot: WindowSnapshot | None,
        window_timeout_seconds: float,
        launch_settle_seconds: float,
        action_delay_seconds: float,
        close_timeout_seconds: float,
        restore_delay_seconds: float,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_stage: StageCallback | None = None,
    ) -> None:
        started = time.monotonic()
        deadline = started + max(1, int(request.timeout))
        logs: list[str] = []
        observations: list[TargetObservation] = []
        target: WindowInfo | None = None
        known_pids: set[int] = set()
        status = ExecutionStatus.ERROR
        note = ""
        completed = 0
        browser_restored = False
        target_left_open = False
        proc = None

        try:
            on_status("Lancement de l'application cible…")
            proc = self.executor.launch_target(request, cwd)
            with self._lock:
                self._root_pid = int(proc.pid)
            known_pids.add(int(proc.pid))
            logs.append(f"LAUNCH PID {proc.pid}: {request.command}")

            if not self._sleep_cancelable(launch_settle_seconds, self._cancel, deadline):
                raise _TargetCancelledOrTimeout()

            target, known_pids = self._wait_for_window(
                int(proc.pid), known_pids, window_timeout_seconds, deadline
            )
            if target is None:
                if self._cancel.is_set():
                    raise _TargetCancelledOrTimeout()
                raise DesktopAutomationUnavailable(
                    "Aucune fenêtre visible appartenant au processus lancé n'a été détectée."
                )
            self.desktop.activate_window(target.hwnd)
            target = self.desktop.window_info(target.hwnd)
            if on_stage is not None:
                on_stage("running")
            logs.append(
                f"WINDOW hwnd={target.hwnd} pid={target.pid} title={target.title!r} "
                f"client={target.client_rect.width}x{target.client_rect.height}"
            )
            on_status(
                f"Cible détectée : {target.title or 'fenêtre sans titre'} "
                f"({target.client_rect.width}×{target.client_rect.height})"
            )

            for index, action in enumerate(actions, start=1):
                if self._cancel.is_set() or time.monotonic() >= deadline:
                    raise _TargetCancelledOrTimeout()

                known_pids = self._process_tree_pids(int(proc.pid), known_pids)
                if not self.desktop.window_exists(target.hwnd):
                    replacement = self.desktop.best_window_for_pids(known_pids)
                    if replacement is None:
                        raise DesktopAutomationUnavailable("La fenêtre cible a disparu pendant la séquence.")
                    target = replacement
                    logs.append(f"WINDOW_REBOUND hwnd={target.hwnd} pid={target.pid}")

                on_status(f"Target App — action {index}/{len(actions)} : {action.kind.value}")
                if action.kind == InteractionKind.CLICK:
                    assert action.x is not None and action.y is not None
                    point = self.desktop.click_window_client(target.hwnd, action.x, action.y)
                    logs.append(f"{index}. CLICK {action.x};{action.y} -> screen {point.x};{point.y} OK")
                elif action.kind == InteractionKind.TYPE_INPUT:
                    self.desktop.activate_window(target.hwnd)
                    self.desktop.type_text(action.text)
                    logs.append(f"{index}. TYPE_INPUT {len(action.text)} chars OK")
                elif action.kind == InteractionKind.KEY:
                    self.desktop.activate_window(target.hwnd)
                    self.desktop.press_key_chord(action.key)
                    logs.append(f"{index}. KEY {action.key} OK")
                elif action.kind == InteractionKind.WAIT:
                    if not self._sleep_cancelable(action.wait_ms / 1000.0, self._cancel, deadline):
                        raise _TargetCancelledOrTimeout()
                    logs.append(f"{index}. WAIT {action.wait_ms} ms OK")
                elif action.kind == InteractionKind.OBSERVE:
                    frame = self.desktop.capture_window_client(target.hwnd)
                    image = bgra_bytes_to_bgr(frame.pixels, frame.width, frame.height).copy()
                    label = action.label or f"observation-{len(observations) + 1}"
                    observations.append(
                        TargetObservation(
                            index=len(observations) + 1,
                            label=label,
                            image_bgr=image,
                            client_width=frame.width,
                            client_height=frame.height,
                        )
                    )
                    logs.append(f"{index}. OBSERVE {label!r} {frame.width}x{frame.height} OK")
                completed = index

                if action.kind != InteractionKind.WAIT and action_delay_seconds > 0:
                    if not self._sleep_cancelable(action_delay_seconds, self._cancel, deadline):
                        raise _TargetCancelledOrTimeout()

            status = ExecutionStatus.SUCCESS
            note = "Séquence Target App terminée."

        except _TargetCancelledOrTimeout:
            if self._cancel.is_set():
                status = ExecutionStatus.CANCELLED
                note = "Intervention utilisateur : actions restantes annulées, application cible laissée ouverte."
                target_left_open = True
            else:
                status = ExecutionStatus.TIMEOUT
                note = "Timeout de la session Target App."
        except Exception as exc:
            status = ExecutionStatus.ERROR
            note = str(exc)
            logs.append(f"ERROR: {exc}")
        finally:
            cancelled_by_user = status == ExecutionStatus.CANCELLED and self._cancel.is_set()
            if not cancelled_by_user:
                if on_stage is not None:
                    try:
                        on_stage("restoring")
                    except Exception:
                        pass
                try:
                    on_status("Fermeture de la cible et restauration du navigateur…")
                except Exception:
                    pass
                try:
                    if proc is not None:
                        known_pids = self._process_tree_pids(int(proc.pid), known_pids)
                    if target is not None and self.desktop.window_exists(target.hwnd):
                        self.desktop.close_window(target.hwnd)
                    # Give the application a brief graceful-close opportunity,
                    # then terminate only the process tree created by #Multiple.
                    if close_timeout_seconds > 0:
                        time.sleep(min(close_timeout_seconds, 1.0))
                    self._terminate_known_processes(known_pids, max(0.1, close_timeout_seconds))
                except Exception as exc:
                    logs.append(f"CLEANUP_WARNING: {exc}")
                try:
                    if restore_delay_seconds > 0:
                        time.sleep(restore_delay_seconds)
                    browser_restored = self.desktop.restore_window(browser_snapshot)
                    if browser_snapshot is not None and not browser_restored:
                        logs.append("RESTORE_ERROR: impossible de restaurer la fenêtre navigateur.")
                except Exception as exc:
                    logs.append(f"RESTORE_ERROR: {exc}")
                    browser_restored = False
            with self._lock:
                self._root_pid = None

            if browser_snapshot is not None and not cancelled_by_user and not browser_restored:
                status = ExecutionStatus.ERROR
                if note:
                    note += " "
                note += "La fenêtre navigateur n'a pas pu être restaurée ; reprise Auto suspendue."

            title = target.title if target is not None else ""
            client_width = target.client_rect.width if target is not None else 0
            client_height = target.client_rect.height if target is not None else 0
            result = TargetSessionResult(
                request_id=request.request_id,
                status=status,
                duration=time.monotonic() - started,
                target_title=title,
                client_width=client_width,
                client_height=client_height,
                actions_total=len(actions),
                actions_completed=completed,
                logs=logs,
                observations=observations,
                note=note,
                browser_restored=browser_restored,
                target_left_open=target_left_open,
            )
            try:
                on_done(result)
            except Exception:
                pass


class _TargetCancelledOrTimeout(RuntimeError):
    pass


def format_multiple_result(result: TargetSessionResult, goal_reminder: str = "") -> str:
    lines = [
        "#MultipleResult",
        "Protocol: 1",
        f"ID: {result.request_id}",
        f"Status: {result.status.value}",
        f"Duration: {result.duration:.2f}s",
        f"TargetWindow: {result.target_title or '<unknown>'}",
        f"ClientSize: {result.client_width}x{result.client_height}",
        f"Actions: {result.actions_completed}/{result.actions_total}",
        f"Observations: {len(result.observations)}",
        f"BrowserRestored: {'YES' if result.browser_restored else 'NO'}",
    ]
    if result.note:
        lines += ["", "NOTE:", result.note]
    if result.logs:
        lines += ["", "ACTION_LOG:", *result.logs]
    if result.observations:
        lines += [
            "",
            "#VisualObservation",
            "Une image jointe contient les captures #Observe numérotées. Les coordonnées #Click sont relatives à la zone cliente montrée.",
        ]
    if goal_reminder.strip():
        lines += ["", "#GoalReminder", goal_reminder.strip()]
    return "\n".join(lines)


def compose_observation_sheet(
    observations: list[TargetObservation],
    *,
    max_width: int = 2400,
    max_height: int = 6000,
) -> np.ndarray | None:
    """Compose labelled observations while preserving a known coordinate scale.

    #Click coordinates always refer to the original client size.  The sheet may
    need to shrink large/multiple captures for clipboard transport, so every
    card states the exact rendering scale instead of silently distorting the
    coordinate system.  One common scale is used for all cards.
    """
    valid = [obs for obs in observations[:8] if obs.image_bgr is not None and obs.image_bgr.size]
    if not valid:
        return None

    header_h = 52
    max_source_width = max(int(obs.image_bgr.shape[1]) for obs in valid)
    source_height_sum = sum(int(obs.image_bgr.shape[0]) for obs in valid)
    available_image_height = max(1, int(max_height) - header_h * len(valid))
    scale = min(
        1.0,
        max(1, int(max_width)) / max(1, max_source_width),
        available_image_height / max(1, source_height_sum),
    )
    scale = max(0.05, float(scale))

    cards: list[np.ndarray] = []
    for obs in valid:
        source = obs.image_bgr
        h, w = source.shape[:2]
        rendered_w = max(1, int(round(w * scale)))
        rendered_h = max(1, int(round(h * scale)))
        if rendered_w != w or rendered_h != h:
            image = cv2.resize(source, (rendered_w, rendered_h), interpolation=cv2.INTER_AREA)
        else:
            image = source
        card = np.zeros((image.shape[0] + header_h, image.shape[1], 3), dtype=np.uint8)
        card[:] = 28
        card[header_h:] = image
        label = (
            f"Observe {obs.index}: {obs.label} | client {obs.client_width}x{obs.client_height} "
            f"| sheet-scale {scale:.3f}"
        )
        cv2.putText(card, label[:150], (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (235, 235, 235), 1, cv2.LINE_AA)
        cards.append(card)

    width = max(card.shape[1] for card in cards)
    padded: list[np.ndarray] = []
    for card in cards:
        if card.shape[1] < width:
            pad = np.zeros((card.shape[0], width - card.shape[1], 3), dtype=np.uint8)
            pad[:] = 28
            card = np.concatenate([card, pad], axis=1)
        padded.append(card)
    return np.concatenate(padded, axis=0)
