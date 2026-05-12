"""Terminal user interface: curses TUI, messaging, and interactive prompts.

Provides a full-screen curses interface with arrow-key navigation menus,
color-coded status messages, and company branding. Falls back to a simple
numeric menu when curses is unavailable (e.g., piped output).

All curses state is encapsulated in the CursesUI class. Module-level
functions (init_ui, close_ui, choose_menu, msg_info, etc.) operate on
a singleton instance for convenience.
"""

import curses
import os
import sys
import time
from typing import Callable, List, Optional, Tuple

# Curses color pair IDs — AWS-inspired palette (blue/amber accents)
CP_HEADER = 1    # White on blue — top header bar
CP_COMPANY = 2   # Yellow — company name box
CP_SELECTED = 3  # Black on yellow — highlighted menu item
CP_INFO = 4      # Cyan — informational messages
CP_WARN = 5      # Yellow — warning messages
CP_ERROR = 6     # Red — error messages
CP_HINT = 7      # Blue — keyboard hint bar at bottom


class CursesUI:
    """Encapsulates all curses session state, rendering, and lifecycle.

    Manages TTY file descriptors, stdin/stdout redirection, color pairs,
    and provides methods for drawing the branded frame, interactive menus,
    status messages, and region input prompts.

    Usage:
        ui = CursesUI()
        ui.open()           # Initialize curses and redirect I/O
        ui.render_menu(...)  # Show interactive menu
        ui.close()          # Restore terminal and file descriptors
    """

    def __init__(self) -> None:
        self.stdscr = None
        self.tty_in = None
        self.tty_out = None
        self._saved_stdin_fd: Optional[int] = None
        self._saved_stdout_fd: Optional[int] = None
        self.status_line = ""
        self.company_name = "My Company"

    @property
    def is_active(self) -> bool:
        return self.stdscr is not None

    # -- Session lifecycle --

    def open(self) -> bool:
        """Initialize curses, redirect stdin/stdout to /dev/tty, set up colors.

        Saves the original file descriptors so they can be restored by close().
        Returns True on success, False if curses initialization fails.
        """
        if self.stdscr is not None:
            return True
        try:
            self.tty_in = open("/dev/tty", "r")
            self.tty_out = open("/dev/tty", "w")
            self._saved_stdin_fd = os.dup(0)
            self._saved_stdout_fd = os.dup(1)
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os.dup2(self.tty_in.fileno(), 0)
            os.dup2(self.tty_out.fileno(), 1)

            self.stdscr = curses.initscr()
            if curses.has_colors():
                curses.start_color()
                try:
                    curses.use_default_colors()
                except Exception:
                    pass
                curses.init_pair(CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)
                curses.init_pair(CP_COMPANY, curses.COLOR_YELLOW, -1)
                curses.init_pair(CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
                curses.init_pair(CP_INFO, curses.COLOR_CYAN, -1)
                curses.init_pair(CP_WARN, curses.COLOR_YELLOW, -1)
                curses.init_pair(CP_ERROR, curses.COLOR_RED, -1)
                curses.init_pair(CP_HINT, curses.COLOR_BLUE, -1)
            curses.noecho()
            curses.cbreak()
            self.stdscr.keypad(True)
            return True
        except Exception:
            self.close()
            return False

    def close(self) -> None:
        """Restore terminal state, original stdin/stdout, and release TTY handles."""
        if self.stdscr is not None:
            try:
                curses.nocbreak()
                self.stdscr.keypad(False)
                curses.echo()
                curses.endwin()
            except Exception:
                pass
            self.stdscr = None

        if self._saved_stdin_fd is not None:
            try:
                os.dup2(self._saved_stdin_fd, 0)
                os.close(self._saved_stdin_fd)
            except Exception:
                pass
            self._saved_stdin_fd = None

        if self._saved_stdout_fd is not None:
            try:
                os.dup2(self._saved_stdout_fd, 1)
                os.close(self._saved_stdout_fd)
            except Exception:
                pass
            self._saved_stdout_fd = None

        if self.tty_in is not None:
            try:
                self.tty_in.close()
            except Exception:
                pass
            self.tty_in = None

        if self.tty_out is not None:
            try:
                self.tty_out.close()
            except Exception:
                pass
            self.tty_out = None

    # -- Low-level helpers --

    def _color(self, pair_id: int, fallback: int = 0) -> int:
        try:
            if curses.has_colors():
                return curses.color_pair(pair_id)
        except Exception:
            pass
        return fallback

    def _safe_add(self, y: int, x: int, text: str, max_x: int, attr: int = 0) -> None:
        if y < 0:
            return
        truncated = text[: max(0, max_x - x - 1)]
        try:
            self.stdscr.addstr(y, x, truncated, attr)
        except curses.error:
            pass

    def _status_attr(self, level: str) -> int:
        upper = level.upper()
        if upper == "ERROR":
            return self._color(CP_ERROR, curses.A_BOLD)
        if upper == "WARN":
            return self._color(CP_WARN, curses.A_BOLD)
        if upper == "OK":
            return self._color(CP_INFO, curses.A_BOLD)
        return self._color(CP_INFO, curses.A_NORMAL)

    # -- Frame / status rendering --

    def draw_frame(self, title: str) -> int:
        """Draw the branded header frame and return the first usable content row.

        Layout:
          Row 0: Blue header bar with 'AWS Accounts Tools'
          Rows 1-3: Yellow company name box
          Row 4: Section title
          Row 6+: Available for menu items or content
        """
        stdscr = self.stdscr
        max_y, max_x = stdscr.getmaxyx()
        header_attr = self._color(CP_HEADER, curses.A_BOLD)
        company_attr = self._color(CP_COMPANY, curses.A_BOLD)

        if max_x > 2:
            self._safe_add(0, 0, " " * (max_x - 1), max_x, header_attr)
        self._safe_add(0, 2, "AWS Accounts Tools", max_x, header_attr | curses.A_BOLD)

        content = f" Company: {self.company_name} "
        box_inner = min(max(10, len(content)), max(10, max_x - 8))
        box_w = min(max_x - 2, box_inner + 2)
        left = max(0, (max_x - box_w) // 2)
        top = 1
        if max_y >= 5 and box_w >= 6:
            top_border = "+" + "-" * (box_w - 2) + "+"
            middle_text = content[: box_w - 2].ljust(box_w - 2)
            mid_line = f"|{middle_text}|"
            self._safe_add(top, left, top_border, max_x, company_attr)
            self._safe_add(top + 1, left, mid_line, max_x, company_attr)
            self._safe_add(top + 2, left, top_border, max_x, company_attr)
        content_row = 4

        title_attr = self._color(CP_HEADER, curses.A_BOLD) | curses.A_BOLD
        self._safe_add(content_row, 0, title, max_x, title_attr)
        return content_row + 2

    def flash_center(self, message: str, seconds: float = 1.0, level: str = "INFO") -> None:
        """Display a centered, highlighted message briefly (visual feedback)."""
        if self.stdscr is None:
            return
        try:
            self.stdscr.clear()
            max_y, max_x = self.stdscr.getmaxyx()
            row = max(0, max_y // 2)
            col = max(0, (max_x - len(message)) // 2)
            flash_attr = self._status_attr(level) | curses.A_BOLD | curses.A_REVERSE
            self._safe_add(row, col, message, max_x, flash_attr)
            self.stdscr.refresh()
            time.sleep(seconds)
        except Exception:
            pass

    def show_status(self, level: str, message: str) -> None:
        """Redraw the frame with a status message (used during async operations)."""
        self.status_line = f"{level.upper()} {message}"
        if self.stdscr is None:
            return
        try:
            stdscr = self.stdscr
            max_y, max_x = stdscr.getmaxyx()
            stdscr.clear()
            content_row = self.draw_frame("AWS Accounts Tools")
            try:
                self._safe_add(content_row, 0, self.status_line, max_x, self._status_attr(level))
                hint_attr = self._color(CP_HINT, curses.A_DIM) | curses.A_DIM
                self._safe_add(max(0, max_y - 1), 0, "Working...", max_x, hint_attr)
            except curses.error:
                pass
            stdscr.refresh()
        except Exception:
            pass

    # -- Interactive menus --

    def render_menu(self, title: str, options: List[str]) -> Optional[str]:
        """Render an interactive arrow-key menu and return the selected option.

        Navigation: UP/DOWN arrows, ENTER to select, ESC or Ctrl+C to cancel.
        Returns None if the user cancels.
        """
        stdscr = self.stdscr
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.nodelay(False)
        stdscr.clear()
        selected = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            row = self.draw_frame(title)

            for i, option in enumerate(options):
                if row >= max_y - 2:
                    break
                if i == selected:
                    sel_attr = self._color(CP_SELECTED, curses.A_REVERSE) | curses.A_BOLD
                    self._safe_add(row, 0, f"> {option}", max_x, sel_attr)
                else:
                    self._safe_add(row, 0, f"  {option}", max_x)
                row += 1

            if self.status_line:
                level = self.status_line.split(" ", 1)[0] if " " in self.status_line else "INFO"
                self._safe_add(max_y - 2, 0, self.status_line, max_x, self._status_attr(level))

            hint = "Use arrows to navigate, ENTER to select, ESC to cancel"
            hint_attr = self._color(CP_HINT, curses.A_DIM) | curses.A_DIM
            self._safe_add(max_y - 1, 0, hint, max_x, hint_attr)
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(options)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(options)
            elif key in (ord("\n"), ord("\r")):
                return options[selected]
            elif key == 27:  # ESC
                return None
            elif key == 3:  # Ctrl+C
                return None

    def prompt_region_input(
        self,
        default_region: str,
        validate_fn: Callable[[str], Tuple[bool, str]],
    ) -> Optional[str]:
        """Show a curses text input prompt for a custom AWS region.

        Validates input via validate_fn. Loops until valid input is provided
        or the user cancels. Blank input defaults to default_region.
        """
        stdscr = self.stdscr
        while True:
            max_y, max_x = stdscr.getmaxyx()
            stdscr.clear()
            row = self.draw_frame("Select AWS region for this session:")

            self._safe_add(row, 0, f"Default region: {default_region}", max_x)
            self._safe_add(row + 2, 0, "Custom region (press ENTER to validate):", max_x)
            self._safe_add(row + 3, 0, "Region: ", max_x, curses.A_BOLD)

            if self.status_line:
                level = self.status_line.split(" ", 1)[0] if " " in self.status_line else "INFO"
                self._safe_add(max_y - 2, 0, self.status_line, max_x, self._status_attr(level))
            hint_attr = self._color(CP_HINT, curses.A_DIM) | curses.A_DIM
            self._safe_add(
                max_y - 1, 0,
                "Type region, ENTER to confirm, blank uses default",
                max_x, hint_attr,
            )
            stdscr.refresh()

            try:
                curses.echo()
                col = len("Region: ")
                stdscr.move(row + 3, col)
                stdscr.clrtoeol()
                raw = stdscr.getstr(row + 3, col, max(1, max_x - col - 1))
                curses.noecho()
            except Exception:
                try:
                    curses.noecho()
                except Exception:
                    pass
                return None

            typed = raw.decode("utf-8", errors="ignore").strip() if raw is not None else ""
            candidate = typed or default_region
            ok, err = validate_fn(candidate)
            if ok:
                return candidate
            self.show_status("WARN", err)
            self.flash_center(err, 1.2, "ERROR")


# ---------------------------------------------------------------------------
# Module-level singleton + public API
# ---------------------------------------------------------------------------

# Single shared instance — all module-level functions delegate to this.
_ui = CursesUI()


def init_ui() -> bool:
    """Initialize the curses TUI session. Safe to call multiple times."""
    return _ui.open()


def close_ui() -> None:
    """Close the curses session and restore the terminal to normal mode."""
    _ui.close()


def is_ui_active() -> bool:
    return _ui.is_active


def set_company_name(name: str) -> None:
    name = (name or "").strip()
    _ui.company_name = name or "My Company"


def flash_center(message: str, seconds: float = 1.0, level: str = "INFO") -> None:
    _ui.flash_center(message, seconds, level)


def choose_menu(title: str, options: List[str]) -> Optional[str]:
    """Present an interactive menu and return the chosen option.

    Tries curses TUI first; falls back to a numbered text menu if curses
    is unavailable. Returns None on cancel or empty options.
    """
    if not options:
        return None
    if _ui.open() and _ui.is_active:
        try:
            return _ui.render_menu(title, options)
        except Exception:
            _ui.close()
    return _fallback_menu(title, options)


def prompt_region_custom(
    default_region: str,
    validate_fn: Callable[[str], Tuple[bool, str]],
) -> Optional[str]:
    """Prompt the user for a custom AWS region (curses or TTY fallback)."""
    if _ui.is_active and _ui.stdscr is not None:
        return _ui.prompt_region_input(default_region, validate_fn)
    while True:
        typed = prompt_required("awsRegion (session override)", default_region).strip()
        ok, err = validate_fn(typed)
        if ok:
            return typed
        msg_warn(err)


# ---------------------------------------------------------------------------
# Fallback numeric menu (used when curses is not available)
# ---------------------------------------------------------------------------

def _fallback_menu(title: str, options: List[str]) -> Optional[str]:
    """Simple numbered menu via /dev/tty for environments without curses."""
    tty_out = None
    tty_in = None
    try:
        tty_out = open("/dev/tty", "w", buffering=1)
        tty_in = open("/dev/tty", "r")
    except Exception:
        pass

    display = tty_out or sys.stdout
    input_src = tty_in or sys.stdin
    try:
        print("\n" + title, file=display)
        display.flush()
        for i, item in enumerate(options, start=1):
            print(f" {i}. {item}", file=display)
            display.flush()
        while True:
            try:
                if tty_in:
                    display.write("Choose (number, empty to cancel): ")
                    display.flush()
                    raw = input_src.readline().strip()
                else:
                    raw = input("Choose (number, empty to cancel): ").strip()
                if not raw:
                    return None
                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(options):
                        return options[idx - 1]
            except KeyboardInterrupt:
                print(file=display)
                display.flush()
                msg_warn("Cancelled by user.")
                return None
    finally:
        if tty_out:
            tty_out.close()
        if tty_in:
            tty_in.close()


# ---------------------------------------------------------------------------
# Messaging — routes to curses UI or ANSI-colored stderr automatically
# ---------------------------------------------------------------------------

def _styled_log(level: str, message: str) -> str:
    """Format a log message with ANSI color codes for terminal output."""
    if not sys.stderr.isatty():
        return f"{level} {message}"
    colors = {
        "INFO": "\033[36m",
        "WARN": "\033[33m",
        "ERROR": "\033[31m",
        "OK": "\033[32m",
    }
    reset = "\033[0m"
    color = colors.get(level, "")
    return f"{color}{level}{reset} {message}" if color else f"{level} {message}"


def _log_or_ui(level: str, message: str) -> None:
    if _ui.is_active:
        _ui.show_status(level, message)
    else:
        print(_styled_log(level, message), file=sys.stderr)


def msg_info(message: str) -> None:
    _log_or_ui("INFO", message)


def msg_warn(message: str) -> None:
    _log_or_ui("WARN", message)


def msg_error(message: str) -> None:
    _log_or_ui("ERROR", message)


def msg_success(message: str) -> None:
    _log_or_ui("OK", message)


# ---------------------------------------------------------------------------
# TTY prompt (non-curses)
# ---------------------------------------------------------------------------

def prompt_required(name: str, default: str) -> str:
    """Prompt the user for a required value via /dev/tty.

    Loops until a non-empty value is provided. If the user presses Enter
    with a non-empty default, the default is accepted. Raises KeyboardInterrupt
    on Ctrl+C.
    """
    tty_out = None
    tty_in = None
    try:
        tty_out = open("/dev/tty", "w", buffering=1)
        tty_in = open("/dev/tty", "r")
    except Exception:
        pass

    display = tty_out or sys.stdout
    input_src = tty_in or sys.stdin
    try:
        while True:
            try:
                if tty_in:
                    display.write(f"{name} [{default}]: ")
                    display.flush()
                    typed = input_src.readline().strip()
                else:
                    typed = input(f"{name} [{default}]: ").strip()
                if typed:
                    return typed
                if default:
                    return default
                print("This field is required.", file=display)
                display.flush()
            except KeyboardInterrupt:
                print(file=display)
                display.flush()
                msg_warn("Cancelled by user.")
                raise
    finally:
        if tty_out:
            tty_out.close()
        if tty_in:
            tty_in.close()
