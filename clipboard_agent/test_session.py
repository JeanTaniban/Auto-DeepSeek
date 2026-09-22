from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import psutil

from .execution import ExecutionManager
from .models import ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from .target_session import TargetObservation, compose_observation_sheet
from .visual_match import bgra_bytes_to_bgr
from .visual_watch import VisualStabilityTracker, VisualState
from .win32_input import DesktopAutomationUnavailable, WindowInfo, Win32DesktopInput
from .workspace import WindowWorkspaceManager


_CHECKPOINT_RE = re.compile(r"\[\[CAR_CHECKPOINT:([A-Za-z0-9_.:-]{1,80})\]\]")
_SCREENSHOT_RE = re.compile(r"\[\[CAR_SCREENSHOT:([^\]\r\n]{1,80})\]\]")


class TestSessionState(str, Enum):
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    ACTIVE_BACKGROUND = "ACTIVE_BACKGROUND"
    ACTIVE_FOREGROUND = "ACTIVE_FOREGROUND"
    CLOSING = "CLOSING"
    LOST = "LOST"


@dataclass(slots=True)
class TestSessionResult:
    request_id: str
    session_id: str
    operation: str
    status: ExecutionStatus
    duration: float
    target_title: str = ""
    client_width: int = 0
    client_height: int = 0
    actions_total: int = 0
    actions_completed: int = 0
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    checkpoints: list[str] = field(default_factory=list)
    observations: list[TargetObservation] = field(default_factory=list)
    note: str = ""
    session_active: bool = False
    llm_restored: bool = False


StatusCallback = Callable[[str], None]
DoneCallback = Callable[[TestSessionResult], None]
StageCallback = Callable[[str], None]


class PersistentTestSession:
    """One persistent development-app process/window across several LLM turns."""

    def __init__(
        self,
        desktop: Win32DesktopInput,
        executor: ExecutionManager,
        workspace: WindowWorkspaceManager,
    ) -> None:
        self.desktop = desktop
        self.executor = executor
        self.workspace = workspace
        self._lock = threading.RLock()
        self._state = TestSessionState.CLOSED
        self._proc = None
        self._request: ExecutionRequest | None = None
        self._cwd: Path | None = None
        self._target: WindowInfo | None = None
        self._known_pids: set[int] = set()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._stdout_sent = 0
        self._stderr_sent = 0
        self._checkpoints: list[str] = []
        self._checkpoint_sent = 0
        self._pending_screenshots: list[str] = []
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._output_cv = threading.Condition(self._lock)

    @property
    def state(self) -> TestSessionState:
        with self._lock:
            return self._state

    @property
    def active(self) -> bool:
        return self.state in {
            TestSessionState.OPENING,
            TestSessionState.ACTIVE_BACKGROUND,
            TestSessionState.ACTIVE_FOREGROUND,
            TestSessionState.CLOSING,
        }

    @property
    def busy(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._request.request_id if self._request else ""

    @property
    def target_hwnd(self) -> int | None:
        with self._lock:
            return self._target.hwnd if self._target else None

    def cancel_interaction(self) -> None:
        self._cancel.set()

    def force_close(self) -> None:
        """Best-effort synchronous cleanup used when the Relay itself exits.

        Normal protocol flow should use ``close_async`` so stdout/stderr and a
        structured result reach the LLM. This method intentionally skips those
        semantics and only prevents an orphaned development process tree.
        """
        self._cancel.set()
        with self._lock:
            proc = self._proc
            target = self._target
            known = set(self._known_pids)
        try:
            if target is not None and self.desktop.window_exists(target.hwnd):
                self.desktop.close_window(target.hwnd)
        except Exception:
            pass
        if proc is not None:
            try:
                known.update(self._process_tree_pids(int(proc.pid), known))
                self._terminate_known_processes(known, 0.8)
            except Exception:
                pass
        with self._lock:
            self._proc = None
            self._target = None
            self._known_pids.clear()
            self._request = None
            self._cwd = None
            self._state = TestSessionState.CLOSED
            self._pending_screenshots.clear()

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

    def _set_state(self, state: TestSessionState) -> None:
        with self._lock:
            self._state = state

    def _start_reader(self, stream, channel: str) -> None:
        def pump() -> None:
            try:
                for line in iter(stream.readline, ""):
                    with self._output_cv:
                        if channel == "stdout":
                            self._stdout.append(line)
                            for name in _CHECKPOINT_RE.findall(line):
                                self._checkpoints.append(name)
                            for label in _SCREENSHOT_RE.findall(line):
                                self._pending_screenshots.append(label.strip())
                        else:
                            self._stderr.append(line)
                        self._output_cv.notify_all()
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        threading.Thread(target=pump, daemon=True, name=f"test-session-{channel}").start()

    def _current_process_alive(self) -> bool:
        with self._lock:
            proc = self._proc
            known = set(self._known_pids)
        if proc is not None and proc.poll() is None:
            return True
        for pid in known:
            try:
                child = psutil.Process(int(pid))
                if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _output_delta(self) -> tuple[str, str, list[str]]:
        with self._lock:
            out = "".join(self._stdout[self._stdout_sent:])
            err = "".join(self._stderr[self._stderr_sent:])
            checkpoints = list(self._checkpoints[self._checkpoint_sent:])
            self._stdout_sent = len(self._stdout)
            self._stderr_sent = len(self._stderr)
            self._checkpoint_sent = len(self._checkpoints)
        return out, err, checkpoints

    def full_output(self) -> tuple[str, str]:
        with self._lock:
            return "".join(self._stdout), "".join(self._stderr)

    def _refresh_target(self) -> WindowInfo | None:
        with self._lock:
            proc = self._proc
            target = self._target
            known = set(self._known_pids)
        if proc is None:
            return None
        known = self._process_tree_pids(int(proc.pid), known)
        replacement = target
        if replacement is None or not self.desktop.window_exists(replacement.hwnd):
            replacement = self.desktop.best_window_for_pids(known)
        if replacement is not None:
            replacement = self.desktop.window_info(replacement.hwnd)
        with self._lock:
            self._known_pids = known
            self._target = replacement
        return replacement

    def _wait_for_window(self, deadline: float, timeout_seconds: float) -> WindowInfo | None:
        timeout_seconds = max(0.5, float(timeout_seconds))
        end = min(deadline, time.monotonic() + timeout_seconds)
        process_exit_seen_at: float | None = None
        exit_grace_seconds = min(5.0, max(2.0, timeout_seconds * 0.10))
        while time.monotonic() < end and not self._cancel.is_set():
            target = self._refresh_target()
            if target is not None:
                return target
            if self._current_process_alive():
                process_exit_seen_at = None
            else:
                now = time.monotonic()
                if process_exit_seen_at is None:
                    process_exit_seen_at = now
                elif now - process_exit_seen_at >= exit_grace_seconds:
                    return None
            time.sleep(0.15)
        return None

    def _wait_checkpoint(self, name: str, deadline: float) -> bool:
        with self._output_cv:
            while time.monotonic() < deadline and not self._cancel.is_set():
                if name in self._checkpoints:
                    return True
                if not self._current_process_alive():
                    return False
                self._output_cv.wait(timeout=min(0.15, max(0.01, deadline - time.monotonic())))
        return False

    def _wait_visual_stable(
        self,
        target: WindowInfo,
        deadline: float,
        *,
        stable_seconds: float,
        poll_ms: int,
    ) -> bool:
        rect = self.desktop.client_rect_screen(target.hwnd)
        baseline = self.desktop.capture_signature(rect)
        tracker = VisualStabilityTracker(
            stable_seconds=max(0.2, float(stable_seconds)),
            timeout_seconds=max(0.5, deadline - time.monotonic()),
            require_motion=False,
        )
        tracker.start(baseline, time.monotonic())
        while time.monotonic() < deadline and not self._cancel.is_set():
            time.sleep(max(0.05, int(poll_ms) / 1000.0))
            if not self.desktop.window_exists(target.hwnd):
                return False
            frame = self.desktop.capture_signature(self.desktop.client_rect_screen(target.hwnd))
            obs = tracker.observe(frame, time.monotonic())
            if obs.state == VisualState.STABLE:
                return True
            if obs.state == VisualState.TIMEOUT:
                return False
        return False

    def _wait_readiness(
        self,
        target: WindowInfo,
        ready: str,
        deadline: float,
        *,
        visual_stable_seconds: float,
        visual_poll_ms: int,
        settle_seconds: float,
    ) -> None:
        ready = (ready or "auto").lower()

        # Give the freshly activated window a deterministic short settle period
        # before evaluating readiness.  This absorbs focus/paint latency without
        # pretending that a fixed delay alone proves the application is ready.
        if settle_seconds > 0 and not self._sleep_cancelable(settle_seconds, self._cancel, deadline):
            raise _TestCancelledOrTimeout()

        if ready.startswith("checkpoint:"):
            name = ready.split(":", 1)[1]
            if not self._wait_checkpoint(name, deadline):
                raise DesktopAutomationUnavailable(f"Checkpoint de readiness non reçu : {name}")
            # A code checkpoint tells us the logical state is ready; require the
            # actual client surface to settle as well before observing/clicking.
            if not self._wait_visual_stable(
                target,
                deadline,
                stable_seconds=visual_stable_seconds,
                poll_ms=visual_poll_ms,
            ):
                raise DesktopAutomationUnavailable(
                    f"Checkpoint {name} reçu, mais l'interface n'est pas devenue visuellement stable avant le timeout."
                )
        elif ready.startswith("delay:"):
            delay_ms = int(ready.split(":", 1)[1])
            if not self._sleep_cancelable(delay_ms / 1000.0, self._cancel, deadline):
                raise _TestCancelledOrTimeout()
        elif ready == "auto":
            if not self._wait_visual_stable(
                target,
                deadline,
                stable_seconds=visual_stable_seconds,
                poll_ms=visual_poll_ms,
            ):
                raise DesktopAutomationUnavailable("L'interface cible n'est pas devenue visuellement stable avant le timeout.")
        elif ready != "window":
            raise DesktopAutomationUnavailable(f"Mode de readiness inconnu : {ready}")

    def _capture_observation(self, target: WindowInfo, label: str, observations: list[TargetObservation]) -> None:
        frame = self.desktop.capture_window_client(target.hwnd)
        image = bgra_bytes_to_bgr(frame.pixels, frame.width, frame.height).copy()
        observations.append(
            TargetObservation(
                index=len(observations) + 1,
                label=label or f"observation-{len(observations) + 1}",
                image_bgr=image,
                client_width=frame.width,
                client_height=frame.height,
            )
        )

    def _capture_pending_screenshots(self, target: WindowInfo, observations: list[TargetObservation]) -> None:
        with self._lock:
            labels = list(self._pending_screenshots)
            self._pending_screenshots.clear()
        for label in labels:
            self._capture_observation(target, label or "checkpoint-screenshot", observations)

    def _execute_actions(
        self,
        target: WindowInfo,
        actions: tuple[InteractionAction, ...],
        deadline: float,
        *,
        action_delay_seconds: float,
        observations: list[TargetObservation],
        logs: list[str],
    ) -> int:
        completed = 0
        self._capture_pending_screenshots(target, observations)
        for index, action in enumerate(actions, start=1):
            if self._cancel.is_set() or time.monotonic() >= deadline:
                raise _TestCancelledOrTimeout()
            target = self._refresh_target()
            if target is None:
                raise DesktopAutomationUnavailable("La fenêtre de la TestSession a disparu.")
            if not self.workspace.ensure_target_workspace(target.hwnd):
                raise DesktopAutomationUnavailable("Impossible d'activer le workspace Target App.")

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
                    raise _TestCancelledOrTimeout()
                logs.append(f"{index}. WAIT {action.wait_ms} ms OK")
            elif action.kind == InteractionKind.OBSERVE:
                self._capture_observation(target, action.label, observations)
                logs.append(f"{index}. OBSERVE {action.label or '<auto>'} OK")
            completed = index
            self._capture_pending_screenshots(target, observations)
            if action.kind != InteractionKind.WAIT and action_delay_seconds > 0:
                if not self._sleep_cancelable(action_delay_seconds, self._cancel, deadline):
                    raise _TestCancelledOrTimeout()
        return completed

    def open_async(
        self,
        request: ExecutionRequest,
        cwd: Path,
        actions: tuple[InteractionAction, ...],
        ready: str,
        *,
        window_timeout_seconds: float,
        visual_stable_seconds: float,
        visual_poll_ms: int,
        settle_seconds: float,
        action_delay_seconds: float,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_stage: StageCallback | None = None,
    ) -> None:
        with self._lock:
            if self._state != TestSessionState.CLOSED or self.busy:
                raise RuntimeError("Une TestSession est déjà ouverte ou en cours d'ouverture.")
            self._state = TestSessionState.OPENING
            self._cancel.clear()
            self._request = request
            self._cwd = cwd
            self._target = None
            self._known_pids.clear()
            self._stdout.clear(); self._stderr.clear(); self._checkpoints.clear(); self._pending_screenshots.clear()
            self._stdout_sent = self._stderr_sent = self._checkpoint_sent = 0

        def worker() -> None:
            started = time.monotonic()
            deadline = started + max(1, int(request.timeout))
            observations: list[TargetObservation] = []
            logs: list[str] = []
            status = ExecutionStatus.ERROR
            note = ""
            completed = 0
            llm_restored = False
            try:
                on_status("Ouverture de la TestSession…")
                proc = self.executor.launch_target_captured(request, cwd)
                with self._lock:
                    self._proc = proc
                    self._known_pids = {int(proc.pid)}
                if proc.stdout is not None: self._start_reader(proc.stdout, "stdout")
                if proc.stderr is not None: self._start_reader(proc.stderr, "stderr")
                logs.append(f"LAUNCH PID {proc.pid}: {request.command}")

                target = self._wait_for_window(deadline, window_timeout_seconds)
                if target is None:
                    if self._cancel.is_set():
                        raise _TestCancelledOrTimeout()
                    proc_exit = proc.poll()
                    if proc_exit is not None and not self._current_process_alive():
                        raise DesktopAutomationUnavailable(
                            "Le processus de TestSession s'est terminé avant l'apparition d'une fenêtre "
                            f"(ExitCode={proc_exit}). Consultez stdout/stderr ci-dessous."
                        )
                    raise DesktopAutomationUnavailable(
                        f"Aucune fenêtre de TestSession détectée après {float(window_timeout_seconds):.1f}s."
                    )
                if on_stage: on_stage("foreground")
                if not self.workspace.ensure_target_workspace(target.hwnd):
                    raise DesktopAutomationUnavailable("Impossible d'activer le workspace Target App.")
                self._set_state(TestSessionState.ACTIVE_FOREGROUND)
                on_status(f"TestSession — attente readiness {ready}…")
                self._wait_readiness(
                    target,
                    ready,
                    deadline,
                    visual_stable_seconds=visual_stable_seconds,
                    visual_poll_ms=visual_poll_ms,
                    settle_seconds=settle_seconds,
                )
                self._capture_pending_screenshots(target, observations)
                completed = self._execute_actions(
                    target, actions, deadline,
                    action_delay_seconds=action_delay_seconds,
                    observations=observations,
                    logs=logs,
                )
                if on_stage: on_stage("restoring")
                llm_restored = self.workspace.restore_llm_workspace()
                if not llm_restored:
                    reason = self.workspace.last_error or "raison Win32 inconnue"
                    raise DesktopAutomationUnavailable(
                        f"Impossible de restaurer le workspace LLM après ouverture de la TestSession : {reason}"
                    )
                self._set_state(TestSessionState.ACTIVE_BACKGROUND)
                status = ExecutionStatus.SUCCESS
                note = "TestSession ouverte et conservée en arrière-plan."
            except _TestCancelledOrTimeout:
                cancelled = self._cancel.is_set()
                status = ExecutionStatus.CANCELLED if cancelled else ExecutionStatus.TIMEOUT
                note = "Intervention utilisateur : TestSession laissée ouverte." if cancelled else "Timeout d'ouverture de la TestSession."
                if not cancelled:
                    llm_restored = self.workspace.restore_llm_workspace()
                if self._refresh_target() is not None and self._current_process_alive():
                    self._set_state(TestSessionState.ACTIVE_BACKGROUND if llm_restored else TestSessionState.ACTIVE_FOREGROUND)
                else:
                    self._set_state(TestSessionState.LOST)
            except Exception as exc:
                status = ExecutionStatus.ERROR
                note = str(exc)
                logs.append(f"ERROR: {exc}")
                if not self._cancel.is_set():
                    llm_restored = self.workspace.restore_llm_workspace()
                if self._refresh_target() is not None and self._current_process_alive():
                    self._set_state(TestSessionState.ACTIVE_BACKGROUND if llm_restored else TestSessionState.ACTIVE_FOREGROUND)
                else:
                    self._set_state(TestSessionState.LOST)
            out, err, checkpoints = self._output_delta()
            target = self._refresh_target()
            on_done(TestSessionResult(
                request_id=request.request_id,
                session_id=request.request_id,
                operation="OPENED",
                status=status,
                duration=time.monotonic() - started,
                target_title=target.title if target else "",
                client_width=target.client_rect.width if target else 0,
                client_height=target.client_rect.height if target else 0,
                actions_total=len(actions),
                actions_completed=completed,
                stdout=out,
                stderr=err,
                checkpoints=checkpoints,
                observations=observations,
                note=note,
                session_active=self.active,
                llm_restored=llm_restored,
            ))

        self._thread = threading.Thread(target=worker, daemon=True, name="persistent-test-open")
        self._thread.start()

    def actions_async(
        self,
        request_id: str,
        actions: tuple[InteractionAction, ...],
        *,
        timeout_seconds: float,
        action_delay_seconds: float,
        settle_seconds: float,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_stage: StageCallback | None = None,
    ) -> None:
        with self._lock:
            if self._state != TestSessionState.ACTIVE_BACKGROUND or self.busy:
                raise RuntimeError("Aucune TestSession disponible en arrière-plan pour ces actions.")
            self._state = TestSessionState.ACTIVE_FOREGROUND
            self._cancel.clear()
            session_id = self._request.request_id if self._request else ""

        def worker() -> None:
            started = time.monotonic()
            deadline = started + max(1.0, float(timeout_seconds))
            observations: list[TargetObservation] = []
            logs: list[str] = []
            completed = 0
            llm_restored = False
            status = ExecutionStatus.ERROR
            note = ""
            try:
                if not self._current_process_alive():
                    self._set_state(TestSessionState.LOST)
                    raise DesktopAutomationUnavailable("Le processus de la TestSession s'est terminé.")
                target = self._refresh_target()
                if target is None:
                    self._set_state(TestSessionState.LOST)
                    raise DesktopAutomationUnavailable("La fenêtre de la TestSession n'existe plus.")
                if on_stage: on_stage("foreground")
                if not self.workspace.ensure_target_workspace(target.hwnd):
                    raise DesktopAutomationUnavailable("Impossible d'activer le workspace Target App.")
                if settle_seconds > 0 and not self._sleep_cancelable(settle_seconds, self._cancel, deadline):
                    raise _TestCancelledOrTimeout()
                completed = self._execute_actions(
                    target, actions, deadline,
                    action_delay_seconds=action_delay_seconds,
                    observations=observations,
                    logs=logs,
                )
                if on_stage: on_stage("restoring")
                llm_restored = self.workspace.restore_llm_workspace()
                if not llm_restored:
                    reason = self.workspace.last_error or "raison Win32 inconnue"
                    raise DesktopAutomationUnavailable(
                        f"Impossible de restaurer le workspace LLM après les actions de test : {reason}"
                    )
                self._set_state(TestSessionState.ACTIVE_BACKGROUND)
                status = ExecutionStatus.SUCCESS
                note = "Actions exécutées ; TestSession toujours ouverte."
            except _TestCancelledOrTimeout:
                status = ExecutionStatus.CANCELLED if self._cancel.is_set() else ExecutionStatus.TIMEOUT
                note = "Intervention utilisateur : actions annulées, TestSession laissée au premier plan." if self._cancel.is_set() else "Timeout des actions TestSession."
                if self._current_process_alive():
                    self._set_state(TestSessionState.ACTIVE_FOREGROUND)
                else:
                    self._set_state(TestSessionState.LOST)
            except Exception as exc:
                status = ExecutionStatus.ERROR
                note = str(exc)
                logs.append(f"ERROR: {exc}")
                if self._current_process_alive():
                    # Technical error while still alive: restore browser if no
                    # user cancellation occurred, then keep the session usable.
                    if not self._cancel.is_set():
                        llm_restored = self.workspace.restore_llm_workspace()
                        self._set_state(TestSessionState.ACTIVE_BACKGROUND if llm_restored else TestSessionState.ACTIVE_FOREGROUND)
                else:
                    self._set_state(TestSessionState.LOST)
            out, err, checkpoints = self._output_delta()
            target = self._refresh_target()
            proc = self._proc
            exit_code = proc.poll() if proc is not None else None
            on_done(TestSessionResult(
                request_id=request_id,
                session_id=session_id,
                operation="ACTIONS",
                status=status,
                duration=time.monotonic() - started,
                target_title=target.title if target else "",
                client_width=target.client_rect.width if target else 0,
                client_height=target.client_rect.height if target else 0,
                actions_total=len(actions),
                actions_completed=completed,
                stdout=out,
                stderr=err,
                exit_code=exit_code,
                checkpoints=checkpoints,
                observations=observations,
                note=note,
                session_active=self.active,
                llm_restored=llm_restored,
            ))

        self._thread = threading.Thread(target=worker, daemon=True, name="persistent-test-actions")
        self._thread.start()

    def close_async(
        self,
        request_id: str,
        *,
        close_timeout_seconds: float,
        restore_delay_seconds: float,
        on_status: StatusCallback,
        on_done: DoneCallback,
        on_stage: StageCallback | None = None,
    ) -> None:
        with self._lock:
            if self._state not in {TestSessionState.ACTIVE_BACKGROUND, TestSessionState.ACTIVE_FOREGROUND, TestSessionState.LOST} or self.busy:
                raise RuntimeError("Aucune TestSession fermable n'est disponible.")
            self._state = TestSessionState.CLOSING
            self._cancel.clear()
            session_id = self._request.request_id if self._request else ""
            proc = self._proc
            target = self._target
            known = set(self._known_pids)

        def worker() -> None:
            started = time.monotonic()
            logs: list[str] = []
            llm_restored = False
            status = ExecutionStatus.SUCCESS
            note = "TestSession fermée."
            exit_code = None
            try:
                on_status("Fermeture de la TestSession…")
                if on_stage: on_stage("closing")
                if proc is not None:
                    known.update(self._process_tree_pids(int(proc.pid), known))
                if target is not None and self.desktop.window_exists(target.hwnd):
                    self.desktop.close_window(target.hwnd)
                end = time.monotonic() + max(0.2, close_timeout_seconds)
                while proc is not None and proc.poll() is None and time.monotonic() < end:
                    time.sleep(0.05)
                if proc is not None and proc.poll() is None:
                    self._terminate_known_processes(known, max(0.1, close_timeout_seconds))
                if proc is not None:
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        pass
                    exit_code = proc.poll()
                if restore_delay_seconds > 0:
                    time.sleep(restore_delay_seconds)
                if on_stage: on_stage("restoring")
                llm_restored = self.workspace.restore_llm_workspace()
                if not llm_restored:
                    status = ExecutionStatus.ERROR
                    note = "TestSession fermée, mais restauration du workspace LLM impossible."
            except Exception as exc:
                status = ExecutionStatus.ERROR
                note = str(exc)
                logs.append(f"ERROR: {exc}")
            out, err = self.full_output()
            with self._lock:
                self._proc = None
                self._target = None
                self._known_pids.clear()
                self._request = None
                self._cwd = None
                self._state = TestSessionState.CLOSED
                self._pending_screenshots.clear()
            on_done(TestSessionResult(
                request_id=request_id,
                session_id=session_id,
                operation="CLOSED",
                status=status,
                duration=time.monotonic() - started,
                target_title=target.title if target else "",
                client_width=target.client_rect.width if target else 0,
                client_height=target.client_rect.height if target else 0,
                stdout=out,
                stderr=err,
                exit_code=exit_code,
                note=note,
                session_active=False,
                llm_restored=llm_restored,
            ))

        self._thread = threading.Thread(target=worker, daemon=True, name="persistent-test-close")
        self._thread.start()


class _TestCancelledOrTimeout(RuntimeError):
    pass


def format_test_session_result(result: TestSessionResult, goal_reminder: str = "", max_output_chars: int = 100_000) -> str:
    from .protocol import truncate_output

    lines = [
        "#TestSessionResult",
        "Protocol: 1",
        f"ID: {result.request_id}",
        f"SessionID: {result.session_id or '<none>'}",
        f"Operation: {result.operation}",
        f"Status: {result.status.value}",
        f"SessionActive: {'YES' if result.session_active else 'NO'}",
        f"LLMWorkspaceRestored: {'YES' if result.llm_restored else 'NO'}",
        f"Duration: {result.duration:.2f}s",
        f"TargetWindow: {result.target_title or '<unknown>'}",
        f"ClientSize: {result.client_width}x{result.client_height}",
        f"Actions: {result.actions_completed}/{result.actions_total}",
        f"ExitCode: {result.exit_code if result.exit_code is not None else 'N/A'}",
    ]
    if result.note:
        lines += ["", "NOTE:", result.note]
    if result.checkpoints:
        lines += ["", "CHECKPOINTS:", *[f"- {name}" for name in result.checkpoints]]
    lines += [
        "",
        "STDOUT_DELTA:" if result.operation != "CLOSED" else "STDOUT:",
        truncate_output(result.stdout or "", max_output_chars) or "<empty>",
        "",
        "STDERR_DELTA:" if result.operation != "CLOSED" else "STDERR:",
        truncate_output(result.stderr or "", max_output_chars) or "<empty>",
    ]
    if result.observations:
        lines += [
            "",
            "#VisualObservation",
            "Une image jointe contient les captures de la TestSession. #Click reste exprimé dans les coordonnées client originales.",
        ]
    if goal_reminder.strip():
        lines += ["", "#GoalReminder", goal_reminder.strip()]
    return "\n".join(lines)


__all__ = [
    "PersistentTestSession",
    "TestSessionState",
    "TestSessionResult",
    "format_test_session_result",
    "compose_observation_sheet",
]
