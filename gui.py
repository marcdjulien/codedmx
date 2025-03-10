from inspect import getmembers, isfunction
import os
import dearpygui.dearpygui as dpg
import textwrap
import logging
import mido
import re
import json
import socket

import model
import util
import functions
import fixtures

logger = logging.getLogger(__name__)

APP = None

SCREEN_WIDTH = 1940
SCREEN_HEIGHT = 1150

AXIS_MARGIN = 0.025
WINDOW_INFO = {}

PROJECT_EXTENSION = "ndmx"
NODE_EXTENSION = "ndmxc"
HUMAN_DELAY = 0.125

CLIP_VIEW = 0
TRACK_VIEW = 1
GLOBAL_VIEW = 2

DEFAULT_SEQUENCE_DURATION = 4  # beats
VARIABLE_NAME_PATTERN = r"[a-zA-Z_][a-zA-Z\d_]*$"

ICON = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")


def set_app(app):
    global APP
    APP = app


def action_callback(sender, app_data, user_data):
    APP.action_callback(sender, app_data, user_data)


def valid(*objs):
    return all([obj is not None and not getattr(obj, "deleted", False) for obj in objs])


class WindowManager:
    """Manages the positions and size of windows."""

    # Clip window is always 41% x 45% of the top left region
    CLIP_WINDOW_PERCENT = [0.41, 0.45]

    # Clip param window starts off at 41% x 45% of the bottom left region
    CLIP_PARAM_WINDOW_PERCENT = [0.41, 0.45]

    def __init__(self, app):
        self.app = app
        self.state = app.state
        self.window_info = {}
        self.screen_width = SCREEN_WIDTH
        self.screen_height = SCREEN_HEIGHT
        self.update_window_size_info(self.screen_width, self.screen_height)

    def resize_all(self):
        self.resize_windows_callback(None, None, None)

    def reset_all(self):
        self.app.clip_preset_window.reset()
        self.app.multi_clip_preset_window.reset()
        self.app.clip_automation_presets_window.reset()
        self.app.code_window.reset()

    def update_window_size_info(self, new_width, new_height):
        self.screen_width = new_width
        self.screen_height = new_height
        height_margin = 18

        # Initial window settings for Edit mode.
        clip_window_pos = (0, 18)
        clip_window_size = (
            int(self.CLIP_WINDOW_PERCENT[0] * new_width),
            int(self.CLIP_WINDOW_PERCENT[1] * new_height),
        )

        code_window_pos = (clip_window_size[0], 18)
        code_window_size = (new_width - clip_window_size[0], clip_window_size[1])

        clip_parameters_pos = (0, 18 + clip_window_size[1])
        clip_parameters_size = (
            int(self.CLIP_PARAM_WINDOW_PERCENT[0] * new_width),
            new_height - clip_window_size[1] - height_margin,
        )

        console_window_pos = (clip_parameters_size[0], 18 + code_window_size[1])
        console_window_size = (
            new_width - clip_parameters_size[0],
            new_height - code_window_size[1] - height_margin,
        )

        self.window_info["edit"] = {
            "clip_pos": clip_window_pos,
            "clip_size": clip_window_size,
            "code_pos": code_window_pos,
            "code_size": code_window_size,
            "console_pos": console_window_pos,
            "console_size": console_window_size,
            "clip_parameters_pos": clip_parameters_pos,
            "clip_parameters_size": clip_parameters_size,
        }


        # Initial window settings for Performance mode.
        clip_window_pos = (0, 18)
        clip_window_size = (
            int(self.CLIP_WINDOW_PERCENT[0] * new_width),
            int(self.CLIP_WINDOW_PERCENT[1] * new_height),
        )

        clip_parameters_pos = (0, 18 + clip_window_size[1])
        clip_parameters_size = (
            int(self.CLIP_PARAM_WINDOW_PERCENT[0] * new_width),
            new_height - clip_window_size[1] - height_margin,
        )

        right_pane_width = new_width - clip_window_size[0]
        right_pane_bottom = int(new_height * 0.25)
        clip_preset_percent = 0.75

        clip_preset_pos = (clip_window_size[0], 18)
        clip_preset_size = (
            int(right_pane_width * clip_preset_percent),
            new_height - right_pane_bottom - height_margin,
        )

        global_clip_preset_pos = (clip_preset_pos[0] + clip_preset_size[0], 18)
        global_clip_preset_size = (
            right_pane_width - clip_preset_size[0],
            new_height - right_pane_bottom - height_margin,
        )

        clip_automation_pos = (
            clip_window_size[0],
            clip_preset_pos[1] + clip_preset_size[1],
        )
        clip_automation_size = (
            right_pane_width,
            right_pane_bottom,
        )

        self.window_info["performance"] = {
            "clip_pos": clip_window_pos,
            "clip_size": clip_window_size,
            "clip_parameters_pos": clip_parameters_pos,
            "clip_parameters_size": clip_parameters_size,
            "clip_preset_pos": clip_preset_pos,
            "clip_preset_size": clip_preset_size,
            "global_clip_preset_pos": global_clip_preset_pos,
            "global_clip_preset_size": global_clip_preset_size,
            "clip_automation_pos": clip_automation_pos,
            "clip_automation_size": clip_automation_size,
        }

    def resize_windows_callback(self, sender, app_data, user_data):
        if app_data is None:
            new_width = self.screen_width
            new_height = self.screen_height
        else:
            new_width, new_height = app_data[2:4]
        self.update_window_size_info(new_width, new_height)

        if self.state.mode == "edit":
            window_info = self.window_info["edit"]

            # Clip window
            dpg.set_item_pos("clip.gui.window", window_info["clip_pos"])
            dpg.set_item_width("clip.gui.window", window_info["clip_size"][0])
            dpg.set_item_height("clip.gui.window", window_info["clip_size"][1])
            dpg.configure_item("clip.gui.window", show=True)

            # Code windows
            window_tag = "code_editor.gui.window"
            dpg.set_item_pos(window_tag, window_info["code_pos"])
            dpg.set_item_width(window_tag, window_info["code_size"][0])
            dpg.set_item_height(window_tag, window_info["code_size"][1])
            dpg.set_item_width(window_tag + ".text", window_info["code_size"][0] * 0.98)
            dpg.set_item_height(
                window_tag + ".text", window_info["code_size"][1] * 0.91
            )
            dpg.configure_item(window_tag, show=True)

            # Resize automation window
            for input_channel in self.app.get_all_valid_clip_input_channels():
                tag = get_source_node_window_tag(input_channel)
                dpg.set_item_pos(tag, window_info["code_pos"])
                dpg.set_item_width(tag, window_info["code_size"][0])
                dpg.set_item_height(tag, window_info["code_size"][1])

            # Console winodws
            dpg.set_item_pos("console.gui.window", window_info["console_pos"])
            dpg.set_item_width("console.gui.window", window_info["console_size"][0])
            dpg.set_item_height("console.gui.window", window_info["console_size"][1])
            dpg.configure_item("console.gui.window", show=True)

            # Clip Parameters Windows
            dpg.set_item_pos(
                "clip_parameters.gui.window", window_info["clip_parameters_pos"]
            )
            dpg.set_item_width(
                "clip_parameters.gui.window", window_info["clip_parameters_size"][0]
            )
            dpg.set_item_height(
                "clip_parameters.gui.window", window_info["clip_parameters_size"][1]
            )
            dpg.configure_item("clip_parameters.gui.window", show=True)

        elif self.state.mode == "performance":
            window_info = self.window_info["performance"]

            # Hide edit windows
            windows = [
                APP.code_window,
                APP.console_window,
            ]
            for window in windows:
                window.hide()

            # Clip window
            dpg.set_item_pos("clip.gui.window", window_info["clip_pos"])
            dpg.set_item_width("clip.gui.window", window_info["clip_size"][0])
            dpg.set_item_height("clip.gui.window", window_info["clip_size"][1])
            dpg.configure_item("clip.gui.window", show=True)

            # Clip Preset Window
            dpg.set_item_pos("clip_preset.gui.window", window_info["clip_preset_pos"])
            dpg.set_item_width(
                "clip_preset.gui.window", window_info["clip_preset_size"][0]
            )
            dpg.set_item_height(
                "clip_preset.gui.window", window_info["clip_preset_size"][1]
            )
            dpg.configure_item("clip_preset.gui.window", show=True)

            # Global Clip Preset Window
            dpg.set_item_pos(
                "global_preset_window.gui.window", window_info["global_clip_preset_pos"]
            )
            dpg.set_item_width(
                "global_preset_window.gui.window",
                window_info["global_clip_preset_size"][0],
            )
            dpg.set_item_height(
                "global_preset_window.gui.window",
                window_info["global_clip_preset_size"][1],
            )
            dpg.configure_item("global_preset_window.gui.window", show=True)

            # Clip Parameters Windows
            dpg.set_item_pos(
                "clip_parameters.gui.window", window_info["clip_parameters_pos"]
            )
            dpg.set_item_width(
                "clip_parameters.gui.window", window_info["clip_parameters_size"][0]
            )
            dpg.set_item_height(
                "clip_parameters.gui.window", window_info["clip_parameters_size"][1]
            )
            dpg.configure_item("clip_parameters.gui.window", show=True)

            # Clip Automation Windows
            dpg.set_item_pos(
                "clip_automation.gui.window", window_info["clip_automation_pos"]
            )
            dpg.set_item_width(
                "clip_automation.gui.window", window_info["clip_automation_size"][0]
            )
            dpg.set_item_height(
                "clip_automation.gui.window", window_info["clip_automation_size"][1]
            )
            dpg.configure_item("clip_automation.gui.window", show=True)


class GuiAction:
    def __init__(self, params=None):
        global APP
        self.app = APP
        self.state = self.app.state
        self.params = params or {}

    def execute(self):
        raise NotImplementedError

    def __call__(self, sender, app_data, user_data):
        self.app.action(self)


class SelectTrack(GuiAction):
    def execute(self):
        # When user clicks on the track title, bring up the output configuration window.
        track = self.params["track"]
        if self.app._active_track == track:
            return

        self.app.save_last_active_clip()

        self.last_track = self.app._active_track
        self.last_clip = self.app._active_clip

        # Unset activate clip
        self.app._active_clip = None
        self.app._active_clip_slot = None
        for tag in self.app.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)

        self.app._active_track = track
        last_active_clip_id = self.app.gui_state["track_last_active_clip"].get(
            self.app._active_track.id
        )
        if last_active_clip_id is not None:
            self.app._active_clip = self.app.state.get_obj(last_active_clip_id)
            SelectClip(
                {"track": self.app._active_track, "clip": self.app._active_clip}
            ).execute()


class SelectEmptyClipSlot(GuiAction):
    def execute(self):
        new_track_i = self.params["track_i"]
        new_clip_i = self.params["clip_i"]
        self.old_clip_slot = self.app._active_clip_slot

        self.app._active_clip_slot = (new_track_i, new_clip_i)
        self.app._active_track = self.state.tracks[new_track_i]

    def undo(self):
        if self.old_clip_slot is None:
            return
        self.app._active_clip_slot = self.old_clip_slot
        self.app._active_track = self.state.tracks[self.old_clip_slot[0]]


class SelectClip(GuiAction):
    def execute(self):
        track = self.params["track"]
        clip = self.params["clip"]

        # Always reset code window.
        if self.app.state.mode == "edit":
            self.app.code_window.reset(show=True)

        if self.app._active_clip == clip:
            return

        self.app.save_last_active_clip()
        self.last_track = self.app._active_track
        self.last_clip = self.app._active_clip

        self.app._active_track = track
        self.app._active_clip = clip
        self.app._active_clip_slot = None

        for tag in self.app.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)


        self.app.clip_automation_presets_window.reset(clip)
        self.app.help_window.reset()
        self.app.clip_params_window.reset()

    def undo(self):
        self.app.save_last_active_clip()
        self.app._active_track = self.last_track
        self.app._active_clip = self.last_clip

        for tag in self.app.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)


class SelectInputChannel(GuiAction):
    def execute(self):
        clip = self.params["clip"]
        input_channel = self.params["channel"]
        for other_input_channel in clip.inputs:
            if other_input_channel.deleted:
                continue
            dpg.configure_item(
                get_source_node_window_tag(other_input_channel), show=False
            )
            # Set button color
            if other_input_channel.input_type == "color":
                dpg.bind_item_theme(
                    f"{other_input_channel.id}.name_button",
                    get_node_tag(other_input_channel) + ".theme",
                )
            else:
                dpg.bind_item_theme(
                    f"{other_input_channel.id}.name_button", "not_selected_preset.theme"
                )

        if self.app.state.mode == "edit":
            dpg.configure_item(get_source_node_window_tag(input_channel), show=True)
        elif self.app.state.mode == "performance":
            if input_channel.input_type == "color":
                dpg.configure_item(get_source_node_window_tag(input_channel), show=True)

        dpg.bind_item_theme(f"{input_channel.id}.name_button", "selected_preset.theme")
        APP.reset_automation_plot(input_channel)

        self.app._active_input_channel = input_channel

        if self.app.state.mode == "edit":
            self.app.code_window.hide()


class CreateNewClip(GuiAction):
    def execute(self):
        track_i = self.params["track_i"]
        clip_i = self.params["clip_i"]

        track = self.state.tracks[track_i]
        action = self.params.get("action")

        if action == "create":
            result = self.app.execute_wrapper(f"new_clip {track.id},{clip_i}")
            if not result.success:
                raise RuntimeError("Failed to create clip")

        clip = track.clips[clip_i]

        with dpg.value_registry():
            dpg.add_string_value(
                tag=get_code_window_tag(clip) + ".text", default_value=clip.code.read()
            )

        # TODO: Can probably simplify this by making Clipindow resettable
        # Gui Updates
        # Delete the double_click handler to create clips
        dpg.delete_item(
            get_clip_slot_group_tag(track_i, clip_i) + ".clip.item_handler_registry"
        )

        group_tag = get_clip_slot_group_tag(track_i, clip_i)
        for slot, child_tags in dpg.get_item_children(group_tag).items():
            for child_tag in child_tags:
                dpg.delete_item(child_tag)

        with dpg.group(parent=group_tag, horizontal=True, horizontal_spacing=5):
            dpg.add_button(
                arrow=True,
                direction=dpg.mvDir_Right,
                tag=f"{clip.id}.gui.play_button",
                callback=self.app.toggle_clip_play_callback,
                user_data=(track, clip),
            )

        clip_tag = group_tag + ".clip"
        with dpg.group(
            parent=group_tag, tag=clip_tag, horizontal=True, horizontal_spacing=5
        ):
            text_tag = f"{clip.id}.name"
            create_passive_button(
                clip_tag,
                text_tag,
                clip.name,
                SelectClip({"track": track, "clip": clip}),
                text_theme="clip_text_theme",
            )

        def copy_clip_callback(sender, app_data, user_data):
            self.app.copy_buffer = [user_data]

        for tag in [text_tag, text_tag + ".filler"]:
            with dpg.popup(tag, mousebutton=1):

                def show_properties_window(sender, app_data, user_data):
                    self.app._properties_buffer.clear()
                    dpg.configure_item(get_properties_window_tag(clip), show=True)

                dpg.add_menu_item(label="Properties", callback=show_properties_window)

                dpg.add_menu_item(
                    label="Copy", callback=copy_clip_callback, user_data=clip
                )
                dpg.add_menu_item(
                    label="Paste",
                    callback=action_callback,
                    user_data=PasteClip({"track_i": track_i, "clip_i": clip_i}),
                )
        # End Gui updates

        self.last_track = self.app._active_track
        self.last_clip = self.app._active_clip
        self.app.save_last_active_clip()
        self.app._active_track = track
        self.app._active_clip = clip

        # Create the properties window
        self.create_clip_properties_window(clip)

        # Add the associated code editor
        self.app.code_view = CLIP_VIEW
        self.app.code_window.reset()
        self.app.clip_params_window.reset()

    def create_clip_properties_window(self, clip):
        window_tag = get_properties_window_tag(clip)

        with dpg.window(
            tag=window_tag,
            label="Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH / 3, SCREEN_HEIGHT / 3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ):
            properties_table_tag = f"{window_tag}.properties_table"
            with dpg.table(
                header_row=True,
                tag=properties_table_tag,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                def update_clip_buffer_callback(sender, app_data, user_data):
                    property_name = user_data
                    self.app._properties_buffer["clip"][property_name] = app_data

                def save_clip_properties_callback(sender, app_data, user_data):
                    clip = user_data
                    for property_name, value in self.app._properties_buffer[
                        "clip"
                    ].items():
                        setattr(clip, property_name, value)
                    dpg.configure_item(window_tag, show=False)

                def cancel_properties_callback(sender, app_data, user_data):
                    clip = user_data
                    dpg.set_value(f"{clip.id}.name", clip.name)
                    dpg.configure_item(window_tag, show=False)

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(
                        source=f"{clip.id}.name",
                        callback=update_clip_buffer_callback,
                        user_data=("name"),
                    )

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Save",
                            callback=save_clip_properties_callback,
                            user_data=clip,
                        )
                        dpg.add_button(
                            label="Cancel",
                            callback=cancel_properties_callback,
                            user_data=clip,
                        )


class PasteClip(GuiAction):
    def execute(self):
        track_i = self.params["track_i"]
        clip_i = self.params["clip_i"]
        APP.paste_clip(track_i, clip_i)


class ShowTrackProperties(GuiAction):
    def execute(self):
        # Hide all track config windows
        for track in self.state.tracks:
            dpg.configure_item(
                get_output_configuration_window_tag(track),
                show=False,
            )

        track = self.params["track"]
        dpg.configure_item(
            get_output_configuration_window_tag(track),
            show=True,
        )
        dpg.focus_item(get_output_configuration_window_tag(track))


class ShowWindow(GuiAction):
    def __init__(self, window):
        if isinstance(window, str):
            super().__init__({"window_tag": window})
        else:
            super().__init__({"window_tag": window.tag})

    def execute(self):
        window_tag = self.params["window_tag"]
        dpg.configure_item(window_tag, show=False)
        dpg.configure_item(window_tag, show=True)
        dpg.focus_item(window_tag)


def get_clip_slot_group_tag(track_i, clip_i):
    return f"track[{track_i}].clip[{clip_i}].gui.table_group"


def get_node_editor_tag(clip):
    return f"{clip.id}.gui.node_window.node_editor"


def get_output_configuration_window_tag(track):
    return f"{track.id}.gui.output_configuration_window"


def get_source_node_window_tag(input_channel, is_id=False):
    return f"{input_channel if is_id else input_channel.id}.gui.source_node_window"


def get_properties_window_tag(obj):
    return f"{obj.id}.gui.properties_window"


def get_plot_tag(input_channel):
    return f"{input_channel.id}.plot"


def get_node_tag(obj):
    return f"{obj.id}.node"


def get_node_window_tag(clip):
    return f"{clip.id}.gui.node_window"


def get_code_window_tag(obj):
    return f"{obj.id}.gui.code_editor.code_window"


def get_node_attribute_tag(clip, channel):
    return f"{clip.id}.{channel.id}.node_attribute"


def get_output_node_value_tag(clip, output_channel):
    return f"{clip.id}.{output_channel.id}.output.value"


def get_preset_menu_bar_tag(preset):
    return f"{preset.id}.menu_bar"


def get_preset_sub_menu_tag(automation):
    return f"{automation.id}.preset_menu"


def get_sequences_group_tag(track):
    return f"{track.id}.sequences_group"


def get_sequence_button_tag(sequence):
    return f"{sequence.id}.button"


def get_preset_button_tag(preset):
    return f"{preset.id}.button"


def get_channel_preset_theme(preset):
    return f"{preset.id}.theme"


def get_automation_button_tag(automation):
    return f"{automation.id}.button"


def register_handler(add_item_handler_func, tag, function, user_data=None):
    handler_registry_tag = f"{tag}.item_handler_registry"
    if not dpg.does_item_exist(handler_registry_tag):
        dpg.add_item_handler_registry(tag=handler_registry_tag)
    add_item_handler_func(
        parent=handler_registry_tag, callback=function, user_data=user_data
    )
    dpg.bind_item_handler_registry(tag, handler_registry_tag)


def create_passive_button(
    group_tag,
    text_tag,
    text,
    single_click_callback=None,
    double_click_callback=None,
    user_data=None,
    double_click=False,
    text_theme=None,
):
    dpg.add_text(parent=group_tag, default_value=text, tag=text_tag)
    if text_theme:
        dpg.bind_item_theme(dpg.last_item(), text_theme)
    dpg.add_text(parent=group_tag, default_value=" " * 1000, tag=f"{text_tag}.filler")
    if single_click_callback is not None:
        register_handler(
            dpg.add_item_clicked_handler,
            group_tag,
            action_callback,
            single_click_callback,
        )
    if double_click_callback is not None:
        register_handler(
            dpg.add_item_double_clicked_handler,
            group_tag,
            action_callback,
            double_click_callback,
        )


class Window:
    def __init__(self, state, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.state = state
        self.window = None
        self.tag = kwargs.get("tag")
        self._create()

    def configure_window(self):
        pass

    def create(self):
        """Build the window."""
        raise NotImplementedError

    def on_close(self, sender, app_data, user_data):
        pass

    def _create(self):
        """Create and build the window."""
        self.configure_window()
        self.window = dpg.window(*self.args, **self.kwargs, on_close=self.on_close)
        self.create()

    @property
    def shown(self):
        return dpg.is_item_shown(self.tag)

    def show(self):
        dpg.configure_item(self.tag, show=True)

    def hide(self):
        dpg.configure_item(self.tag, show=False)

    def focus(self):
        dpg.focus_item(self.tag)

    def unfix_location(self):
        self.kwargs["no_move"] = False
        self.kwargs["no_collapse"] = False
        self.kwargs["no_close"] = False
        self.kwargs["no_resize"] = False

    def fix_location(self):
        self.kwargs["no_move"] = True
        self.kwargs["no_collapse"] = True
        self.kwargs["no_close"] = True
        self.kwargs["no_resize"] = True


class ResettableWindow(Window):
    """A Window that can be recreated.

    Helpful for windows that depend on state from other items that frequently
    change.
    """

    def __init__(self, state, *args, **kwargs):
        self.position = kwargs.get("pos", [1, 1])
        self.width = kwargs.get("width", 1)
        self.height = kwargs.get("height", 1)
        super().__init__(state, *args, **kwargs)

    def _create(self):
        """Create and build the window."""
        self.kwargs["pos"] = self.position
        self.kwargs["width"] = self.width
        self.kwargs["height"] = self.height
        self.kwargs["no_focus_on_appearing"] = True
        super()._create()

    def reset(self, show=None, focus=False):
        self.position = dpg.get_item_pos(self.tag)
        self.width = dpg.get_item_width(self.tag)
        self.height = dpg.get_item_height(self.tag)

        if show is None:
            try:
                show = self.shown
            except:
                logger.warning("Failed to get show status")
                show = None

        with APP.lock:
            dpg.delete_item(self.tag)
            self._create()

            if focus:
                self.focus()

            if show:
                self.show()
            else:
                self.hide()


    def reset_callback(self, sender, app_data, user_data):
        self.reset(show=True)


class TrackPropertiesWindow(ResettableWindow):
    def __init__(self, state, track):
        self.track = track
        super().__init__(
            state,
            tag=get_output_configuration_window_tag(track),
            label="Output Configuration",
            width=400,
            height=SCREEN_HEIGHT * 5 / 6,
            pos=(799, 60),
            show=False,
        )

    def create(self):
        with self.window:
            output_table_tag = f"{self.tag}.output_table"

            with dpg.group(horizontal=True):

                def set_track_title_button_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        self.track.name = app_data
                        dpg.set_value(user_data, self.track.name)

                track_title_tag = f"{self.track.id}.gui.button"
                dpg.add_input_text(
                    tag=f"{self.track.id}.name",
                    default_value=self.track.name,
                    user_data=track_title_tag,
                    callback=set_track_title_button_text,
                    width=75,
                )

                dpg.add_button(
                    label="Add Output",
                    callback=APP.create_track_output,
                    user_data=("create", self.track),
                )
                dpg.add_button(label="Add Fixture")
                with dpg.popup(dpg.last_item(), mousebutton=0):
                    for fixture in fixtures.FIXTURES:
                        dpg.add_menu_item(
                            label=fixture.name,
                            callback=APP.add_fixture,
                            user_data=(self.track, fixture),
                        )

                    def open_fixture_dialog():
                        dpg.configure_item("open_fixture_dialog", show=True)

                    dpg.add_menu_item(label="Custom", callback=open_fixture_dialog)

            with dpg.table(
                header_row=True,
                tag=output_table_tag,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(
                    label="DMX Ch.", tag=f"{output_table_tag}.column.dmx_address"
                )
                dpg.add_table_column(
                    label="Name", tag=f"{output_table_tag}.column.name"
                )
                dpg.add_table_column(tag=f"{output_table_tag}.column.delete", width=10)

        ###############
        ### Restore ###
        ###############

        for output_index, output_channel in enumerate(self.track.outputs):
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                APP.create_track_output_group(
                    sender=None,
                    app_data=None,
                    user_data=("restore", self.track, output_channel),
                )
            else:
                APP.create_track_output(
                    sender=None,
                    app_data=None,
                    user_data=("restore", self.track, output_channel),
                )

    def on_close(self):
        APP.clip_params_window.reset()


class RenameWindow(Window):
    def __init__(self, state):
        super().__init__(
            state,
            tag="rename_node.popup",
            label="Rename",
            no_background=False,
            modal=False,
            show=False,
            autosize=True,
            pos=(2 * SCREEN_WIDTH / 5, SCREEN_HEIGHT / 3),
            no_move=True,
            no_collapse=True,
            no_close=True,
        )

    def create(self):
        with self.window:

            def set_name_property(sender, app_data, user_data):
                if not re.match(VARIABLE_NAME_PATTERN, app_data):
                    return

                if APP._active_clip is not None and app_data:
                    node_editor_tag = get_node_editor_tag(APP._active_clip)
                    items = dpg.get_selected_nodes(node_editor_tag)
                    # Renaming a node
                    if items:
                        item = items[0]
                        alias = dpg.get_item_alias(item)
                        node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                        obj = self.state.get_obj(node_id)
                        obj.name = app_data
                        dpg.configure_item(
                            get_node_tag(APP._active_clip, obj), label=obj.name
                        )
                        dpg.set_value(f"{obj.id}.name", obj.name)
                    # Renaming a clip
                    else:
                        APP._active_clip.name = app_data
                        dpg.set_value(f"{APP._active_clip.id}.name", app_data)
                dpg.configure_item(self.tag, show=False)

            dpg.add_input_text(
                tag="rename_node.text",
                on_enter=True,
                callback=set_name_property,
                no_spaces=True,
            )


class InspectorWindow(Window):
    def __init__(self, state):
        self.x_values = list(range(500))
        super().__init__(
            state,
            label="Inspector",
            width=750,
            height=600,
            pos=(810, 0),
            show=False,
            tag="inspector.gui.window",
        )

    def create(self):
        with self.window:
            with dpg.plot(label="Inspector", height=-1, width=-1):
                dpg.add_plot_axis(dpg.mvXAxis, label="x")
                dpg.set_axis_limits(dpg.last_item(), 0, len(self.x_values))

                dpg.add_plot_axis(dpg.mvYAxis, label="y")
                dpg.add_line_series(
                    [],
                    [],
                    tag="inspector.series",
                    parent=dpg.last_item(),
                )

    def update(self):
        dpg.configure_item(
            "inspector.series",
            x=self.x_values,
            y=APP._active_input_channel.history[-1 - len(self.x_values) : -1],
        )


class GlobalStorageDebugWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="Global Storage Debug",
            tag="global_storage_debug.gui.window",
            show=False,
        )

    def create(self):
        with self.window:
            with dpg.table(
                header_row=True,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(label="Variable")
                dpg.add_table_column(label="Value")
                for i, (name, value) in enumerate(model.GlobalStorage.items()):
                    with dpg.table_row():
                        dpg.add_text(name)
                        dpg.add_text(value, tag=f"{self.tag}.{i}")

                dpg.set_value(
                    "n_global_storage_elements", len(model.GlobalStorage.items())
                )


class RemapMidiDeviceWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="Remap MIDI Device",
            tag="remap_midi_device.gui.window",
            show=False,
        )
        self.index_to_remap = None

    def set_index_to_remap(self, index):
        self.index_to_remap = index

    def create(self):
        def remap(sender, app_data, user_data):
            assert self.index_to_remap is not None
            new_device_name = user_data
            data = {"index": self.index_to_remap, "new_device_name": new_device_name}

            result = APP.execute_wrapper(f"remap_midi_device {json.dumps(data)}")

            if result.success:
                new_device = result.payload
                APP.gui_state["io_args"]["inputs"][
                    self.index_to_remap
                ] = new_device.device_name
                APP.io_window.create_io(
                    None, None, ("restore", self.index_to_remap, new_device, "inputs")
                )

                for channel in APP.get_all_valid_clip_input_channels():
                    if isinstance(channel, model.MidiInput):
                        device_parameter_id = channel.get_parameter_id("device")
                        id_parameter_id = channel.get_parameter_id("id")
                        dpg.set_value(
                            f"{device_parameter_id}.value",
                            channel.get_parameter("device").value,
                        )
                        dpg.set_value(
                            f"{id_parameter_id}.value",
                            channel.get_parameter("id").value,
                        )

                self.index_to_remap = None
                self.hide()

        with self.window:
            dpg.add_button(label="Refresh", callback=self.reset_callback)

            with dpg.table(
                header_row=True,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(label="Device Name")
                dpg.add_table_column(label="")
                for device_name in mido.get_input_names():
                    with dpg.table_row():
                        dpg.add_text(device_name)
                        dpg.add_button(
                            label="Remap", callback=remap, user_data=device_name
                        )


class IOWindow(Window):
    def __init__(self, state):
        super().__init__(
            state,
            label="I/O",
            width=750,
            height=300,
            pos=(1180, 0),
            tag="io.gui.window",
            show=False,
        )

    def create_io(self, sender, app_data, user_data):
        arg = app_data
        action = user_data[0]
        if action == "create":
            _, index, input_output = user_data
            io_type = APP.gui_state["io_types"][input_output][index]
            result = APP.execute_wrapper(
                f"create_io {index} {input_output} {io_type} {arg}"
            )
            if not result.success:
                raise RuntimeError("Failed to create IO")
            io = result.payload
            APP.gui_state["io_args"][input_output][index] = arg
        else:  # restore
            _, index, io, input_output = user_data

        table_tag = f"io.{input_output}.table"
        dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
        dpg.set_value(f"{table_tag}.{index}.arg", value=io.args)

    def create(self):
        try:
            ip_address = socket.gethostbyname(socket.gethostname())
        except:
            ip_address = "Unknown"

        with self.window:
            output_table_tag = "io.outputs.table"
            input_table_tag = "io.inputs.table"

            def set_io_type(sender, app_data, user_data):
                index, io_type, input_output, *args = user_data
                table_tag = f"io.{input_output}.table"
                APP.gui_state["io_types"][input_output][index] = io_type.type
                dpg.configure_item(
                    f"{table_tag}.{index}.type", label=io_type.nice_title
                )
                if not dpg.get_value(f"{table_tag}.{index}.arg"):
                    dpg.set_value(
                        f"{table_tag}.{index}.arg",
                        value=io_type.arg_template if not args else args[0],
                    )

                if args:
                    self.create_io(None, args[0], ("create", index, input_output))

            def connect(sender, app_data, user_data):
                _, index, input_output = user_data

                result = APP.execute_wrapper(f"connect_io {index} {input_output}")
                if not result.success:
                    raise RuntimeError("Failed to create IO")

                io = result.payload
                table_tag = f"io.{input_output}.table"
                dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io.args)

            dpg.add_text(f"IP Address: {ip_address}")

            with dpg.table(
                header_row=True,
                tag=input_table_tag,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                type_column_tag = f"{input_table_tag}.column.type"
                arg_column_tag = f"{input_table_tag}.column.arg"
                connect_column_tag = f"{input_table_tag}.column.connect"
                connected_column_tag = f"{input_table_tag}.column.connected"
                dpg.add_table_column(label="Input Type", tag=type_column_tag)
                dpg.add_table_column(label="Input", tag=arg_column_tag)
                dpg.add_table_column(label="Connect", tag=connect_column_tag)
                dpg.add_table_column(label=" ", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        input_type = self.state.io_inputs[i]
                        type_tag = f"{input_table_tag}.{i}.type"
                        dpg.add_button(
                            label="Select Input Type"
                            if input_type is None
                            else input_type.nice_title,
                            tag=type_tag,
                        )
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_input_type in model.ALL_INPUT_TYPES:
                                if io_input_type.type == "midi_input":
                                    with dpg.menu(label="MIDI"):
                                        for device_name in mido.get_input_names():
                                            dpg.add_menu_item(
                                                label=device_name,
                                                callback=set_io_type,
                                                user_data=(
                                                    i,
                                                    io_input_type,
                                                    "inputs",
                                                    device_name,
                                                ),
                                            )
                                else:
                                    dpg.add_menu_item(
                                        label=io_input_type.nice_title,
                                        callback=set_io_type,
                                        user_data=(i, io_input_type, "inputs"),
                                    )

                        arg_tag = f"{input_table_tag}.{i}.arg"
                        dpg.add_input_text(
                            default_value="",
                            tag=arg_tag,
                            on_enter=True,
                            callback=self.create_io,
                            user_data=("create", i, "inputs"),
                        )

                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label="Connect",
                                callback=connect,
                                user_data=("create", i, "inputs"),
                            )

                            def remap(sender, app_data, user_data):
                                APP.remap_midi_device_window.set_index_to_remap(
                                    user_data
                                )
                                APP.action(ShowWindow(APP.remap_midi_device_window))

                            dpg.add_button(
                                label="Remap",
                                callback=remap,
                                user_data=i,
                            )

                        dpg.add_table_cell()

            with dpg.table(
                header_row=True,
                tag=output_table_tag,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                type_column_tag = f"{output_table_tag}.column.type"
                arg_column_tag = f"{output_table_tag}.column.arg"
                connect_column_tag = f"{output_table_tag}.column.connect"
                connected_column_tag = f"{output_table_tag}.column.connected"
                dpg.add_table_column(label="Output Type", tag=type_column_tag)
                dpg.add_table_column(label="Output", tag=arg_column_tag)
                dpg.add_table_column(label="Connect", tag=connect_column_tag)
                dpg.add_table_column(label=" ", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        type_tag = f"{output_table_tag}.{i}.type"
                        dpg.add_button(
                            label="Select Output Type"
                            if self.state.io_outputs[i] is None
                            else self.state.io_outputs[i].nice_title,
                            tag=type_tag,
                        )
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_output_type in model.ALL_OUTPUT_TYPES:
                                if io_output_type.type == "midi_output":
                                    with dpg.menu(label="MIDI"):
                                        for device_name in mido.get_output_names():
                                            dpg.add_menu_item(
                                                label=device_name,
                                                callback=set_io_type,
                                                user_data=(
                                                    i,
                                                    io_output_type,
                                                    "outputs",
                                                    device_name,
                                                ),
                                            )
                                else:
                                    dpg.add_menu_item(
                                        label=io_output_type.nice_title,
                                        callback=set_io_type,
                                        user_data=(i, io_output_type, "outputs"),
                                    )

                        arg_tag = f"{output_table_tag}.{i}.arg"
                        dpg.add_input_text(
                            default_value="",
                            tag=arg_tag,
                            on_enter=True,
                            callback=self.create_io,
                            user_data=("create", i, "outputs"),
                        )

                        dpg.add_button(
                            label="Connect",
                            callback=connect,
                            user_data=("create", i, "outputs"),
                        )

                        dpg.add_table_cell()


class GlobalStorageDebugWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="Global Storage Debug",
            tag="global_storage_debug.gui.window",
            show=False,
        )

    def create(self):
        with self.window:
            with dpg.table(
                header_row=True,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(label="Variable")
                dpg.add_table_column(label="Value")
                for i, (name, value) in enumerate(model.GlobalStorage.items()):
                    with dpg.table_row():
                        dpg.add_text(name)
                        dpg.add_text(value, tag=f"{self.tag}.{i}")

                dpg.set_value(
                    "n_global_storage_elements", len(model.GlobalStorage.items())
                )


class HelpWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="Help",
            tag="code_help.gui.window",
            show=False,
        )

        with dpg.theme(tag="header.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[255, 255, 0, 255],
                    category=dpg.mvThemeCat_Core,
                )
        with dpg.theme(tag="header2.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[0, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )

    def create(self):
        def add_header(text):
            dpg.add_text(default_value=text)
            dpg.bind_item_theme(dpg.last_item(), "header.theme")

        def add_header2(text):
            dpg.add_text(default_value=text)
            dpg.bind_item_theme(dpg.last_item(), "header2.theme")

        def add_text(text):
            dpg.add_text(default_value=text)

        with self.window:
            if APP._active_clip is not None:
                if APP._active_clip.inputs:
                    add_header("[Inputs]")
                    add_text("Available channels:")

                    text = ""
                    for input_channel in APP._active_clip.inputs:
                        text += f"   {input_channel.name}\n"

                    text += textwrap.dedent(
                        """
                    The following functions are available for all Input Channels:
                    - Input.get()  ->  Returns the current value
                    - Input.value  ->  Returns the current value
                    """
                    )
                    add_text(text)

                if APP._active_clip.outputs:
                    add_header("[Outputs]")
                    add_text("Available channels:")

                    text = ""
                    for output_channel in APP._active_clip.outputs:
                        for channel_name in output_channel.channel_names:
                            text += f"   {output_channel.name}.{channel_name} / {output_channel.name}['{channel_name}']\n"
                        text += "\n"

                    text += textwrap.dedent(
                        """
                    The following functions are available for all Input Channels:
                    - Output.set(value)                ->  Sets the current value
                    - Output.channel.value = value     ->  Sets the current value
                    - Output['channel'].value = value  ->  Sets the current value
                    """
                    )
                    add_text(text)

            add_header("[Functions]")
            for name, function in getmembers(functions, isfunction):
                lines = function.__doc__.split("\n")
                add_header2(lines[0])
                add_text("\n".join(lines[1::]))


class ClipPresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=-1,
            height=800,
            label="< Clip Presets",
            tag="clip_preset.gui.window",
            show=False,
        )

        with dpg.theme(tag="clip_preset.gui.window.table_header_theme"):
            with dpg.theme_component(dpg.mvAll):
                for target in [
                    dpg.mvThemeCol_ButtonHovered,
                    dpg.mvThemeCol_Button,
                    dpg.mvThemeCol_ButtonActive,
                ]:
                    dpg.add_theme_color(
                        target=target,
                        value=[22, 42, 62, 105],
                        category=dpg.mvThemeCat_Core,
                    )
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[200, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )

    def configure_window(self):
        if self.state.mode == "edit":
            self.unfix_location()
        else:
            self.fix_location()

    def create(self):
        def play_clip_and_preset(sender, app_data, user_data):
            track, clip, preset = user_data
            APP._active_presets[track.id] = preset
            APP.play_clip_callback(None, None, (track, clip))
            APP.play_clip_preset_callback(None, None, preset)
            SelectClip({"track": track, "clip": clip}).execute()

        with self.window:
            with dpg.table(
                tag=f"{self.tag}.table",
                policy=dpg.mvTable_SizingFixedSame,
                scrollX=True,
                scrollY=False,
                hideable=True,
            ):
                has_presets = {}
                for i, track in enumerate(self.state.tracks):
                    any_presets = False
                    for clip in track.clips:
                        if not valid(clip):
                            continue
                        any_presets = any_presets or clip.presets

                    if any_presets:
                        dpg.add_table_column(label=track.name)

                    has_presets[track.id] = any_presets

                len(self.state.tracks[0].clips)
                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):
                        if not has_presets[track.id]:
                            continue
                        with dpg.table_cell():
                            for clip_i, clip in enumerate(track.clips):
                                if clip is None:
                                    continue
                                if not clip.presets:
                                    continue
                                clip = track.clips[clip_i]
                                with dpg.group(
                                    tag=f"{clip.id}.clip_preset_window.group",
                                ):
                                    dpg.add_button(label=clip.name, width=150)
                                    dpg.bind_item_theme(
                                        dpg.last_item(),
                                        "clip_preset.gui.window.table_header_theme",
                                    )

                                    for preset in clip.presets:
                                        dpg.add_button(
                                            tag=get_preset_button_tag(preset),
                                            label=preset.name,
                                            callback=play_clip_and_preset,
                                            user_data=(track, clip, preset),
                                            width=-1,
                                        )
                                        dpg.bind_item_theme(
                                            dpg.last_item(),
                                            get_channel_preset_theme(preset),
                                        )


class MultiClipPresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="< Multi Clip Presets",
            tag="global_preset_window.gui.window",
            show=False,
        )

    def configure_window(self):
        if self.state.mode == "edit":
            self.unfix_location()
        else:
            self.fix_location()

    def create(self):
        def play_multi_clip_preset_preset(sender, app_data, user_data):
            multi_clip_preset = user_data
            multi_clip_preset.execute()

            for clip_preset in multi_clip_preset.presets:
                track, clip, preset = clip_preset
                APP._active_presets[track.id] = preset

        with self.window:
            with dpg.menu_bar():
                dpg.add_menu_item(
                    label="New Preset",
                    callback=action_callback,
                    user_data=ShowWindow(APP.save_new_multi_clip_preset_window),
                )

            with dpg.table(tag="multi_clip_preset.table"):
                dpg.add_table_column(label="Preset")
                for multi_clip_preset in self.state.multi_clip_presets:
                    with dpg.table_row():
                        dpg.add_button(
                            label=multi_clip_preset.name,
                            callback=play_multi_clip_preset_preset,
                            user_data=multi_clip_preset,
                        )


class SaveNewMultiClipPresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=500,
            height=500,
            label="Save New Global Clip Preset",
            tag="save_new_global_preset_window.gui.window",
            show=False,
            modal=True,
        )
        self._multi_clip_presets_buffer = {}

    def create(self):
        def cancel():
            dpg.delete_item(self.tag)

        def save():
            multi_clip_presets = []
            for i, track in enumerate(self.state.tracks):
                include = dpg.get_value(f"global_preset.{i}")
                if include:
                    track, clip, preset = self._multi_clip_presets_buffer[i]
                    multi_clip_presets.append(":".join([track.id, clip.id, preset.id]))

            if multi_clip_presets:
                name = dpg.get_value("global_preset.name")
                result = APP.execute_wrapper(
                    f"add_multi_clip_preset {','.join(multi_clip_presets)} {name}"
                )
                if result.success:
                    self._multi_clip_presets_buffer.clear()
                    self.reset()
                    self.hide()
                    APP.multi_clip_preset_window.reset()
                else:
                    logger.warning("Failed to add clip preset")

        with self.window:
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Global Preset Name")
                    dpg.add_input_text(tag="global_preset.name")

                with dpg.table_row():
                    dpg.add_text(default_value="Track")
                    dpg.add_text(default_value="Clip Preset")
                    dpg.add_text(default_value="Include")

                for i, track in enumerate(self.state.tracks):
                    if all(clip is None for clip in track.clips):
                        continue

                    with dpg.table_row():
                        dpg.add_text(default_value=track.name)

                        def preset_selected(sender, app_data, user_data):
                            i, title, track, clip, preset = user_data
                            self._multi_clip_presets_buffer[int(i)] = (
                                track,
                                clip,
                                preset,
                            )
                            dpg.configure_item(
                                item=f"{self.tag}.menu_bar.{i}.title",
                                label=title,
                            )

                        with dpg.menu(
                            tag=f"{self.tag}.menu_bar.{i}.title",
                            label="Select Clip Preset",
                        ):
                            for clip in track.clips:
                                if not valid(clip):
                                    continue
                                for preset in clip.presets:
                                    title = f"{clip.name}: {preset.name}"
                                    dpg.add_menu_item(
                                        label=title,
                                        callback=preset_selected,
                                        user_data=(i, title, track, clip, preset),
                                    )

                        dpg.add_checkbox(tag=f"global_preset.{i}")

                with dpg.table_row():
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)


class ManageTriggerWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label="Triggers",
            tag="manage_trigger.gui.window",
            show=False,
        )

    def create(self):
        with self.window:
            dpg.add_button(
                label="Add Trigger",
                callback=action_callback,
                user_data=ShowWindow(APP.add_new_trigger_window),
            )

            with dpg.table(tag="triggers.table"):
                dpg.add_table_column(label="Name")
                dpg.add_table_column(label="Event")
                dpg.add_table_column(label="Command")
                dpg.add_table_column(label="Edit")
                dpg.add_table_column(label="Delete")

                for trigger in self.state.trigger_manager.triggers:
                    if not valid(trigger):
                        continue

                    with dpg.table_row():
                        # Name
                        dpg.add_text(default_value=trigger.name)

                        # Event
                        dpg.add_text(default_value=trigger.event)

                        # Command
                        dpg.add_text(default_value=trigger.command)

                        # Edit
                        dpg.add_button(label="Edit", callback=lambda: None)

                        # Delete
                        dpg.add_button(label="X", callback=lambda: None)


class AddNewTriggerWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(300, 200),
            width=600,
            height=200,
            label="New Trigger",
            tag="new_trigger.gui.window",
            show=False,
        )

    def create(self):
        def save():
            name = dpg.get_value("new_trigger.name")
            type_ = dpg.get_value("new_trigger.type")
            event = dpg.get_value("new_trigger.event")
            command = dpg.get_value("new_trigger.command")

            # TODO: Validate events and commands
            assert name
            assert type_
            assert event
            assert command

            data = {
                "name": name,
                "type": type_,
                "event": event,
                "command": command,
            }

            result = APP.execute_wrapper(f"add_trigger {json.dumps(data)}")
            if result.success:
                self.hide()
                dpg.set_value("new_trigger.name", "")
                dpg.set_value("new_trigger.type", "MIDI")
                dpg.set_value("new_trigger.event", "<device_name>, <channel>/<note>")
                dpg.set_value("new_trigger.command", "")
                APP.manage_trigger_window.reset()

        def cancel():
            self.hide()

        with self.window:

            def type_changed(sender, app_data, user_data):
                if app_data == "MIDI":
                    dpg.set_value(
                        item="new_trigger.event",
                        value="<device_name>, <channel>/<note>",
                    )
                elif app_data == "OSC":
                    dpg.set_value(item="new_trigger.event", value="<endpoint>")
                elif app_data == "Key":
                    dpg.set_value(item="new_trigger.event", value="<letter>")

                dpg.configure_item("new_trigger.midi_learn", enabled=app_data == "MIDI")

            with dpg.table(
                tag="new_trigger.table",
                header_row=False,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(tag="new_trigger.name", width=400)

                with dpg.table_row():
                    dpg.add_text(default_value="Type")
                    dpg.add_radio_button(
                        tag="new_trigger.type",
                        items=["MIDI", "OSC", "Key"],
                        horizontal=True,
                        callback=type_changed,
                        default_value="MIDI",
                    )

                with dpg.table_row():
                    dpg.add_text(default_value="Event")
                    dpg.add_input_text(
                        tag="new_trigger.event",
                        default_value="<device_name>, <channel>/<note>",
                        width=400,
                    )
                    dpg.add_button(
                        tag="new_trigger.midi_learn",
                        label="MIDI Learn",
                        callback=self.create_and_show_learn_midi_map_window_callback,
                    )

                with dpg.table_row():
                    dpg.add_text(default_value="Command")
                    dpg.add_input_text(tag="new_trigger.command", width=400)
                    dpg.add_button(
                        tag="new_trigger.command_learn",
                        label="Command Learn",
                        callback=self.enter_command_listen_mode,
                    )

                with dpg.table_row(tag="new_trigger.table.save_cancel_row"):
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)

    def enter_command_listen_mode(self, sender, app_data, user_data):
        APP.command_listening_mode = True
        with dpg.theme() as button_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button, (255, 0, 0, 40), category=dpg.mvThemeCat_Core
                )

        dpg.bind_item_theme("new_trigger.command_learn", button_theme)
        dpg.configure_item(item="new_trigger.command_learn", label="Listening...")

    def save_command(self, command):
        APP.command_listening_mode = False
        with dpg.theme() as button_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (100, 100, 100, 255),
                    category=dpg.mvThemeCat_Core,
                )

        dpg.bind_item_theme("new_trigger.command_learn", button_theme)
        dpg.configure_item(item="new_trigger.command_learn", label="Command Learn")

        dpg.set_value(item="new_trigger.command", value=command)

    def create_and_show_learn_midi_map_window_callback(
        self, sender, app_data, user_data
    ):
        try:
            dpg.delete_item("new_trigger.midi_map_window")
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item("new_trigger.midi_map_window")

        def save(sender, app_data, user_data):
            if model.LAST_MIDI_MESSAGE is not None:
                device_name, message = model.LAST_MIDI_MESSAGE
                note_control, value = model.midi_value(message)
                dpg.set_value(
                    "new_trigger.event",
                    f"{device_name}, {message.channel}/{note_control}",
                )
                dpg.delete_item("new_trigger.midi_map_window")

        with dpg.window(
            tag="new_trigger.midi_map_window",
            modal=True,
            width=300,
            height=300,
            no_move=True,
        ):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=save)
                dpg.add_button(label="Cancel", callback=cancel)


class ClipAutomationPresetWindow(ResettableWindow):
    def __init__(self, state):
        self.clip = None
        super().__init__(
            state,
            label="< Input Presets",
            tag="clip_automation.gui.window",
            show=False,
            horizontal_scrollbar=True,
            width=-1,
            height=-1,
        )

    def configure_window(self):
        if self.state.mode == "edit":
            self.unfix_location()
        else:
            self.fix_location()

    def create(self):
        if self.clip is None:
            with self.window:
                pass
            return

        def activate_automation(sender, app_data, user_data):
            clip, channel, automation = user_data
            APP.select_automation_callback(None, None, (channel, automation))

            for channel in clip.inputs:
                if not isinstance(channel, model.AutomatableSourceNode):
                    continue
                for automation in channel.automations:
                    dpg.bind_item_theme(
                        get_automation_button_tag(automation),
                        "selected_preset.theme"
                        if channel.active_automation == automation
                        else "not_selected_preset.theme",
                    )
            if APP._active_track.id in APP._active_presets:
                del APP._active_presets[APP._active_track.id]

        with self.window:
            with dpg.table(
                tag="all_automation.table",
                policy=dpg.mvTable_SizingFixedFit,
                scrollX=True,
            ):
                for channel in self.clip.inputs:
                    if not valid(channel):
                        continue
                    if not isinstance(channel, model.AutomatableSourceNode):
                        continue
                    dpg.add_table_column(label=channel.name)

                with dpg.table_row():
                    for channel in self.clip.inputs:
                        if not valid(channel):
                            continue
                        if not isinstance(channel, model.AutomatableSourceNode):
                            continue
                        with dpg.table_cell():
                            with dpg.group(horizontal=True):
                                dpg.add_button(
                                    label="1",
                                    callback=APP.default_time_callback,
                                    user_data=channel,
                                )
                                dpg.add_button(
                                    label="x2",
                                    callback=APP.double_time_callback,
                                    user_data=channel,
                                )
                                dpg.add_button(
                                    label="/2",
                                    callback=APP.half_time_callback,
                                    user_data=channel,
                                )
                            for automation in channel.automations:
                                dpg.add_button(
                                    tag=get_automation_button_tag(automation),
                                    label=automation.name,
                                    callback=activate_automation,
                                    user_data=(self.clip, channel, automation),
                                )
                                dpg.bind_item_theme(
                                    dpg.last_item(),
                                    "selected_preset.theme"
                                    if channel.active_automation == automation
                                    else "not_selected_preset.theme",
                                )

    def reset(self, clip=None, show=None):
        if clip:
            self.clip = clip
        super().reset(show=show)


class ClipWindow(Window):
    def __init__(self, state):
        super().__init__(
            state,
            tag="clip.gui.window",
            label="Clip",
            pos=(0, 18),
            width=800,
            height=520,
            no_resize=True,
            no_title_bar=True,
        )

    def configure_window(self):
        self.fix_location()

    def create(self):
        with self.window:
            table_tag = "clip_window.table"
            with dpg.table(
                header_row=False,
                tag=table_tag,
                borders_innerH=True,
                borders_outerH=True,
                borders_innerV=True,
                borders_outerV=True,
                policy=dpg.mvTable_SizingStretchProp,
                resizable=True,
            ):
                for _ in self.state.tracks:
                    dpg.add_table_column()

                # Track Header Row
                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):
                        with dpg.table_cell():
                            with dpg.group(horizontal=True) as group_tag:
                                text_tag = f"{track.id}.gui.button"
                                create_passive_button(
                                    group_tag,
                                    text_tag,
                                    track.name,
                                    single_click_callback=SelectTrack({"track": track}),
                                )

                                # Menu for track
                                for tag in [text_tag, text_tag + ".filler"]:
                                    with dpg.popup(tag, mousebutton=1):
                                        dpg.add_menu_item(
                                            label="Properties",
                                            # dpg requires the callback to be a function, not an object.
                                            callback=action_callback,
                                            user_data=ShowTrackProperties(
                                                {"track": track}
                                            ),
                                        )

                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    with dpg.table_row(height=10):
                        for track_i, track in enumerate(self.state.tracks):
                            clip = track.clips[clip_i]
                            with dpg.table_cell():
                                group_tag = get_clip_slot_group_tag(track_i, clip_i)
                                with dpg.group(tag=group_tag, horizontal=True):
                                    with dpg.group(
                                        tag=group_tag + ".clip",
                                        horizontal=True,
                                        horizontal_spacing=5,
                                    ):
                                        # Always add elements for an empty clip, if the clip is not empty, then we will update it after.
                                        text_tag = f"{track.id}.{clip_i}.gui.text"
                                        create_passive_button(
                                            group_tag + ".clip",
                                            text_tag,
                                            "",
                                            single_click_callback=SelectEmptyClipSlot(
                                                {"track_i": track_i, "clip_i": clip_i}
                                            ),
                                            double_click_callback=CreateNewClip(
                                                {
                                                    "track_i": track_i,
                                                    "clip_i": clip_i,
                                                    "action": "create",
                                                }
                                            ),
                                        )
                                        # Menu for empty clip
                                        with dpg.popup(
                                            text_tag + ".filler", mousebutton=1
                                        ):
                                            dpg.add_menu_item(
                                                label="New Clip",
                                                callback=CreateNewClip(
                                                    {
                                                        "track_i": track_i,
                                                        "clip_i": clip_i,
                                                        "action": "create",
                                                    }
                                                ),
                                            )
                                            dpg.add_menu_item(
                                                label="Paste",
                                                callback=action_callback,
                                                user_data=PasteClip(
                                                    {
                                                        "track_i": track_i,
                                                        "clip_i": clip_i,
                                                    }
                                                ),
                                            )
                            # Restore
                            if clip is not None:
                                APP.action(
                                    CreateNewClip(
                                        {"track_i": track_i, "clip_i": clip_i}
                                    )
                                )


class ConsoleWindow(Window):
    def __init__(self, state):
        self.current_log = state.log

        with dpg.theme(tag="clear_button.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (255, 0, 0, 50),
                    category=dpg.mvThemeCat_Core,
                )

        super().__init__(
            state,
            tag="console.gui.window",
            label="Console",
            pos=(801, 537),
            width=SCREEN_WIDTH - 800,
            height=SCREEN_HEIGHT - 520,
            no_resize=True,
            no_title_bar=True,
        )

    def configure_window(self):
        self.fix_location()

    def create(self):
        with self.window:
            with dpg.group(horizontal=True):

                def clear_errors():
                    self.current_log.clear()

                dpg.add_button(label="Clear", callback=clear_errors)
                dpg.bind_item_theme(dpg.last_item(), "clear_button.theme")

                def show_debug():
                    self.current_log = self.state.log

                dpg.add_button(label="Debug", callback=show_debug)

                def show_osc():
                    self.current_log = self.state.osc_log

                dpg.add_button(label="OSC", callback=show_osc)

                def show_midi():
                    self.current_log = self.state.midi_log

                dpg.add_button(label="MIDI", callback=show_midi)

            dpg.add_text(tag="io_debug.text")

    def update(self):
        console_text = "\n".join(str(e) for e in self.current_log[::-1])
        dpg.set_value("io_debug.text", console_text)


class CodeWindow(ResettableWindow):
    def __init__(self, state, *args, **kwargs):
        super().__init__(
            state,
            tag="code_editor.gui.window",
            label="Code Editor",
            no_resize=True,
            show=False,
            no_scrollbar=True,
        )

    def configure_window(self):
        self.fix_location()

    def create(self):
        track = APP._active_track
        clip = APP._active_clip
        obj = None
        if APP.code_view == GLOBAL_VIEW:
            obj = APP.state
        elif APP.code_view == TRACK_VIEW:
            obj = track
        else:
            obj = clip

        if obj is None:
            return

        with self.window:
            dpg.configure_item(
                self.tag, label=f"Code > {getattr(obj, 'name', 'Global')}"
            )

            def save():
                APP.save_menu_callback()
                clip.code.reload()
                track.code.reload()
                self.state.code.reload()

            def show_global(sender, app_data, user_data):
                APP.code_view = GLOBAL_VIEW
                self.reset()

            def show_track(sender, app_data, user_data):
                APP.code_view = TRACK_VIEW
                self.reset()

            def show_clip(sender, app_data, user_data):
                APP.code_view = CLIP_VIEW
                self.reset()

            with dpg.group(horizontal=True, height=20):
                dpg.add_button(label="Save", callback=APP.save_menu_callback)
                dpg.add_button(
                    label="Help",
                    callback=APP.help_window.reset_callback,
                )
                dpg.add_button(label="|", tag=self.tag + ".button.separator")
                dpg.add_button(
                    tag=self.tag + ".button.global",
                    label="Global",
                    callback=show_global,
                )
                dpg.add_button(
                    tag=self.tag + ".button.clip", label="Clip", callback=show_clip
                )
                dpg.add_button(
                    tag=self.tag + ".button.track", label="Track", callback=show_track
                )

            # TODO: Should create this in the value_registry
            dpg.add_input_text(
                tag=self.tag + ".text",
                source=get_code_window_tag(obj) + ".text",
                multiline=True,
                readonly=False,
                tab_input=True,
                width=APP.window_manager.screen_width - 800 - 35,
                height=APP.window_manager.screen_height - 520 - 70,
                on_enter=False,
            )

        dpg.bind_item_theme(self.tag + ".button.track", "code_editor.track.theme")
        dpg.bind_item_theme(self.tag + ".button.clip", "code_editor.clip.theme")
        dpg.bind_item_theme(self.tag + ".button.global", "code_editor.global.theme")
        dpg.bind_item_theme(
            self.tag + ".button.separator", "code_editor.separator.theme"
        )

        if isinstance(obj, model.Track):
            dpg.bind_item_theme(self.tag + ".text", "code_editor.track.theme")
        elif isinstance(obj, model.Clip):
            dpg.bind_item_theme(self.tag + ".text", "code_editor.clip.theme")
        else:  # State
            dpg.bind_item_theme(self.tag + ".text", "code_editor.global.theme")


# TODO: Not being used
class AutomationWindow(Window):
    def __init__(self, state, clip, input_channel):
        self.clip = clip
        self.input_channel = input_channel
        super().__init__(
            state,
            tag=get_source_node_window_tag(input_channel),
            label="Automation Window",
            pos=WINDOW_INFO["code_pos"],
            width=WINDOW_INFO["code_size"][0],
            height=WINDOW_INFO["code_size"][1],
            no_resize=True,
        )

    def configure_window(self):
        self.fix_location()

    # TODO: Finish conversion
    def create(self):
        with self.window:
            self.tags["hide_on_clip_selection"].append(parent)

            automation = input_channel.active_automation

            series_tag = f"{input_channel.id}.series"
            plot_tag = get_plot_tag(input_channel)
            playhead_tag = f"{input_channel.id}.gui.playhead"
            ext_value_tag = f"{input_channel.id}.gui.ext_value"
            menu_tag = f"{input_channel.id}.menu"

            with dpg.menu_bar(tag=menu_tag):
                dpg.add_menu_item(
                    tag=f"{input_channel.id}.gui.automation_enable_button",
                    label="Disable" if input_channel.mode == "automation" else "Enable",
                    callback=self.toggle_automation_mode_callback,
                    user_data=input_channel,
                )
                # dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_record_button", label="Record", callback=self.enable_recording_mode_callback, user_data=input_channel)

                preset_menu_tag = f"{input_channel.id}.preset_menu"
                with dpg.menu(tag=preset_menu_tag, label="Automation"):
                    dpg.add_menu_item(
                        label="New Automation",
                        callback=self.add_preset_callback,
                        user_data=input_channel,
                    )
                    dpg.add_menu_item(
                        label="Reorder",
                        callback=self.create_and_show_reorder_window,
                        user_data=(
                            input_channel.automations,
                            preset_menu_tag,
                            get_preset_sub_menu_tag,
                        ),
                    )
                    for automation in input_channel.automations:
                        self.add_automation_tab(input_channel, automation)

                dpg.add_menu_item(
                    label="1",
                    callback=self.default_time_callback,
                    user_data=input_channel,
                )

                dpg.add_menu_item(
                    label="x2",
                    callback=self.double_time_callback,
                    user_data=input_channel,
                )

                dpg.add_menu_item(
                    label="/2",
                    callback=self.half_time_callback,
                    user_data=input_channel,
                )

                def update_automation_length(sender, app_data, user_data):
                    if app_data:
                        input_channel = user_data
                        input_channel.active_automation.set_length(float(app_data))
                        self.reset_automation_plot(input_channel)

                def update_preset_name(sender, app_data, user_data):
                    input_channel = user_data
                    automation = input_channel.active_automation
                    if automation is None:
                        return
                    automation.name = app_data

                    preset_sub_menu_tag = get_preset_sub_menu_tag(automation)
                    dpg.configure_item(preset_sub_menu_tag, label=app_data)

                prop_x_start = 600
                dpg.add_text("Preset:", pos=(prop_x_start - 200, 0))
                dpg.add_input_text(
                    tag=f"{parent}.preset_name",
                    label="",
                    default_value="",
                    pos=(prop_x_start - 150, 0),
                    on_enter=True,
                    callback=update_preset_name,
                    user_data=input_channel,
                    width=100,
                )

                dpg.add_text("Beats:", pos=(prop_x_start + 200, 0))
                dpg.add_input_text(
                    tag=f"{parent}.beats",
                    label="",
                    default_value=input_channel.active_automation.length,
                    pos=(prop_x_start + 230, 0),
                    on_enter=True,
                    callback=update_automation_length,
                    user_data=input_channel,
                    width=50,
                )

            with dpg.plot(
                label=input_channel.active_automation.name,
                height=-1,
                width=-1,
                tag=plot_tag,
                query=True,
                callback=self.print_callback,
                anti_aliased=True,
                no_menus=True,
            ):
                min_value = input_channel.get_parameter("min").value
                max_value = input_channel.get_parameter("max").value
                x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.add_plot_axis(
                    dpg.mvXAxis, label="x", tag=x_axis_limits_tag, no_gridlines=True
                )
                dpg.set_axis_limits(
                    dpg.last_item(),
                    gui.AXIS_MARGIN,
                    input_channel.active_automation.length,
                )

                dpg.add_plot_axis(
                    dpg.mvYAxis, label="y", tag=y_axis_limits_tag, no_gridlines=True
                )
                dpg.set_axis_limits(dpg.last_item(), min_value, max_value)

                dpg.add_line_series(
                    [],
                    [],
                    tag=series_tag,
                    parent=dpg.last_item(),
                )

                self.reset_automation_plot(input_channel)

                dpg.add_line_series(
                    parent=x_axis_limits_tag,
                    label="Playhead",
                    tag=playhead_tag,
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=dpg.get_axis_limits(y_axis_limits_tag),
                )
                dpg.add_line_series(
                    parent=y_axis_limits_tag,
                    label="Ext Value",
                    tag=ext_value_tag,
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=dpg.get_axis_limits(y_axis_limits_tag),
                )

                with dpg.popup(plot_tag, mousebutton=1):
                    dpg.add_menu_item(
                        label="Double Automation",
                        callback=self.double_automation_callback,
                    )
                    dpg.add_menu_item(
                        label="Duplicate Preset",
                        callback=self.duplicate_channel_preset_callback,
                    )
                    with dpg.menu(label="Set Quantize"):
                        dpg.add_menu_item(
                            label="Off",
                            callback=self.set_quantize_callback,
                            user_data=None,
                        )
                        dpg.add_menu_item(
                            label="1 bar",
                            callback=self.set_quantize_callback,
                            user_data=4,
                        )
                        dpg.add_menu_item(
                            label="1/2",
                            callback=self.set_quantize_callback,
                            user_data=2,
                        )
                        dpg.add_menu_item(
                            label="1/4",
                            callback=self.set_quantize_callback,
                            user_data=1,
                        )
                        dpg.add_menu_item(
                            label="1/8",
                            callback=self.set_quantize_callback,
                            user_data=0.5,
                        )
                        dpg.add_menu_item(
                            label="1/16",
                            callback=self.set_quantize_callback,
                            user_data=0.25,
                        )
                    with dpg.menu(label="Shift (Beats)"):
                        with dpg.menu(label="Left"):
                            dpg.add_menu_item(
                                label="4",
                                callback=self.shift_points_callback,
                                user_data=-4,
                            )
                            dpg.add_menu_item(
                                label="2",
                                callback=self.shift_points_callback,
                                user_data=-2,
                            )
                            dpg.add_menu_item(
                                label="1",
                                callback=self.shift_points_callback,
                                user_data=-1,
                            )
                            dpg.add_menu_item(
                                label="1/2",
                                callback=self.shift_points_callback,
                                user_data=-0.5,
                            )
                            dpg.add_menu_item(
                                label="1/4",
                                callback=self.shift_points_callback,
                                user_data=-0.25,
                            )
                        with dpg.menu(label="Right"):
                            dpg.add_menu_item(
                                label="4",
                                callback=self.shift_points_callback,
                                user_data=4,
                            )
                            dpg.add_menu_item(
                                label="2",
                                callback=self.shift_points_callback,
                                user_data=2,
                            )
                            dpg.add_menu_item(
                                label="1",
                                callback=self.shift_points_callback,
                                user_data=1,
                            )
                            dpg.add_menu_item(
                                label="1/2",
                                callback=self.shift_points_callback,
                                user_data=0.5,
                            )
                            dpg.add_menu_item(
                                label="1/4",
                                callback=self.shift_points_callback,
                                user_data=0.25,
                            )
                    with dpg.menu(label="Interpolation Mode"):
                        dpg.add_menu_item(
                            label="Linear",
                            callback=self.set_interpolation_callback,
                            user_data="linear",
                        )
                        dpg.add_menu_item(
                            label="Nearest",
                            callback=self.set_interpolation_callback,
                            user_data="nearest",
                        )
                        dpg.add_menu_item(
                            label="Nearest Up",
                            callback=self.set_interpolation_callback,
                            user_data="nearest-up",
                        )
                        dpg.add_menu_item(
                            label="Zero",
                            callback=self.set_interpolation_callback,
                            user_data="zero",
                        )
                        dpg.add_menu_item(
                            label="S-Linear",
                            callback=self.set_interpolation_callback,
                            user_data="slinear",
                        )
                        dpg.add_menu_item(
                            label="Quadratic",
                            callback=self.set_interpolation_callback,
                            user_data="quadratic",
                        )
                        dpg.add_menu_item(
                            label="Cubic",
                            callback=self.set_interpolation_callback,
                            user_data="cubic",
                        )
                        dpg.add_menu_item(
                            label="Previous",
                            callback=self.set_interpolation_callback,
                            user_data="previous",
                        )
                        dpg.add_menu_item(
                            label="Next",
                            callback=self.set_interpolation_callback,
                            user_data="next",
                        )

            dpg.bind_item_theme(playhead_tag, "playhead_line.theme")
            dpg.bind_item_theme(ext_value_tag, "bg_line.theme")
            dpg.bind_item_theme(series_tag, "automation_line.theme")

            def show_popup(sender, app_data, user_data):
                input_channel = user_data
                popup_tag = f"{input_channel.id}.gui.popup"
                # Right click
                if app_data[0] == 1:
                    dpg.configure_item(item=popup_tag, show=True)


class ClipParametersWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            width=int(SCREEN_WIDTH*APP.window_manager.CLIP_PARAM_WINDOW_PERCENT[0]),
            tag="clip_parameters.gui.window",
            show=True,
            label="Select a Controller ^",
        )

    def configure_window(self):
        self.fix_location()

    def create(self):
        clip = APP._active_clip
        with self.window:
            def set_window_percent_callback(sender, app_data, user_data):
                # TODO: try/except here is s hack. Figure out how this
                # causes an exception when selecting Presets in Clip Preset Window
                try:
                    width = dpg.get_item_width(dpg.get_item_alias(app_data))
                    percent = width / APP.window_manager.screen_width
                    APP.window_manager.CLIP_PARAM_WINDOW_PERCENT[0] = percent
                    APP.window_manager.resize_all()
                except Exception as e:
                    logger.exception(e)

            with dpg.item_handler_registry() as handler:
                dpg.add_item_resize_handler(callback=set_window_percent_callback)
            dpg.bind_item_handler_registry(self.tag, handler)

            if clip is None:
                return

            dpg.configure_item(self.tag, label=f"Inputs > {clip.name}")

            menu_tag = f"{self.tag}.menu_bar"
            with dpg.menu_bar(tag=menu_tag):
                # TODO: The node menu should just add to the current active clip
                # so that we don't need a new menu for each clip
                APP.create_input_channel_menu(menu_tag, clip)

                preset_menu_tag = f"{menu_tag}.preset_menu"
                with dpg.menu(label="Presets", tag=preset_menu_tag):
                    dpg.add_menu_item(
                        label="New Preset",
                        callback=APP.create_and_show_save_presets_window,
                        user_data=(clip, None),
                    )
                    dpg.add_menu_item(
                        label="Reorder",
                        callback=APP.create_and_show_reorder_window,
                        user_data=(
                            clip.presets,
                            preset_menu_tag,
                            get_preset_menu_bar_tag,
                        ),
                    )
                    for preset in clip.presets:
                        if valid(preset):
                            APP.add_clip_preset_to_menu(clip, preset)

                dpg.add_menu_item(
                    label="Show All Automation",
                    callback=action_callback,
                    user_data=ShowWindow(APP.clip_automation_presets_window),
                )

            with dpg.group(horizontal=True):
                width = (
                    2
                    * APP.window_manager.window_info["edit"]["clip_parameters_size"][0]
                    // 3
                )
                with dpg.child_window(
                    width=width,
                    no_scrollbar=True,
                ):
                    with dpg.group(
                        tag="input_child_window_group", width=width, height=1000
                    ):
                        with dpg.table(
                            header_row=True,
                            tag="clip_parameters.input.table.gui",
                            policy=dpg.mvTable_SizingFixedFit,
                        ):
                            dpg.add_table_column(
                                label="Input",
                                tag="clip_parameters.input.table.column.input",
                                width_stretch=True,
                                init_width_or_weight=0.2,
                            )
                            dpg.add_table_column(
                                label="Type",
                                tag="clip_parameters.input.table.column.type",
                                width_stretch=True,
                                init_width_or_weight=0.1,
                            )
                            dpg.add_table_column(
                                label="Value",
                                tag="clip_parameters.input.table.column.output",
                                width_stretch=True,
                                init_width_or_weight=0.2,
                            )
                            dpg.add_table_column(
                                label="Parameters",
                                tag="clip_parameters.input.table.column.parameters",
                                width_stretch=True,
                                init_width_or_weight=0.4,
                            )
                            dpg.add_table_column(
                                tag="clip_parameters.input.table.column.edit",
                                width_stretch=True,
                                init_width_or_weight=0.1,
                            )

                            for input_index, input_channel in enumerate(clip.inputs):
                                if input_channel.deleted:
                                    continue

                                with dpg.table_row():
                                    if input_channel.input_type in [
                                        "int",
                                        "float",
                                        "bool",
                                        "midi",
                                        "osc_input_int",
                                        "osc_input_float",
                                    ]:
                                        dpg.add_button(
                                            tag=f"{input_channel.id}.name_button",
                                            label=input_channel.name,
                                            callback=APP.action_callback,
                                            user_data=SelectInputChannel(
                                                {"clip": clip, "channel": input_channel}
                                            ),
                                        )

                                        dpg.add_text(
                                            default_value=input_channel.input_type
                                        )

                                        with dpg.group(
                                            horizontal=True, horizontal_spacing=5
                                        ):
                                            if input_channel.mode == "automation":
                                                dpg.add_simple_plot(
                                                    height=20,
                                                    width=50,
                                                    tag=f"{input_channel.id}.mini_plot",
                                                )
                                                add_func = (
                                                    dpg.add_input_float
                                                    if input_channel.dtype == "float"
                                                    else dpg.add_input_int
                                                )
                                                add_func(
                                                    source=f"{input_channel.id}.value",
                                                    readonly=True,
                                                    step=0,
                                                    width=40,
                                                )
                                            else:
                                                add_func = (
                                                    dpg.add_drag_float
                                                    if input_channel.dtype == "float"
                                                    else dpg.add_drag_int
                                                )
                                                add_func(
                                                    min_value=input_channel.get_parameter(
                                                        "min"
                                                    ).value,
                                                    max_value=input_channel.get_parameter(
                                                        "max"
                                                    ).value,
                                                    source=f"{input_channel.id}.value",
                                                    width=95,
                                                    callback=APP.update_input_channel_ext_value_callback,
                                                    user_data=input_channel,
                                                )

                                    elif input_channel.input_type == "color":
                                        dpg.add_button(
                                            tag=f"{input_channel.id}.name_button",
                                            label=input_channel.name,
                                            callback=APP.action_callback,
                                            user_data=SelectInputChannel(
                                                {"clip": clip, "channel": input_channel}
                                            ),
                                        )
                                        dpg.bind_item_theme(
                                            f"{input_channel.id}.name_button",
                                            get_node_tag(input_channel) + ".theme",
                                        )

                                        dpg.add_text(default_value="color")

                                        def update_color(sender, app_data, user_data):
                                            # Color picker returns values between 0 and 1. Convert
                                            # to 255 int value.
                                            [
                                                int(util.clamp(v * 255, 0, 255))
                                                for v in app_data
                                            ]
                                            APP.update_channel_value_callback(
                                                sender, app_data, user_data
                                            )

                                        dpg.add_drag_intx(
                                            source=f"{input_channel.id}.value",
                                            width=100,
                                            callback=update_color,
                                            user_data=input_channel,
                                            max_value=255,
                                            size=4,
                                        )

                                    elif input_channel.input_type == "button":
                                        dpg.add_button(
                                            tag=f"{input_channel.id}.name_button",
                                            label=input_channel.name,
                                            callback=APP.action_callback,
                                            user_data=SelectInputChannel(
                                                {"clip": clip, "channel": input_channel}
                                            ),
                                        )
                                        dpg.add_text(default_value="button")

                                        def update_button(sender, app_data, user_data):
                                            APP.update_channel_value_callback(
                                                sender, int(app_data), user_data
                                            )

                                        dpg.add_checkbox(
                                            callback=update_button,
                                            user_data=input_channel,
                                            default_value=bool(input_channel.get()),
                                        )

                                    param_strs = []
                                    for parameter_index, parameter in enumerate(
                                        input_channel.parameters
                                    ):
                                        if str(parameter.value):
                                            param_strs.append(
                                                f"{parameter.name}: {parameter.value}"
                                            )
                                    dpg.add_text(default_value=", ".join(param_strs))

                                    # Edit
                                    def show_properties_window(
                                        sender, app_data, user_data
                                    ):
                                        APP._properties_buffer.clear()
                                        dpg.configure_item(
                                            get_properties_window_tag(user_data),
                                            show=True,
                                        )

                                    dpg.add_button(
                                        label="Edit",
                                        callback=show_properties_window,
                                        user_data=input_channel,
                                    )

                                    # Right click for input channel name button
                                    with dpg.popup(
                                        f"{input_channel.id}.name_button", mousebutton=1
                                    ):
                                        dpg.add_menu_item(
                                            label="Copy",
                                        )
                                        dpg.add_menu_item(
                                            label="Delete",
                                            callback=APP.create_and_show_delete_obj_callback,
                                            user_data=input_channel,
                                        )

                # Right click for entire window
                with dpg.popup("input_child_window_group", mousebutton=1):
                    dpg.add_menu_item(
                        label="Paste",
                    )

                with dpg.child_window(no_scrollbar=True):
                    with dpg.table(
                        header_row=True,
                        tag="clip_parameters.output.table.gui",
                        policy=dpg.mvTable_SizingStretchProp,
                    ):
                        dpg.add_table_column(
                            label="Output",
                            tag="clip_parameters.output.table.column.input",
                        )
                        dpg.add_table_column(
                            label="Value",
                            tag="clip_parameters.output.table.column.type",
                        )
                        for output_index, output in enumerate(clip.outputs):
                            if output.deleted:
                                continue

                            def add_output_row(channel):
                                with dpg.table_row():
                                    dpg.add_text(
                                        default_value=f"[{channel.dmx_address}] {channel.name}",
                                    )
                                    dpg.add_input_int(
                                        source=f"{channel.id}.value",
                                        width=50,
                                        readonly=True,
                                        step=0,
                                    )

                            if isinstance(output, model.DmxOutputGroup):
                                for i, output_channel in enumerate(output.outputs):
                                    add_output_row(output_channel)
                            else:
                                add_output_row(output)
