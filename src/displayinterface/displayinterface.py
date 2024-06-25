from abc import ABC, abstractmethod
import json
import os
import socket
import sys
from typing import Any, NamedTuple, override

class DisplayInfo(NamedTuple):
    width: int
    height: int
    scale: float

class DisplayInterface(ABC):
    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int]:
        """Return the cursor position in physical coordinates"""
        pass

    @abstractmethod
    def get_screen_info(self) -> DisplayInfo:
        """Returns DisplayInfo object with width, height in physical coordinates, and a float scale factor"""
        pass


class CachedScreenInfoMixin:
    def update_stored_screen_info(self) -> None:
        raise NotImplementedError

class HyprlandInterface(DisplayInterface, CachedScreenInfoMixin):
    def __init__(self):
        self.hyprland_instance_signature: str = os.getenv("HYPRLAND_INSTANCE_SIGNATURE", "")
        self.socket_path: str = f"{os.getenv('XDG_RUNTIME_DIR')}/hypr/{self.hyprland_instance_signature}/.socket.sock"
        self.screen_info: DisplayInfo = self.get_screen_info()

    def __send_command(self, command: bytes, buffer_size: int) -> str:
        """Establishes a connection, sends a command, and receives the response."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self.socket_path)
            sock.sendall(command)
            data: bytes = sock.recv(1024)  # Adjust buffer size as necessary
            return data.decode()

    @override
    def update_stored_screen_info(self):
        """The hyprland interface stores screen info to scale the mouse coordinates faster.
        If for whatever reason you want to update the stored screen info in case something changed, call this method.
        """
        self.screen_info = self.get_screen_info()

    @override
    def get_cursor_position(self) -> tuple[int, int]:
        """Sends a cursor position request and returns the cursor position in physical coordinates."""
        position_string = self.__send_command(b"/cursorpos", buffer_size=512)
        position_coords = self.string_to_point(position_string)
        return self.convert_to_physical_coords(position_coords, self.screen_info)

    @override
    def get_screen_info(self) -> DisplayInfo:
        """Sends a request to list all monitors and parses the response, returning an instance DisplayInfo for the monitor with ID 0.
        The width and height fields correspond exactly to the configured "real" display resolution.
        The scale field is the configured scaling factor, where 1 is no scaling.
        """
        # Send equivalent to "hyprctl monitors -j" command (the -j tells hyprctl to output in JSON)
        monitors_json = self.__send_command(b"j/monitors", buffer_size=4096)
        # The parsed json will be a list[dict[str, any]], each entry is a monitor
        # No idea how to deal with multi monitor so lets just take the first one (by searching for ID 0)
        monitors_parsed: list[dict[str, Any]] = json.loads(monitors_json)
        monitors_id_zero: list[dict[str, Any]] = list(filter(lambda m: m.get("id") == 0, monitors_parsed))
        # We should now have a single-entry list containing the monitor with ID 0
        # If this list has more than one entry, something has gone incredibly wrong
        num_monitors: int = len(monitors_id_zero) 
        if num_monitors == 0:
            raise RuntimeError("Could not find any monitor with ID = 0.")
        elif num_monitors != 1:
            raise ValueError(f"Expected exactly one monitor with ID = 0, but found {num_monitors}.")

        monitor = monitors_id_zero[0]
        info = DisplayInfo(width=monitor['width'], height=monitor['height'], scale=monitor['scale'])
        return info

    @staticmethod
    def convert_to_physical_coords(global_coords: tuple[int, int], display_info: DisplayInfo) -> tuple[int, int]:
        """
        Converts global layout coordinates (see below) to physical coordinates

        NOTE: Global layout coordinates have scaling and/or transformations applied and do not necessarily 
        match the display resolution. See https://www.gfxstrand.net/faith/projects/wayland/transforms/ for more info.

        A 1920x1080 monitor that has a transform of 90 degrees applied will take up a 1080x1920 rectangle in global coordinates.
        A 3200x1800 monitor with a 2x scaling factor will take up a 1600x900 rectangle in global coordinates.
        """
        scale_factor = display_info.scale
        # Disgusting
        physical_coords: tuple[int, int] = int(round(global_coords[0] * scale_factor)), int(round(global_coords[1] * scale_factor))
        return physical_coords

    @staticmethod
    def string_to_point(pos_string: str) -> tuple[int, int]:
        coords = [ coord.strip() for coord in pos_string.split(',') ]
        (x, y) = (int(coords[0]), int(coords[1]))
        return (x, y)

    # def __enter__(self) -> 'HyprlandInterface':
    #     return self
    #
    # def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None) -> None:
    #     # No need to close sockets here since we are using context management in send_command
    #     pass

class PyAutoGuiInterface(DisplayInterface):
    """Interface used for Windows/macOS as well as X11 (non-Wayland!) Linux"""
    @override
    def get_screen_info(self) -> DisplayInfo:
        import pyautogui
        screen_size: pyautogui.Size = pyautogui.size()
        (width, height) = screen_size
        # Let's just hope PyAutoGUI has handled display scaling because I don't want to
        scale_factor: int = 1
        return DisplayInfo(width=width, height=height, scale=scale_factor)

    @override
    def get_cursor_position(self) -> tuple[int, int]:
        import pyautogui
        cursor_pos: pyautogui.Point = pyautogui.position()
        (x, y) = (int(round(cursor_pos[0])), int(round(cursor_pos[1])))
        return (x, y)


def get_display_interface() -> DisplayInterface:
    """Determines the current system environment and returns an interface for its display"""
    detected_platform = sys.platform
    if detected_platform == 'linux':
        xdg_session_type: str = os.getenv("XDG_SESSION_TYPE", "")
        if xdg_session_type == 'x11':
            return PyAutoGuiInterface()
        elif xdg_session_type == 'wayland':
            hyprland_instance_signature: str = os.getenv("HYPRLAND_INSTANCE_SIGNATURE", "")
            if hyprland_instance_signature != "":
                return HyprlandInterface()
            else:
                raise NotImplementedError("Non-hyprland wayland compositors not implemented!")
        else:
            raise NotImplementedError("This program does not recognise your Linux window manager/compositor")
    elif detected_platform == 'win32':
        return PyAutoGuiInterface()
    elif detected_platform == 'darwin': 
        # macOS = darwin
        return PyAutoGuiInterface()
    else:
        raise NotImplementedError(f"Your platform ({detected_platform}) is not supported by this program.")

def maybe_update_screen_info(display: DisplayInterface) -> None:
    """Check if the display object caches its current screen info (resolution and scale) and if so, updates them. Currently, this only affects Hyprland environments on Linux."""
    if isinstance(display, CachedScreenInfoMixin):
        display.update_stored_screen_info()
    # NOTE: Perhaps return a boolean from this to signify if it called the updater function or not? Maybe useful, maybe not idk.

# Test code. Don't run this.
# if __name__ == "__main__":
#     display = get_display_interface()
#     info = display.get_screen_info()
#     cursor = display.get_cursor_position()
#     maybe_update_screen_info(display)
#     print("Done!")
