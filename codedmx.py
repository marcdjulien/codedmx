import dearpygui.dearpygui as dpg
import time
import re
from threading import RLock, Thread

import model
import gui
import fixtures

import numpy as np
import os
from collections import defaultdict
import json
from cProfile import Profile
from pstats import SortKey, Stats
import argparse
import subprocess
import sys
import logging
import util

logging.basicConfig(
    filename="log.txt",
    filemode="w",
    format="[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
    level=logging.DEBUG,
)

logger = logging.getLogger(__name__)

PROJECT_EXTENSION = "ndmx"
VARIABLE_NAME_PATTERN = r"[a-zA-Z_][a-zA-Z\d_]*$"
HUMAN_DELAY = 0.125

DEFAULT_SEQUENCE_DURATION = 4  # beats


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


class Application:
    """Runs the main dpg loop and state updates."""

    def __init__(self, debug):
        # Debug flag.
        self.debug = debug

        # Model and state logic
        self.state = model.ProgramState()

        # State of GUI elements.
        # TODO: Move to IOWindow
        self.gui_state = {
            # I/O type
            "io_types": {
                "inputs": [None] * 5,
                "outputs": [None] * 5,
            },
            # I/O arguments
            "io_args": {
                "inputs": [None] * 5,
                "outputs": [None] * 5,
            },
            # Last active clip for each track
            "track_last_active_clip": {},
            # Themes for each clip preset
            "clip_preset_themes": {},
        }

        # Special DPG tags to keep track of.
        self.tags = {
            # Drag Point tags for the currently selected automation window
            "point_tags": [],
            # Things to hide in the gui when a clip is selected.
            "hide_on_clip_selection": [],
        }

        self.cache = {"recent": []}

        # Current position of the mouse
        self.mouse_x, self.mouse_y = 0, 0

        # Position of the mouse while being dragged.
        self.mouse_drag_x, self.mouse_drag_y = 0, 0

        # Position of the mouse last time it was clicked.
        self.mouse_click_x, self.mouse_click_y = 0, 0

        # Current code view mode.
        self.code_view = gui.CLIP_INIT_CODE_VIEW

        # Whether keyboard mode is enabled.
        self.keyboard_mode = False

        self._active_track = None
        self._active_clip = None
        self._active_clip_slot = None
        self._active_input_channel = None
        self._active_presets = {}

        # TODO: These can be held in the respective Window's object.
        self._properties_buffer = defaultdict(dict)

        self._tap_tempo_buffer = [0, 0, 0, 0, 0]
        self._quantize_amount = None

        self.ctrl = False
        self.shift = False

        self.copy_buffer = []

        self.lock = RLock()
        self.past_actions = []

        # Windows
        self.clip_preset_window = None
        self.multi_clip_preset_window = None
        self.save_new_multi_clip_preset_window = None
        self.clip_automation_presets_window = None
        self.global_storage_debug_window = None
        self.io_window = None
        self.inspector_window = None
        self.rename_window = None
        self.track_properties_windows = {}
        self.console_window = None
        self.remap_midi_device_window = None
        self.code_window = None
        self.reorder_window = None
        self.sequence_configuration_window = None
        self.sequences_window = None
        self.preset_configuration_window = None
        self.python_modules_window = None

        self.window_manager = gui.WindowManager(self)

    def run(self):
        """Initialize then run the main loop."""
        self.initialize()
        self.main_loop()

    def initialize(self):
        # Init main context.
        logging.debug("Create dearpygui context")
        dpg.create_context()

        # Initialize dpg values
        logging.debug("Initializing value registry")
        with dpg.value_registry():
            # The last midi message received
            dpg.add_string_value(default_value="", tag="last_midi_message")
            # Number of global elements
            dpg.add_int_value(default_value=0, tag="n_global_storage_elements")

        # Create main viewport.
        logging.debug("Create viewport")
        dpg.create_viewport(
            title=f"CodeDMX [{self.state.project_name}] *",
            width=gui.SCREEN_WIDTH,
            height=gui.SCREEN_HEIGHT,
            x_pos=50,
            y_pos=0,
            large_icon=gui.ICON,
            small_icon=gui.ICON,
        )

        #### Init Themes ####
        logging.debug("Creating themes")
        self.create_themes()

        #### Create Console ####
        logging.debug("Creating console window")
        self.console_window = gui.ConsoleWindow(self.state)

        logging.debug("Creating performance windows")
        self.clip_preset_window = gui.ClipPresetWindow(self.state)
        self.save_new_multi_clip_preset_window = gui.SaveNewMultiClipPresetWindow(
            self.state
        )
        self.multi_clip_preset_window = gui.MultiClipPresetWindow(self.state)
        self.clip_automation_presets_window = gui.ClipAutomationPresetWindow(self.state)
        self.add_new_trigger_window = gui.AddNewTriggerWindow(self.state)
        self.manage_trigger_window = gui.ManageTriggerWindow(self.state)
        self.remap_midi_device_window = gui.RemapMidiDeviceWindow(self.state)
        self.reorder_window = gui.ReorderWindow(self.state)
        self.sequence_configuration_window = gui.SequenceConfigurationWindow(self.state)
        self.sequences_window = gui.SequencesWindow(self.state)
        self.preset_configuration_window = gui.PresetConfigurationWindow(self.state)

        #### Help Window ####
        self.help_window = gui.HelpWindow(self.state)

        #### Global Storage Debug Window ####
        self.global_storage_debug_window = gui.GlobalStorageDebugWindow(self.state)

        #### Create Code Editor Windows ####
        logging.debug("Creating code editor windows")
        self.code_window = gui.CodeWindow(self.state)

        #### Create Clip Parameters Window ####
        logging.debug("Creating clip parameters window")
        self.clip_params_window = gui.ClipParametersWindow(self.state)

        #### Create Clip Window ####
        logging.debug("Creating clip window")
        self.clip_window = gui.ClipWindow(self.state)

        #### Mouse/Key Handlers ####
        logging.debug("Installing mouse/key handlers")
        with dpg.handler_registry():
            dpg.add_mouse_move_handler(callback=self.mouse_move_callback)
            dpg.add_mouse_click_handler(callback=self.mouse_click_callback)
            dpg.add_mouse_double_click_handler(
                callback=self.mouse_double_click_callback
            )
            dpg.add_key_press_handler(callback=self.key_press_callback)
            dpg.add_key_down_handler(callback=self.key_down_callback)
            dpg.add_key_release_handler(callback=self.key_release_callback)

        logging.debug("Creating inspector window")
        self.inspector_window = gui.InspectorWindow(self.state)

        logging.debug("Creating i/o window")
        self.io_window = gui.IOWindow(self.state)

        logging.debug("Creating python modules window")
        self.python_modules_window = gui.PythonModulesWindow(self.state)
        
        logging.debug("Creating rename window")
        self.rename_window = gui.RenameWindow(self.state)

        #### File Dialogs ####
        logging.debug("Creating viewport menu bar")
        self.create_viewport_menu_bar()

        # Initialize Tracks
        self._active_track = self.state.tracks[0]

        # Need to create these after the node_editor_windows
        for track in self.state.tracks:
            self.track_properties_windows[track.id] = gui.TrackPropertiesWindow(
                self.state, track
            )

        # Automation Windows
        logging.debug("Creating Automation Windows")

        for track in self.state.tracks:
            for clip in track.clips:
                if not util.valid(clip):
                    continue
                # Moved this to CreateNewClip
                #for input_channel in clip.inputs:
                #    self.add_input_channel_callback(
                #        sender=None,
                #        app_data=None,
                #        user_data=(
                #            "restore",
                #            (clip, input_channel),
                #        ),
                #    )


        logging.debug("Initializing window settings")
        dpg.set_viewport_resize_callback(
            callback=self.window_manager.resize_windows_callback
        )

        self.restore_gui_state()

        dpg.setup_dearpygui()
        dpg.show_viewport()
        # dpg.show_item_registry()
        # dpg.show_metrics()

    def main_loop(self):
        logging.debug("Starting main loop")

        # State loop runs in a separate thread so that
        # lighting fixtures continue to operate even if GUI locks up.
        thread = Thread(target=self.state_loop)
        thread.daemon = True
        thread.start()

        # Gui runs in this main thread.
        try:
            while dpg.is_dearpygui_running():
                self.update_clip_window()
                with self.lock:
                    self.update_clip_preset_window()
                    self.update_gui_from_state()
                dpg.render_dearpygui_frame()
            dpg.destroy_context()
        except Exception as e:
            import traceback

            logger.warning(traceback.format_exc())
            logger.warning(e)
            raise e

    def state_loop(self):
        # Runs at 60 Hz
        period = 1.0 / 60.0
        while True:
            t_start = time.time()
            self.state.update()
            t_end = time.time()
            delta_t = t_end - t_start
            if delta_t < period:
                time.sleep(period - delta_t)

    def gui_lock(func):
        """Return a wrapper that will grab the GUI lock."""

        def wrapper(self, sender, app_data, user_data):
            with self.lock:
                return func(self, sender, app_data, user_data)

        return wrapper

    def execute_wrapper(self, command):
        """Wrapper around execute.

        Should always use this function instead of ProgramState::execute
        directly, so that the Application knows when the state is modified.
        """
        dpg.set_viewport_title(f"CodeDMX [{self.state.project_name}] *")
        result = self.state.execute(command)

        skip = [
            "update_automation_point",
            "set_clip",
            "add_multi_clip_preset",
        ]
        if not any(kw in command for kw in skip):
            self.clip_params_window.reset()

        return result

    def action(self, action: gui.GuiAction):
        # Gui actions can modify state. Use the lock to make sure
        # the enture state is updated before the GUI tries to render.
        with self.lock:
            action.execute()
        self.past_actions.append(action)

    def update_clip_window(self):
        for track_i, track in enumerate(self.state.tracks):
            for clip_i, clip in enumerate(track.clips):
                clip_color = [0, 0, 0, 100]

                if self._active_clip_slot == (track_i, clip_i):
                    clip_color[2] += 155

                if util.valid(clip) and clip == self._active_clip:
                    clip_color[2] += 255
                    clip_color[1] += 50

                if util.valid(clip) and clip.playing:
                    clip_color[1] += 255

                if util.valid(clip) and not clip.playing:
                    clip_color[2] += 100
                    clip_color[1] += 50

                if not util.valid(clip):
                    if track.global_track:
                        clip_color[0:3] = 70, 70, 50
                    else:
                        clip_color[0:3] = 50, 50, 50

                if self._active_clip_slot == (track_i, clip_i):
                    clip_color[3] += 50

                dpg.highlight_table_cell(
                    "clip_window.table", clip_i + 1, track_i, color=clip_color
                )

            if self._active_track == track:
                dpg.highlight_table_column(
                    "clip_window.table", track_i, color=[100, 100, 100, 255]
                )
            else:
                dpg.highlight_table_column(
                    "clip_window.table", track_i, color=[0, 0, 0, 0]
                )

    def update_clip_preset_window(self):
        for track in self.state.tracks:
            active_preset = self._active_presets.get(track.id, None)
            for clip in track.clips:
                if util.valid(clip):
                    for preset in clip.presets:
                        button_tag = get_preset_button_tag(preset)
                        if active_preset == preset:
                            dpg.bind_item_theme(button_tag, "selected_preset2.theme")
                        else:
                            dpg.bind_item_theme(
                                button_tag, get_channel_preset_theme(preset)
                            )

    def add_clip_preset_to_menu(self, clip, preset, before=None):
        menu_tag = f"{self.clip_params_window.tag}.menu_bar"
        preset_menu_tag = f"{menu_tag}.preset_menu"
        preset_menu_bar = get_preset_menu_bar_tag(preset)
        preset_theme = get_channel_preset_theme(preset)

        def set_color(sender, app_data, user_data):
            if app_data is None:
                color = dpg.get_value(sender)
            else:
                color = [int(255 * v) for v in app_data]
            dpg.configure_item(f"{preset_theme}.text_color", value=color)
            dpg.configure_item(f"{preset_theme}.button_bg_color", value=color)
            self.gui_state["clip_preset_themes"][f"{preset_theme}.text_color"] = color
            self.gui_state["clip_preset_themes"][
                f"{preset_theme}.button_bg_color"
            ] = color

        def duplicate_clip_preset(sender, app_data, user_data):
            clip, preset = user_data
            result = self.execute_wrapper(
                f"duplicate_clip_preset {clip.id} {preset.id}"
            )
            if not result.success:
                self.state.log.append("Failed to duplicate clip")

        with dpg.menu(
            parent=preset_menu_tag,
            tag=preset_menu_bar,
            label=preset.name,
            before=get_preset_menu_bar_tag(before) if util.valid(before) else 0,
        ):
            dpg.add_menu_item(
                tag=f"{preset_menu_bar}.activate",
                label="Activate",
                callback=self.play_clip_preset_callback,
                user_data=preset,
            )
            dpg.add_menu_item(
                tag=f"{preset_menu_bar}.edit",
                label="Edit",
                callback=self.preset_configuration_window.configure_and_show,
                user_data=(clip, preset),
            )
            dpg.add_menu_item(
                tag=f"{preset_menu_bar}.duplicate",
                label="Duplicate",
                callback=duplicate_clip_preset,
                user_data=(clip, preset),
            )
            with dpg.menu(label="Select Color"):
                with dpg.group(horizontal=True):
                    dpg.add_color_button(
                        callback=set_color, default_value=(0, 200, 255)
                    )
                    dpg.add_color_button(
                        callback=set_color, default_value=(0, 255, 100)
                    )
                    dpg.add_color_button(
                        callback=set_color, default_value=(255, 100, 100)
                    )
                    dpg.add_color_button(
                        callback=set_color, default_value=(255, 255, 255)
                    )
                    dpg.add_color_button(callback=set_color, default_value=(0, 0, 0))
                dpg.add_color_picker(
                    display_type=dpg.mvColorEdit_uint8, callback=set_color
                )
            dpg.add_menu_item(
                tag=f"{preset_menu_bar}.delete",
                label="Delete",
                callback=self.delete_clip_preset_callback,
                user_data=preset,
            )

        dpg.bind_item_theme(preset_menu_bar, preset_theme)

    def create_automation_input_channel_window(self, input_channel):
        parent = get_source_node_window_tag(input_channel)
        with dpg.window(
            tag=parent,
            label="Automation Window",
            show=False,
            no_move=True,
            no_title_bar=True,
        ):
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
                        callback=self.reorder_window.configure_and_show,
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

    def create_input_channel_window(self, input_channel):
        parent = get_source_node_window_tag(input_channel)
        self.tags["hide_on_clip_selection"].append(parent)

        input_type = input_channel.input_type

        # ColorNode
        if input_type == "color":

            def update_color(sender, app_data, user_data):
                # Color picker returns values between 0 and 1. Convert
                # to 255 int value.
                rgb = [int(util.clamp(v * 255, 0, 255)) for v in app_data]
                self.update_channel_value_callback(sender, rgb, user_data)

            width = 520
            height = 520
            with dpg.window(
                tag=parent,
                label="Automation Window",
                width=width,
                height=height,
                pos=(799, 18),
                show=False,
                no_move=True,
                no_title_bar=True,
            ):
                default_color = input_channel.get()
                node_theme = get_node_tag(input_channel) + ".theme"
                with dpg.theme(tag=node_theme):
                    with dpg.theme_component(dpg.mvAll):
                        dpg.add_theme_color(
                            tag=f"{node_theme}.color1",
                            target=dpg.mvNodeCol_NodeBackground,
                            value=default_color,
                            category=dpg.mvThemeCat_Nodes,
                        )
                        dpg.add_theme_color(
                            tag=f"{node_theme}.color2",
                            target=dpg.mvNodeCol_NodeBackgroundHovered,
                            value=default_color,
                            category=dpg.mvThemeCat_Nodes,
                        )
                        dpg.add_theme_color(
                            tag=f"{node_theme}.color3",
                            target=dpg.mvNodeCol_NodeBackgroundSelected,
                            value=default_color,
                            category=dpg.mvThemeCat_Nodes,
                        )
                        dpg.add_theme_color(
                            tag=f"{node_theme}.row_bg",
                            target=dpg.mvThemeCol_Button,
                            value=default_color,
                            category=dpg.mvThemeCat_Core,
                        )
                dpg.add_color_picker(
                    width=height * 0.8,
                    height=height,
                    callback=update_color,
                    user_data=input_channel,
                    default_value=default_color,
                    display_type=dpg.mvColorEdit_uint8,
                )

        # ButtonNode
        elif input_type == "button":
            width = 520
            height = 520
            with dpg.window(
                tag=parent,
                label="Automation Window",
                width=width,
                height=height,
                pos=(799, 18),
                show=False,
                no_move=True,
                no_title_bar=True,
            ):
                pass

    def add_automation_tab(self, input_channel, automation):
        preset_menu_tag = f"{input_channel.id}.preset_menu"
        preset_sub_menu_tag = get_preset_sub_menu_tag(automation)
        with dpg.menu(
            parent=preset_menu_tag, tag=preset_sub_menu_tag, label=automation.name
        ):
            dpg.add_menu_item(
                tag=f"{preset_sub_menu_tag}.activate",
                label="Activate",
                callback=self.select_automation_callback,
                user_data=(input_channel, automation),
            )
            dpg.add_menu_item(
                tag=f"{preset_sub_menu_tag}.duplicate",
                label="Duplicate",
                callback=self.duplicate_channel_preset_callback,
                user_data=automation,
            )
            dpg.add_menu_item(
                tag=f"{preset_sub_menu_tag}.delete",
                label="Delete",
                callback=self.delete_automation_callback,
                user_data=(input_channel, automation),
            )

    def reset_automation_plot(self, input_channel):
        if not isinstance(input_channel, model.AutomatableSourceNode):
            return

        window_tag = get_source_node_window_tag(input_channel)
        automation = input_channel.active_automation
        plot_tag = get_plot_tag(input_channel)
        x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
        y_axis_limits_tag = f"{plot_tag}.y_axis_limits"

        dpg.configure_item(plot_tag, label=input_channel.active_automation.name)

        dpg.set_axis_limits(
            x_axis_limits_tag,
            -gui.AXIS_MARGIN,
            input_channel.active_automation.length + gui.AXIS_MARGIN,
        )

        min_value = input_channel.get_parameter("min").value
        max_value = input_channel.get_parameter("max").value
        y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
        dpg.set_axis_limits(y_axis_limits_tag, min_value, max_value)

        dpg.set_value(f"{window_tag}.beats", value=automation.length)
        dpg.set_value(f"{window_tag}.preset_name", value=automation.name)

        # Always delete and redraw all the points
        for tag in self.tags["point_tags"]:
            dpg.delete_item(tag)

        self.tags["point_tags"].clear()
        for point in automation.points:
            if point.deleted:
                continue

            point_tag = f"{point.id}.gui.point"
            x, y = point.x, point.y
            dpg.add_drag_point(
                color=[0, 255, 255, 255],
                default_value=[x, y],
                callback=self.update_automation_point_callback,
                parent=plot_tag,
                tag=point_tag,
                user_data=(automation, point),
                thickness=1,
            )
            self.tags["point_tags"].append(point_tag)

        # Add quantization bars
        y_limits = dpg.get_axis_limits(y_axis_limits_tag)
        if self._quantize_amount is not None:
            i = 0
            while True:
                tag = f"gui.quantization_series.{i}"
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
                else:
                    break
                i += 1

            n_bars = int(input_channel.active_automation.length / self._quantize_amount)
            for i in range(n_bars + 1):
                tag = f"gui.quantization_series.{i}"
                value = i * self._quantize_amount
                dpg.add_line_series(
                    x=[value, value],
                    y=y_limits,
                    tag=tag,
                    parent=y_axis_limits_tag,
                )
                dpg.bind_item_theme(tag, "bg_line.theme")

    def create_themes(self):
        # Initialize global theme
        # with dpg.theme() as global_theme:

        # with dpg.theme_component(dpg.mvAll):
        # dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (40, 40, 40), category=dpg.mvThemeCat_Core)
        # dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (40, 40, 40), category=dpg.mvThemeCat_Core)
        # dpg.add_theme_color(dpg.mvThemeCol_Border, (20, 20, 20), category=dpg.mvThemeCat_Core)
        #
        #        # Buttons
        #        dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 15, 40), category=dpg.mvThemeCat_Core)
        #
        #        # Table
        #        dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (0, 35, 70), category=dpg.mvThemeCat_Core)
        #
        #        # Menu
        #        dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (0, 15, 50), category=dpg.mvThemeCat_Core)
        #
        #        # Text
        #        dpg.add_theme_color(dpg.mvThemeCol_Text, (200, 255, 255), category=dpg.mvThemeCat_Core)
        #
        # dpg.bind_theme(global_theme)

        with dpg.theme(tag="clip_text_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Text,
                    [200, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )

        with dpg.theme(tag="red_button_theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (100, 0, 0, 255),
                    category=dpg.mvThemeCat_Core,
                )

        for track in self.state.tracks:
            for clip in track.clips:
                if util.valid(clip):
                    for preset in clip.presets:
                        self.create_preset_theme(preset)

        with dpg.theme(tag="playhead_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line,
                    (255, 255, 0, 255),
                    tag="playhead_line.color",
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_LineWeight, 1, category=dpg.mvThemeCat_Plots
                )

        with dpg.theme(tag="bg_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line,
                    (255, 255, 255, 30),
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_LineWeight, 1, category=dpg.mvThemeCat_Plots
                )

        with dpg.theme(tag="automation_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, (0, 200, 255), category=dpg.mvThemeCat_Plots
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_LineWeight, 3, category=dpg.mvThemeCat_Plots
                )

        with dpg.theme(tag="transport.play_button.pause.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (255, 255, 255, 30),
                    category=dpg.mvThemeCat_Core,
                )
        with dpg.theme(tag="transport.play_button.play.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    gui.PLAY_BUTTON_COLOR,
                    category=dpg.mvThemeCat_Core,
                    tag="transport.play_button.play.theme.color",
                )

        with dpg.theme(tag="code_editor.global.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBg,
                    (100, 255, 0, 10),
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (100, 255, 0, 50),
                    category=dpg.mvThemeCat_Core,
                )
        with dpg.theme(tag="code_editor.track.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBg,
                    (255, 255, 255, 10),
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (255, 255, 255, 50),
                    category=dpg.mvThemeCat_Core,
                )
        with dpg.theme(tag="code_editor.clip.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBg,
                    (0, 100, 255, 10),
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (0, 100, 255, 50),
                    category=dpg.mvThemeCat_Core,
                )
        with dpg.theme(tag="code_editor.separator.theme"):
            with dpg.theme_component(dpg.mvAll):
                for theme in [
                    dpg.mvThemeCol_Button,
                    dpg.mvThemeCol_ButtonHovered,
                    dpg.mvThemeCol_ButtonActive,
                ]:
                    dpg.add_theme_color(
                        theme,
                        (0, 0, 0, 0),
                        category=dpg.mvThemeCat_Core,
                    )

        with dpg.theme(tag="reset_button.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (0, 155, 150, 55),
                    category=dpg.mvThemeCat_Core,
                )

        with dpg.theme(tag="selected_preset.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[0, 0, 0, 255],
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Button,
                    value=[215, 255, 0, 255],
                    category=dpg.mvThemeCat_Core,
                )

        with dpg.theme(tag="selected_preset2.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[255, 255, 100, 255],
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Button,
                    value=[255, 255, 0, 70],
                    category=dpg.mvThemeCat_Core,
                )

        with dpg.theme(tag="not_selected_preset.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Text,
                    value=[255, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    target=dpg.mvThemeCol_Button,
                    value=[50, 50, 50, 255],
                    category=dpg.mvThemeCat_Core,
                )

    def add_fixture(self, sender, app_data, user_data):
        track = user_data[0]
        fixture = user_data[1]
        starting_address = fixture.address

        for output_channel in track.outputs:
            if isinstance(output_channel, model.DmxOutputGroup):
                starting_address = max(
                    starting_address, output_channel.outputs[-1].dmx_address + 1
                )
            else:
                starting_address = max(starting_address, output_channel.dmx_address + 1)

        self.create_track_output_group(
            None,
            None,
            ("create", track, starting_address, fixture.name, fixture.channels),
        )

    def paste_selected(self):
        if self._active_clip is not None:
            for obj in self.copy_buffer:
                if isinstance(obj, model.SourceNode):
                    result = self.execute_wrapper(
                        f"duplicate_node {self._active_clip.id} {obj.id}"
                    )
                    if result.success:
                        new_input_channel = result.payload
                        self.add_input_channel_callback(
                            sender=None,
                            app_data=None,
                            user_data=(
                                "restore",
                                (self._active_clip, new_input_channel),
                            ),
                        )
                    else:
                        raise RuntimeError(f"Failed to duplicate {obj.id}")
                else:
                    raise RuntimeError(f"Failed to duplicate {obj.id}")

        if self._active_clip_slot is not None:
            self.paste_clip(self._active_clip_slot[0], self._active_clip_slot[1])

    def paste_clip(self, track_i, clip_i):
        # TODO: Prevent copy/pasting clips across different tracks (outputs wont match)
        if not self.copy_buffer:
            return

        obj = self.copy_buffer[0]
        if not isinstance(obj, model.Clip):
            return

        clip = obj
        clip_id = clip.id
        result = self.execute_wrapper(f"duplicate_clip {track_i} {clip_i} {clip_id} ")
        if result.success:
            new_clip = result.payload
            self.action(gui.CreateNewClip({"track_i": track_i, "clip_i": clip_i}))
        else:
            raise RuntimeError(f"Failed to duplicate clip {clip_id}")

        self.save_last_active_clip()
        self._active_track = self.state.tracks[track_i]
        self._active_clip = new_clip

    def get_all_valid_clip_input_channels(self):
        src_channels = []
        for track in self.state.tracks:
            for clip in track.clips:
                if clip is None:
                    continue
                for input_channel in clip.inputs:
                    if input_channel.deleted:
                        continue
                    src_channels.append(input_channel)
        return src_channels

    def get_all_valid_clip_output_channels(self):
        dst_channels = []
        found = set()
        for track in self.state.tracks:
            for clip in track.clips:
                if clip is None:
                    continue
                for output_channel in self.get_all_valid_track_output_channels_for_clip(
                    clip
                ):
                    if output_channel.deleted or output_channel.id in found:
                        continue
                    dst_channels.append(output_channel)
                    found.add(output_channel.id)
        return dst_channels

    def get_all_valid_track_output_channels_for_clip(self, clip):
        output_channels = []
        for output_channel in clip.outputs:
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                output_channels.extend(output_channel.outputs)
            else:
                output_channels.append(output_channel)
        return output_channels

    def get_all_valid_node_src_channels(self, clip):
        src_channels = []
        for input_channel in clip.inputs:
            if input_channel.deleted:
                continue
            src_channels.append(input_channel)
        return src_channels

    def get_all_valid_dst_channels(self, clip):
        if clip is None:
            return []
        dst_channels = []
        for output_channel in clip.outputs:
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                dst_channels.extend(output_channel.outputs)
            else:
                dst_channels.append(output_channel)
        return dst_channels

    def update_gui_from_state(self):
        dpg.configure_item(
            "play_button",
            label="[Playing]" if self.state.playing else "[Paused]",
        )
        if self.state.playing:
            g = 0.90 * gui.PLAY_BUTTON_COLOR[1]
            g = max(10, int(g))

            if int(util.beats_to_16th(self.state.time_since_start_beat)) % 4 == 0:
                g = 255
            gui.PLAY_BUTTON_COLOR[1] = g
            dpg.configure_item(
                "transport.play_button.play.theme.color", value=gui.PLAY_BUTTON_COLOR
            )

        # Cache the active clip, since it can change while this function is running
        c_active_clip = self._active_clip
        c_active_input_channel = self._active_input_channel

        if util.valid(c_active_clip):
            # This is only setting the GUI value, so we only need to update the active clip.
            for dst_channel in self.get_all_valid_dst_channels(c_active_clip):
                tag = f"{dst_channel.id}.value"
                dpg.set_value(tag, dst_channel.get())

            # This is only setting the GUI value, so we only need to update the active clip.
            for src_channel in self.get_all_valid_node_src_channels(c_active_clip):
                tag = f"{src_channel.id}.value"
                dpg.set_value(tag, src_channel.get())

                if src_channel.input_type == "color":
                    node_theme = get_node_tag(src_channel) + ".theme"
                    rgb = src_channel.get()
                    dpg.configure_item(f"{node_theme}.color1", value=rgb)
                    dpg.configure_item(f"{node_theme}.color2", value=rgb)
                    dpg.configure_item(f"{node_theme}.color3", value=rgb)
                    dpg.configure_item(f"{node_theme}.row_bg", value=rgb)
                elif src_channel.input_type in [
                    "int",
                    "float",
                    "midi",
                    "osc_input_int",
                    "osc_input_float",
                ]:
                    tag = f"{src_channel.id}.mini_plot"
                    if dpg.does_item_exist(tag):
                        dpg.set_value(tag, src_channel.history)

        # Update automation points
        if (
            util.valid(c_active_input_channel)
            and isinstance(c_active_input_channel, model.AutomatableSourceNode)
            and util.valid(c_active_input_channel.active_automation)
        ):
            xs = np.arange(0, c_active_input_channel.active_automation.length, 0.01)
            ys = c_active_input_channel.active_automation.f(xs).astype(
                float if c_active_input_channel.dtype == "float" else int
            )
            dpg.configure_item(
                f"{c_active_input_channel.id}.series",
                x=xs,
                y=ys,
            )

        # Update GlobalStorageDebugWindow
        if len(model.GlobalStorage.items()) != dpg.get_value(
            "n_global_storage_elements"
        ):
            self.global_storage_debug_window.reset(
                show=self.global_storage_debug_window.shown
            )
        items = tuple(model.GlobalStorage.items())
        for i, (name, obj) in enumerate(items):
            value = obj.value if isinstance(obj, model.CodeEditorChannel) else obj
            dpg.set_value(f"{self.global_storage_debug_window.tag}.{i}", value)

        # Set the play heads to the correct position
        if util.valid(self._active_clip, self._active_input_channel):
            if isinstance(
                self._active_input_channel, model.AutomatableSourceNode
            ) and util.valid(self._active_input_channel.active_automation):
                playhead_tag = f"{self._active_input_channel.id}.gui.playhead"
                ext_value_tag = f"{self._active_input_channel.id}.gui.ext_value"
                dpg.configure_item(
                    f"{self._active_input_channel.id}.gui.automation_enable_button",
                    label="Disable"
                    if self._active_input_channel.mode == "automation"
                    else "Enable",
                )

                playhead_color = {
                    "armed": [255, 100, 100, 255],
                    "recording": [255, 0, 0, 255],
                    "automation": [255, 255, 0, 255],
                    "manual": [200, 200, 200, 255],
                }
                dpg.configure_item(
                    "playhead_line.color",
                    value=playhead_color[self._active_input_channel.mode],
                )
                y_axis_limits_tag = (
                    f"{self._active_input_channel.id}.plot.y_axis_limits"
                )
                playhead_x = (
                    self._active_input_channel.last_beat
                    % self._active_input_channel.active_automation.length
                    if self._active_input_channel.mode
                    in ["automation", "armed", "recording"]
                    else 0
                )
                dpg.configure_item(
                    playhead_tag,
                    x=[playhead_x, playhead_x],
                    y=dpg.get_axis_limits(y_axis_limits_tag),
                )

                x_axis_limits_tag = (
                    f"{self._active_input_channel.id}.plot.x_axis_limits"
                )
                dpg.configure_item(
                    ext_value_tag,
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=[
                        self._active_input_channel.ext_get(),
                        self._active_input_channel.ext_get(),
                    ],
                )

        if model.LAST_MIDI_MESSAGE is not None:
            device_name, message = model.LAST_MIDI_MESSAGE
            channel = message.channel
            note_control, _ = model.midi_value(message)
            dpg.set_value(
                "last_midi_message", f"{device_name}: {channel}/{note_control}"
            )

        # Update IO Window
        red = (255, 0, 0, 100)
        green = [0, 255, 0, 255]
        for inout in ["inputs", "outputs"]:
            for i in range(5):
                table_tag = f"io.{inout}.table"
                io = (
                    self.state.io_inputs[i]
                    if inout == "inputs"
                    else self.state.io_outputs[i]
                )
                color = red if io is None or not io.connected() else green
                if color == green:
                    alpha = 255 - int(
                        util.clamp((time.time() - io.last_io_time) / 0.25, 0, 0.5) * 255
                    )
                    color[3] = alpha
                dpg.highlight_table_cell(table_tag, i, 3, color=color)

        # Update Consoles
        self.console_window.update()

    def mouse_inside_window(self, window_tag):
        try:
            window_x, window_y = dpg.get_item_pos(window_tag)
        except:
            return False
        window_x2, window_y2 = window_x + dpg.get_item_width(
            window_tag
        ), window_y + dpg.get_item_height(window_tag)
        return util.inside(
            (self.mouse_x, self.mouse_y),
            (window_x, window_x2, window_y + 10, window_y2),
        )

    ########################
    ### Create Functions ###
    ########################

    def create_preset_theme(self, preset):
        preset_theme = get_channel_preset_theme(preset)
        text_color_theme_tag = f"{preset_theme}.text_color"
        button_bg_color_theme_tag = f"{preset_theme}.button_bg_color"
        with dpg.theme(tag=preset_theme):
            with dpg.theme_component(dpg.mvAll):
                value = self.gui_state["clip_preset_themes"].get(
                    text_color_theme_tag, [255, 255, 255, 255]
                )
                dpg.add_theme_color(
                    tag=text_color_theme_tag,
                    target=dpg.mvThemeCol_Text,
                    value=value,
                    category=dpg.mvThemeCat_Core,
                )
                value = self.gui_state["clip_preset_themes"].get(
                    button_bg_color_theme_tag, [255, 255, 255, 255]
                )
                dpg.add_theme_color(
                    tag=button_bg_color_theme_tag,
                    target=dpg.mvThemeCol_Border,
                    value=value,
                    category=dpg.mvThemeCat_Core,
                )

    def create_standard_source_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "create":
            clip, dtype = args
            result = self.execute_wrapper(f"create_source {clip.id} {dtype}")
            if not result.success:
                raise RuntimeError("Failed to create input")
            input_channel = result.payload
            if isinstance(app_data, str):
                input_channel.name = app_data

        else:  # restore
            clip, input_channel = args

        self.create_input_channel_registry_values(input_channel)
        self.create_input_channel_window(input_channel)
        self.create_properties_window(input_channel)

    def create_automatable_source_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "create":
            clip, dtype = args
            result = self.execute_wrapper(f"create_source {clip.id} {dtype}")
            if not result.success:
                raise RuntimeError("Failed to create input")
            input_channel = result.payload
            if isinstance(app_data, str):
                input_channel.name = app_data

            result = self.execute_wrapper(f"add_automation {input_channel.id}")
            if not result.success:
                raise RuntimeError("Failed to create automation")
        else:  # restore
            clip, input_channel = args

        self.create_input_channel_registry_values(input_channel)
        self.create_automation_input_channel_window(input_channel)
        self.create_properties_window(input_channel)

    def create_input_channel_registry_values(self, input_channel):
        with dpg.value_registry():
            tag = f"{input_channel.id}.value"
            logging.debug(f"\t{tag}")
            add_func = {
                "float": dpg.add_float_value,
                "int": dpg.add_int_value,
                "bool": dpg.add_int_value,
                "any": dpg.add_float_value,
                "color": dpg.add_int4_value,
                "midi": dpg.add_int_value,
                "osc_input_float": dpg.add_float_value,
                "osc_input_int": dpg.add_int_value,
                "button": dpg.add_int_value,
            }[input_channel.input_type]
            add_func(tag=tag)

            # Add attributes
            dpg.add_string_value(
                tag=f"{input_channel.id}.name", default_value=input_channel.name
            )

            # Add parameters
            parameters = getattr(input_channel, "parameters", [])
            for parameter_index, parameter in enumerate(parameters):
                if parameter.dtype == "bool":
                    dpg.add_bool_value(
                        tag=f"{parameter.id}.value",
                        default_value=parameter.value,
                    )
                else:
                    dpg.add_string_value(
                        tag=f"{parameter.id}.value",
                        default_value=parameter.value
                        if parameter.value is not None
                        else "",
                    )

    def create_input_channel_menu(self, parent, clip):
        # TODO: Remove right_click_menu
        right_click_menu = "popup_menu" in dpg.get_item_alias(parent)

        with dpg.menu(parent=parent, label="Sources"):
            dpg.add_menu_item(
                label="Bool",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "bool"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Integer",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "int"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Float",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "float"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Osc Integer",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "osc_input_int"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Osc Float",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "osc_input_float"), right_click_menu),
            )
            dpg.add_menu_item(
                label="MIDI",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "midi"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Color",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "color"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Button",
                callback=self.add_input_channel_callback,
                user_data=("create", (clip, "button"), right_click_menu),
            )

        # TODO: Implement these
        with dpg.menu(parent=parent, label="Edit"):
            dpg.add_menu_item(
                label="Copy",
            )

            def paste():
                self.paste_selected()
                dpg.configure_item(parent, show=False)

            dpg.add_menu_item(
                label="Paste",
                callback=paste,
            )

    # TODOL: Turn into ResettableWindow
    def create_properties_window(self, obj):
        window_tag = get_properties_window_tag(obj)
        with dpg.window(
            tag=window_tag,
            label="Properties",
            width=500,
            pos=(gui.SCREEN_WIDTH / 3, gui.SCREEN_HEIGHT / 3),
            no_move=True,
            show=False,
            no_title_bar=True,
        ):
            properties_table_tag = f"{window_tag}.properties_table"
            with dpg.table(
                header_row=True,
            ):
                dpg.add_table_column(label=f"{obj.name}")

            with dpg.table(
                header_row=True,
                tag=properties_table_tag,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    dpg.add_text(default_value="Type")
                    dpg.add_text(default_value=obj.nice_title)

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(
                        default_value=obj.name,
                        callback=self.update_attr_buffer_callback,
                        user_data=("name", f"{obj.id}.name"),
                        source=f"{obj.id}.name",
                        no_spaces=True,
                    )

                if isinstance(obj, model.Parameterized):
                    for parameter_index, parameter in enumerate(obj.parameters):
                        with dpg.table_row():
                            dpg.add_text(default_value=parameter.name)
                            with dpg.group(horizontal=True):
                                if parameter.dtype == "bool":
                                    dpg.add_checkbox(
                                        source=f"{parameter.id}.value",
                                        callback=self.update_parameter_buffer_callback,
                                        user_data=(parameter, parameter_index),
                                        default_value=parameter.value,
                                    )
                                else:
                                    dpg.add_input_text(
                                        source=f"{parameter.id}.value",
                                        callback=self.update_parameter_buffer_callback,
                                        user_data=(parameter, parameter_index),
                                        default_value=parameter.value
                                        if parameter.value is not None
                                        else "",
                                    )

                                if obj.input_type == "midi" and parameter.name == "id":
                                    dpg.add_button(
                                        label="Learn",
                                        callback=self.create_and_show_learn_midi_map_window_callback,
                                        user_data=(obj, "input"),
                                    )

                                    def unmap_midi(sender, app_data, user_data):
                                        obj = user_data
                                        result = self.execute_wrapper(
                                            f"unmap_midi {obj.id}"
                                        )
                                        if result.success:
                                            device_parameter_id = obj.get_parameter_id(
                                                "device"
                                            )
                                            id_parameter_id = obj.get_parameter_id("id")
                                            dpg.set_value(
                                                f"{device_parameter_id}.value",
                                                obj.get_parameter("device").value,
                                            )
                                            dpg.set_value(
                                                f"{id_parameter_id}.value",
                                                obj.get_parameter("id").value,
                                            )

                                    dpg.add_button(
                                        label="Clear",
                                        callback=unmap_midi,
                                        user_data=obj,
                                    )

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Save",
                            callback=self.save_properties_callback,
                            user_data=(obj,),
                        )

                        def cancel_properties_callback(sender, app_data, user_data):
                            obj = user_data
                            for parameter in obj.parameters:
                                dpg.set_value(f"{parameter.id}.value", parameter.value)
                            dpg.configure_item(window_tag, show=False)
                            dpg.set_value(f"{obj.id}.name", obj.name)

                        dpg.add_button(
                            label="Cancel",
                            callback=cancel_properties_callback,
                            user_data=obj,
                        )

                        delete_button = dpg.add_button(
                            label="Delete",
                            callback=self.create_and_show_delete_obj_window,
                            user_data=obj,
                        )
                        dpg.bind_item_theme(delete_button, "red_button_theme")

    def create_and_show_delete_obj_window(self, sender, app_data, user_data):
        obj = user_data
        with dpg.window(
            modal=True,
            no_move=True,
            no_title_bar=True,
            popup=True,
            autosize=True,
        ) as confirm_window:

            def delete():
                self.delete_node(obj)
                dpg.delete_item(confirm_window)
                self.window_manager.reset_all()

            def cancel():
                dpg.delete_item(confirm_window)

            dpg.add_text(f"Are you sure you want to delete {obj.name}?")
            with dpg.group(horizontal=True):
                delete_button = dpg.add_button(label="Delete", callback=delete)
                dpg.bind_item_theme(delete_button, "red_button_theme")
                dpg.add_button(label="Cancel", callback=cancel)

    def create_track_output(self, sender, app_data, user_data):
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            address = user_data[2] if len(user_data) == 3 else 1
            result = self.execute_wrapper(f"create_output {track.id} {address}")
            if not result.success:
                return
            output_channel = result.payload
        else:  # restore
            output_channel = user_data[2]

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(
                tag=f"{output_channel.id}.dmx_address",
                width=75,
                default_value=output_channel.dmx_address,
                callback=self.update_channel_attr_callback,
                user_data=(output_channel, "dmx_address"),
            )
            dpg.add_input_text(
                tag=f"{output_channel.id}.name",
                default_value=output_channel.name,
                callback=self.update_channel_attr_callback,
                user_data=(output_channel, "name"),
                width=150,
            )
            dpg.add_button(
                label="X",
                callback=self.delete_track_output_callback,
                user_data=(track, output_channel),
            )

        # TODO: Consolidate with code that creates this during init
        with dpg.value_registry():
            tag = f"{output_channel.id}.value"
            add_func = {
                "float": dpg.add_float_value,
                "int": dpg.add_int_value,
                "bool": dpg.add_int_value,
                "any": dpg.add_float_value,
            }[output_channel.dtype]
            add_func(tag=tag)

    def create_track_output_group(self, sender, app_data, user_data):
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            starting_address = user_data[2]
            group_name = user_data[3]
            channel_names = user_data[4]
            result = self.execute_wrapper(
                f"create_output_group {track.id} {starting_address} {group_name} {','.join(channel_names)}"
            )
            if not result.success:
                return
            output_channel_group = result.payload
        else:  # restore
            output_channel_group = user_data[2]

        def update_channel_group_address(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_starting_address(app_data)

        def update_channel_group_name(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_name(app_data)

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel_group.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(
                tag=f"{output_channel_group.id}.dmx_address",
                width=75,
                default_value=output_channel_group.dmx_address,
                callback=update_channel_group_address,
                user_data=output_channel_group,
            )
            dpg.add_input_text(
                tag=f"{output_channel_group.id}.name",
                default_value=output_channel_group.name,
                callback=update_channel_group_name,
                user_data=output_channel_group,
                width=150,
            )
            dpg.add_button(
                label="X",
                callback=self.delete_track_output_group_callback,
                user_data=(track, output_channel_group),
            )
            # TODO: Consolidate with code that creates this during init
            with dpg.value_registry():
                for output_channel in output_channel_group.outputs:
                    tag = f"{output_channel.id}.value"
                    add_func = {
                        "float": dpg.add_float_value,
                        "int": dpg.add_int_value,
                        "bool": dpg.add_int_value,
                        "any": dpg.add_float_value,
                    }[output_channel.dtype]
                    add_func(tag=tag)

    def create_viewport_menu_bar(self):
        def save_callback(sender, app_data):
            file_path_name = app_data["file_path_name"]
            project_name = os.path.basename(file_path_name).replace(
                f".{PROJECT_EXTENSION}", ""
            )
            root_dir = os.path.dirname(file_path_name)
            project_folder_path = os.path.join(root_dir, project_name)
            project_file_path = os.path.join(
                project_folder_path, f"{project_name}.{PROJECT_EXTENSION}"
            )

            self.state.project_name = project_name
            self.state.project_folder_path = project_folder_path
            self.state.project_file_path = project_file_path
            self.save()

        def restore_callback(sender, app_data):
            self.open_project(app_data["file_path_name"])

        def restore_recent_callback(sender, app_data, user_data):
            self.open_project(user_data)

        def load_custom_fixture(sender, app_data):
            file_path_name = app_data["file_path_name"]
            loaded_fixtures = fixtures.parse_fixture(file_path_name)
            for fixture in loaded_fixtures:
                self.add_fixture(None, None, (self._active_track, fixture))

        with dpg.viewport_menu_bar():
            dpg.add_file_dialog(
                directory_selector=True,
                show=False,
                callback=save_callback,
                tag="save_file_dialog",
                cancel_callback=self.print_callback,
                width=700,
                height=400,
                modal=True,
                default_filename=self.state.project_name,
            )

            dpg.add_file_dialog(
                directory_selector=False,
                show=False,
                callback=restore_callback,
                tag="open_file_dialog",
                cancel_callback=self.print_callback,
                width=700,
                height=400,
                modal=True,
            )

            dpg.add_file_dialog(
                directory_selector=False,
                show=False,
                callback=load_custom_fixture,
                tag="open_fixture_dialog",
                cancel_callback=self.print_callback,
                width=700,
                height=400,
                modal=True,
            )

            for tag in ["open_file_dialog", "save_file_dialog"]:
                dpg.add_file_extension(
                    f".{PROJECT_EXTENSION}", color=[255, 255, 0, 255], parent=tag
                )

            dpg.add_file_extension(
                ".fixture", color=[0, 255, 255, 255], parent="open_fixture_dialog"
            )

            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open", callback=self.open_menu_callback)

                with dpg.menu(label="Open Recent"):
                    for filepath in self.cache["recent"]:
                        dpg.add_menu_item(
                            label=os.path.basename(filepath),
                            callback=restore_recent_callback,
                            user_data=filepath,
                        )

                dpg.add_menu_item(label="Save", callback=self.save_menu_callback)
                dpg.add_menu_item(label="Save As", callback=self.save_as_menu_callback)

            #### Edit menu ####
            with dpg.menu(label="Edit"):
                # TODO: Implement
                dpg.add_menu_item(
                    label="Copy Clip",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.manage_trigger_window),
                )
                dpg.add_menu_item(
                    label="Paste Clip",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.manage_trigger_window),
                )
                dpg.add_menu_item(
                    label="Triggers",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.manage_trigger_window),
                )
                dpg.add_menu_item(
                    label="Python Modules",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.python_modules_window),
                )

            #### View menu ####
            with dpg.menu(label="View"):
                dpg.add_menu_item(
                    label="I/O",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow("io.gui.window"),
                )
                with dpg.menu(label="Debug"):
                    dpg.add_menu_item(
                        label="Inspector",
                        callback=self.action_callback,
                        user_data=gui.ShowWindow("inspector.gui.window"),
                    )
                    dpg.add_menu_item(
                        label="GlobalStorage",
                        callback=self.action_callback,
                        user_data=gui.ShowWindow(self.global_storage_debug_window),
                    )

            #### Performance menu ####
            with dpg.menu(label="Performance"):
                dpg.add_menu_item(
                    label="Clip Presets",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.clip_preset_window),
                )

                dpg.add_menu_item(
                    label="Multi Clip Presets",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.multi_clip_preset_window),
                )

                dpg.add_menu_item(
                    label="Sequences",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.sequences_window),
                )

            #### Transport section ####
            def add_separator():
                dpg.add_button(label="  |  ")
                dpg.bind_item_theme(dpg.last_item(), "code_editor.separator.theme")

            add_separator()
            dpg.add_button(
                label="Reset",
                callback=self.reset_time_callback,
            )
            dpg.bind_item_theme(dpg.last_item(), "reset_button.theme")

            dpg.add_button(label="Tap Tempo", callback=self.tap_tempo_callback)

            def update_tempo(sender, app_data):
                self.state.tempo = app_data

            dpg.add_input_float(
                label="BPM",
                default_value=self.state.tempo,
                on_enter=True,
                callback=update_tempo,
                width=45,
                tag="tempo",
                step=0,
            )
            add_separator()

            dpg.add_button(
                label="[Paused]",
                callback=self.toggle_play_callback,
                tag="play_button",
            )
            dpg.bind_item_theme("play_button", "transport.play_button.pause.theme")
            add_separator()

            def mode_change():
                self.state.mode = (
                    "edit" if self.state.mode == "performance" else "performance"
                )
                dpg.configure_item(
                    "mode_button",
                    label="Mode: Edit"
                    if self.state.mode == "edit"
                    else "Mode: Performance",
                )
                self.window_manager.resize_all()
                self.window_manager.reset_all()

                if self.state.mode == "edit":
                    # Hide performance windows
                    windows = [
                        self.clip_preset_window,
                        self.multi_clip_preset_window,
                        self.clip_automation_presets_window,
                    ]
                else:
                    # Hide edit windows
                    windows = [
                        self.code_window,
                        self.console_window,
                    ]
                for window in windows:
                    window.hide()

            dpg.add_button(
                label="Mode: Edit",
                callback=mode_change,
                tag="mode_button",
            )
            add_separator()

            dpg.add_button(
                label="Key Mode: Off",
                callback=self.keyboard_mode_change_callback,
                tag="key_mode_button",
            )

    #################
    ### Callbacks ###
    #################

    def action_callback(self, sender, app_data, user_data):
        """Callback form since we can't use GuiActions as callbacks directly sometimes."""
        self.action(user_data)

    # TODO: Turn into GuiAction
    def select_automation_callback(self, sender, app_data, user_data):
        input_channel, automation = user_data

        result = self.execute_wrapper(
            f"set_active_automation {input_channel.id} {automation.id}"
        )
        if result.success:
            self.reset_automation_plot(input_channel)
            gui.SelectInputChannel(
                {"clip": self._active_clip, "channel": input_channel}
            ).execute()

        # self.window_manager.resize_all()
        # self.window_manager.reset_all()

    def shift_points_callback(self, sender, app_data, user_data):
        if util.valid(self._active_input_channel.active_automation):
            self._active_input_channel.active_automation.shift_points(user_data)
        self.reset_automation_plot(self._active_input_channel)

    def set_quantize_callback(self, sender, app_data, user_data):
        self._quantize_amount = user_data
        self.reset_automation_plot(self._active_input_channel)

    def set_interpolation_callback(self, sender, app_data, user_data):
        if util.valid(self._active_input_channel.active_automation):
            self._active_input_channel.active_automation.set_interpolation(user_data)
        self.reset_automation_plot(self._active_input_channel)

    def double_automation_callback(self):
        if self._active_input_channel is None:
            return

        automation = self._active_input_channel.active_automation
        if automation is None:
            return

        result = self.execute_wrapper(f"double_automation {automation.id}")
        if result.success:
            self.reset_automation_plot(self._active_input_channel)

    def duplicate_channel_preset_callback(self, sender, app_data, user_data):
        if user_data is not None:
            automation = user_data
        elif self._active_input_channel is not None:
            automation = self._active_input_channel.active_automation
        else:
            return

        if automation is None:
            return

        result = self.execute_wrapper(
            f"duplicate_channel_preset {self._active_input_channel.id} {automation.id}"
        )
        if result.success:
            automation = result.payload
            self.add_automation_tab(self._active_input_channel, automation)
            self.select_automation_callback(
                None, None, (self._active_input_channel, automation)
            )

    def delete_automation_callback(self, sender, app_data, user_data):
        input_channel, automation = user_data

        def get_valid_automations(input_channel):
            return [a for a in input_channel.automations if not a.deleted]

        if len(get_valid_automations(input_channel)) <= 1:
            return

        result = self.execute_wrapper(f"delete {automation.id}")

        if result.success:
            dpg.delete_item(get_preset_sub_menu_tag(automation))

        if input_channel.active_automation == automation:
            input_channel.set_active_automation(get_valid_automations(input_channel)[0])
            self.reset_automation_plot(input_channel)

    def add_preset_callback(self, sender, app_data, user_data):
        input_channel = user_data
        result = self.execute_wrapper(f"add_automation {input_channel.id}")
        if result.success:
            automation = result.payload
            self.add_automation_tab(input_channel, automation)
            self.reset_automation_plot(input_channel)

    def update_parameter_callback(self, sender, app_data, user_data):
        if app_data is not None:
            obj, parameter_index = user_data
            result = self.execute_wrapper(
                f"update_parameter {obj.id} {parameter_index} {app_data}"
            )
            if not result.success:
                raise RuntimeError("Failed to update parameter")
            if obj.parameters[parameter_index].name == "code":
                dpg.set_value(
                    f"{obj.parameters[parameter_index].id}.value",
                    obj.parameters[parameter_index].value.replace("[NEWLINE]", "\n"),
                )
            else:
                dpg.set_value(
                    f"{obj.parameters[parameter_index].id}.value",
                    obj.parameters[parameter_index].value,
                )
            return result.success

    def toggle_automation_mode_callback(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = (
            "manual" if input_channel.mode == "automation" else "automation"
        )

    def enable_recording_mode_callback(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "armed"

    def default_time_callback(self, sender, app_data, user_data):
        user_data.speed = 0

    def double_time_callback(self, sender, app_data, user_data):
        user_data.speed += 1

    def half_time_callback(self, sender, app_data, user_data):
        user_data.speed -= 1

    def update_parameter_by_name(self, obj, parameter_name, value):
        obj.get_parameter(parameter_name).value = value

    def reset_time_callback(self):
        self.state.play_time_start_s = time.time() - HUMAN_DELAY

    def tap_tempo_callback(self):
        self._tap_tempo_buffer.insert(0, time.time())
        self._tap_tempo_buffer.pop()
        dts = []
        for i in range(len(self._tap_tempo_buffer) - 1):
            dt = abs(self._tap_tempo_buffer[i] - self._tap_tempo_buffer[i + 1])
            if dt < 2:
                dts.append(dt)
        t = sum(dts) / len(dts)
        if t == 0:
            return
        self.state.tempo = round(60.0 / t, 2)
        dpg.set_value("tempo", self.state.tempo)

    @gui_lock
    def delete_track_output_callback(self, _, __, user_data):
        track, output_channel = user_data

        result = self.execute_wrapper(f"delete {output_channel.id}")
        if result.success:
            # Delete each Node from each clip's node editor
            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue

            # Remake the window
            self.track_properties_windows[track.id].reset()
        else:
            RuntimeError(f"Failed to delete: {output_channel.id}")

    @gui_lock
    def delete_track_output_group_callback(self, _, __, user_data):
        track, output_channel_group = user_data

        result = self.execute_wrapper(f"delete {output_channel_group.id}")
        if result.success:
            # Delete each Node from each clip's node editor
            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue

            # Remake the window
            self.track_properties_windows[track.id].reset()
        else:
            RuntimeError(f"Failed to delete: {output_channel_group.id}")

    def delete_node(self, obj):
        result = self.execute_wrapper(f"delete_node {obj.id}")
        if result.success:
            # TODO: Figure out what else needs to be deleted here.
            dpg.delete_item(get_properties_window_tag(obj))
        else:
            RuntimeError(f"Failed to delete: {obj.id}")

    def update_parameter_buffer_callback(self, sender, app_data, user_data):
        parameter, parameter_index = user_data
        if app_data is not None:
            self._properties_buffer["parameters"][parameter] = (
                parameter_index,
                app_data,
            )

    def update_attr_buffer_callback(self, sender, app_data, user_data):
        attr_name, tag = user_data
        if app_data:
            self._properties_buffer["attrs"][attr_name] = (app_data, tag)

    def save_properties_callback(self, sender, app_data, user_data):
        obj = user_data[0]
        # Parameters
        for parameter, (parameter_index, value) in self._properties_buffer.get(
            "parameters", {}
        ).items():
            self.update_parameter_callback(None, value, (obj, parameter_index))

        # Attributes
        # Validate attributes first. For now, the name is the only thing we need to check.
        for attribute_name, (value, tag) in self._properties_buffer.get(
            "attrs", {}
        ).items():
            if attribute_name == "name" and not re.match(VARIABLE_NAME_PATTERN, value):
                return

        for attribute_name, (value, tag) in self._properties_buffer.get(
            "attrs", {}
        ).items():
            setattr(obj, attribute_name, value)
            dpg.set_value(tag, value)

        dpg.configure_item(get_properties_window_tag(obj), show=False)

        self.clip_params_window.reset()

    def delete_clip_preset_callback(self, sender, app_data, user_data):
        preset = user_data
        result = self.execute_wrapper(f"delete {preset.id}")
        if result.success:
            dpg.delete_item(get_preset_menu_bar_tag(preset))

    def update_input_channel_ext_value_callback(self, sender, app_data, user_data):
        channel = user_data
        channel.ext_set(app_data)

    def update_channel_value_callback(self, sender, app_data, user_data):
        channel = user_data
        channel.set(app_data)

    def update_channel_attr_callback(self, sender, app_data, user_data):
        channel, attr = user_data
        setattr(channel, attr, app_data)

    @gui_lock
    def add_input_channel_callback(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "restore":
            _, input_channel = args
            input_type = input_channel.input_type
        else:  # create
            _, input_type = args

        if input_type in ["color", "button"]:
            self.create_standard_source_node(sender, app_data, user_data)
        else:
            self.create_automatable_source_node(sender, app_data, user_data)

    def play_clip_preset_callback(self, sender, app_data, user_data):
        preset = user_data
        preset.execute()
        if util.valid(self._active_input_channel):
            self.reset_automation_plot(self._active_input_channel)

        preset_button_tag = get_preset_button_tag(preset)
        dpg.bind_item_theme(preset_button_tag, "selected_preset2.theme")

        self.clip_automation_presets_window.reset()

    def play_clip_callback(self, sender, app_data, user_data):
        track, clip = user_data
        if self.ctrl:
            self.execute_wrapper(f"play_clip {track.id} {clip.id}")
        else:
            self.execute_wrapper(f"set_clip {track.id} {clip.id}")

    def toggle_clip_play_callback(self, sender, app_data, user_data):
        track, clip = user_data
        self.execute_wrapper(f"toggle_clip {track.id} {clip.id}")

    def toggle_play_callback(self):
        self.state.toggle_play()
        if self.state.playing:
            dpg.bind_item_theme("play_button", "transport.play_button.play.theme")
        else:
            dpg.bind_item_theme("play_button", "transport.play_button.pause.theme")

    # TODO: Turn into object
    def create_and_show_learn_midi_map_window_callback(
        self, sender, app_data, user_data
    ):
        obj, inout = user_data
        try:
            dpg.delete_item("midi_map_window")
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item("midi_map_window")

        def save(sender, app_data, user_data):
            obj = user_data
            if model.LAST_MIDI_MESSAGE is not None:
                device_name, message = model.LAST_MIDI_MESSAGE
                note_control, value = model.midi_value(message)
                if inout == "input":
                    self.update_parameter_by_name(obj, "device", device_name)
                    self.update_parameter_by_name(
                        obj, "id", f"{message.channel}/{note_control}"
                    )
                    result = self.execute_wrapper(f"midi_map {obj.id}")
                    if result.success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(
                            f"{device_parameter_id}.value",
                            obj.get_parameter("device").value,
                        )
                        dpg.set_value(
                            f"{id_parameter_id}.value", obj.get_parameter("id").value
                        )
                        dpg.delete_item("midi_map_window")
                    else:
                        raise RuntimeError("Failed to map midi")
                else:  # output
                    input_midi_device_name = device_name
                    while input_midi_device_name:
                        for i, output_device in model.MIDI_OUTPUT_DEVICES.items():
                            if output_device.device_name.startswith(
                                input_midi_device_name
                            ):
                                output_device.map_channel(
                                    message.channel, note_control, obj
                                )
                                logger.debug(
                                    f"Mapping {(message.channel, note_control)} to {output_device.device_name}"
                                )
                                dpg.delete_item("midi_map_window")
                                return
                        input_midi_device_name = input_midi_device_name[:-1]
                    logger.warning(
                        f"Failed to find corresponding output MIDI device for {input_midi_device_name}"
                    )

        dpg.set_value("last_midi_message", "")

        with dpg.window(
            tag="midi_map_window",
            modal=True,
            width=300,
            height=300,
            no_move=True,
            no_title_bar=True,
        ):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=save, user_data=obj)
                dpg.add_button(label="Cancel", callback=cancel, user_data=obj)

    def open_menu_callback(self):
        dpg.configure_item("open_file_dialog", show=True)

    def save_menu_callback(self):
        if self.state.project_file_path is None:
            dpg.configure_item("save_file_dialog", show=True)
        else:
            self.save()

    def save_as_menu_callback(self):
        dpg.configure_item("save_file_dialog", show=True)

    def mouse_move_callback(self, sender, app_data, user_data):
        cur_x, cur_y = app_data
        self.mouse_x = cur_x
        self.mouse_y = cur_y

        # Relative to window
        cur_x, cur_y = dpg.get_mouse_pos()
        self.mouse_drag_x = cur_x - self.mouse_click_x
        self.mouse_drag_y = cur_y - self.mouse_click_y

    def mouse_click_callback(self, sender, app_data, user_data):
        # TODO: separate click by relative and non-relative positions
        # Automation window wants relative
        # Node Editor wants non relative
        self.mouse_click_x, self.mouse_click_y = self.mouse_x, self.mouse_y

        if app_data == 0:
            if self._active_clip is not None:
                tag = get_node_window_tag(self._active_clip)

    def mouse_double_click_callback(self, sender, app_data, user_data):
        window_tag = dpg.get_item_alias(dpg.get_item_parent(dpg.get_active_window()))

        if app_data == 0:
            # Left double click on the automation plot
            if window_tag is not None and self.mouse_inside_window(window_tag):
                plot_mouse_pos = dpg.get_plot_mouse_pos()
                automation = self._active_input_channel.active_automation
                first_point, last_point = automation.points[0], automation.points[-1]
                for point in automation.points:
                    if point.deleted:
                        continue
                    x, y = point.x, point.y
                    x_axis_limits_tag = (
                        f"{self._active_input_channel.id}.plot.x_axis_limits"
                    )
                    y_axis_limits_tag = (
                        f"{self._active_input_channel.id}.plot.y_axis_limits"
                    )

                    # Clicked on a point, try to delete it.
                    if (
                        util.norm_distance(
                            (x, y),
                            plot_mouse_pos,
                            dpg.get_axis_limits(x_axis_limits_tag),
                            dpg.get_axis_limits(y_axis_limits_tag),
                        )
                        <= 0.015
                    ):
                        if point == first_point or point == last_point:
                            return
                        result = self.execute_wrapper(
                            f"delete_automation_point {automation.id} {point.id}"
                        )
                        if result.success:
                            dpg.delete_item(f"{point.id}.gui.point")
                            return
                        else:
                            raise RuntimeError("Failed to delete automation point")

                point = self._quantize_point(
                    *plot_mouse_pos,
                    self._active_input_channel.dtype,
                    automation.length,
                    quantize_x=False,
                )
                result = self.execute_wrapper(
                    f"add_automation_point {automation.id} {point[0]},{point[1]}"
                )
                if result.success:
                    self.reset_automation_plot(self._active_input_channel)

    @gui_lock
    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)

        #if key_n in [18]:
        #    self.keyboard_mode_change_callback(None, None, None)

        if self.keyboard_mode:
            return

        if key == " " and self.shift:
            self.toggle_play_callback()
        elif key_n in [120]:
            if self._active_input_channel is not None:
                self.enable_recording_mode_callback(
                    None, None, self._active_input_channel
                )
        elif key_n in [9]:  # tab
            pass
        elif key in ["C"]:
            if self.ctrl:
                pass
        elif key in ["O"]:
            if self.ctrl:
                self.open_menu_callback()
        elif key in ["I"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_channel_callback(
                        None, None, ("create", (self._active_clip, "int"), False)
                    )
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_channel_callback(
                        None, None, ("create", (self._active_clip, "bool"), False)
                    )
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_channel_callback(
                        None, None, ("create", (self._active_clip, "float"), False)
                    )
        elif key in ["T"]:
            if self.state.mode == "performance":
                self.tap_tempo_callback()
        elif key in ["V"]:
            if self.ctrl:
                self.paste_selected()
        elif key in ["R"]:
            if self.state.mode == "performance":
                self.reset_time_callback()
        elif key in ["N"]:
            if self.ctrl:
                for track_i, track in enumerate(self.state.tracks):
                    if track == self._active_track:
                        for clip_i, clip in enumerate(track.clips):
                            if clip is None:
                                self.action(
                                    gui.CreateNewClip(
                                        {
                                            "track_i": track_i,
                                            "clip_i": clip_i,
                                            "action": "create",
                                        }
                                    )
                                )
                                return
        elif key in ["S"]:
            if self.shift and self.ctrl:
                self.save_as_menu_callback()
            elif self.ctrl:
                self.save_menu_callback()
        elif key in ["Z"]:
            if self.ctrl:
                self.undo_action()

    def key_down_callback(self, sender, app_data, user_data):
        key, duration = app_data

        if self.keyboard_mode:
            self.trigger_keys([key], pressed=True)
            return

        if 17 == key:
            self.ctrl = True
        if 16 == key:
            self.shift = True

    def key_release_callback(self, sender, app_data, user_data):
        if not isinstance(app_data, int):
            return
        key_n = app_data
        chr(key_n)

        if self.keyboard_mode:
            self.trigger_keys([key_n], pressed=False)
            return

        if key_n == 17:
            self.ctrl = False
        if key_n == 16:
            self.shift = False

    def keyboard_mode_change_callback(self, sender, app_data, user_data):
        self.keyboard_mode = not self.keyboard_mode
        dpg.configure_item(
            "key_mode_button",
            label="Key Mode: On" if self.keyboard_mode else "Key Mode: Off",
        )

    def print_callback(self, sender, app_data, user_data):
        print(sender)
        print(app_data)
        print(user_data)

    def update_automation_point_callback(self, sender, app_data, user_data):
        """Callback when a draggable point it moved."""
        automation, point = user_data

        x, y, *_ = dpg.get_value(sender)

        # First and last point must maintain x position
        first_point, last_point = automation.points[0], automation.points[-1]
        if point == first_point or point == last_point:
            x = point.x
            x, y = self._quantize_point(x, y, automation.dtype, automation.length)
        else:
            # Other points must stay in between nearest points
            index = automation.points.index(point)
            left_point, right_point = (
                automation.points[index - 1],
                automation.points[index + 1],
            )
            if x < left_point.x:
                x = left_point.x
            if x > right_point.x:
                x = right_point.x
            x, y = self._quantize_point(
                x, y, automation.dtype, automation.length, quantize_x=True
            )

        result = self.execute_wrapper(
            f"update_automation_point {automation.id} {point.id} {x},{y}"
        )
        if not result.success:
            raise RuntimeError("Failed to update automation point")

        dpg.set_value(sender, (x, y))

    #############
    ### Other ###
    #############

    def trigger_keys(self, keys, pressed):
        for key in keys:
            letter = chr(key)
            channel = self.state.channel_from_key(letter.upper())
            if not channel:
                continue

            # Only support AutomatableSourceNode for now
            if not isinstance(channel, model.AutomatableSourceNode):
                continue

            channel.ext_set(
                channel.max_parameter.value if pressed else channel.min_parameter.value
            )

    def _quantize_point(self, x, y, dtype, length, quantize_x=False):
        x2 = x
        y2 = y

        if dtype == "bool":
            y2 = int(y > 0.5)
        elif dtype == "int":
            y2 = int(y)

        if self._quantize_amount is not None and quantize_x:
            x2 /= self._quantize_amount
            x2 = round(x2)
            x2 *= self._quantize_amount
            x2 = min(length, max(0, x2))

        return x2, y2

    def save(self):
        self.state.log.append("Saving project.")
        self.save_code()

        # Deprecated
        gui_data = self.gui_state.copy()
        gui_data.update(
            {
                "node_positions": {},
            }
        )

        data = {"state": self.state.serialize(), "gui": gui_data}

        with open(self.state.project_file_path, "w") as f:
            f.write(json.dumps(data, indent=4, sort_keys=False))

        dpg.set_viewport_title(f"CodeDMX [{self.state.project_name}]")

    def save_code(self):
        if not os.path.exists(self.state.project_folder_path):
            os.mkdir(self.state.project_folder_path)
            os.mkdir(os.path.join(self.state.project_folder_path, "code"))

        for track in self.state.tracks:
            for clip in track.clips:
                if util.valid(clip):
                    clip.init_code.save(dpg.get_value(get_code_window_tag(clip) + ".init.text"))
                    clip.main_code.save(dpg.get_value(get_code_window_tag(clip) + ".main.text"))
                    clip.reload_code()

    def restore_gui_state(self):
        for inout in ["inputs", "outputs"]:
            for i, args in enumerate(self.gui_state["io_args"][inout]):
                if not util.valid(args):
                    continue
                dpg.set_value(f"io.{inout}.table.{i}.arg", args)

        for inout in ["inputs", "outputs"]:
            for i, io_type in enumerate(self.gui_state["io_types"][inout]):
                if not util.valid(io_type):
                    continue
                io_type_class = model.IO_TYPES[io_type]
                dpg.configure_item(
                    f"io.{inout}.table.{i}.type", label=io_type_class.nice_title
                )

    def save_last_active_clip(self):
        if util.valid(self._active_track) and util.valid(self._active_clip):
            self.gui_state["track_last_active_clip"][
                self._active_track.id
            ] = self._active_clip.id

    def deserialize(self, data, project_file_path):
        self.state.deserialize(data["state"], project_file_path)
        self.gui_state = data["gui"]

    def open_project(self, filepath):
        if filepath in self.cache["recent"]:
            self.cache["recent"].remove(filepath)
        self.cache["recent"].insert(0, filepath)
        with open(self.cache["path"], "w") as f:
            json.dump(self.cache, f)

        new_cmd = ["python"] + sys.argv + ["--project", filepath]
        subprocess.Popen(new_cmd)

        dpg.stop_dearpygui()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CodeDMX [BETA]")
    parser.add_argument(
        # "--project", default="C:\\Users\\marcd\\Desktop\\Code\\nodedmx\\projects\\transcedent5-v3\\transcedent5-v3.ndmx", dest="project_file_path", help="Project file path."
        "--project",
        default=None,
        dest="project_file_path",
        help="Project file path.",
    )

    parser.add_argument(
        "--cache", default=".cache", dest="cache_file_path", help="Cached data."
    )

    parser.add_argument(
        "--profile", default=False, dest="profile", help="Enable profiling."
    )

    parser.add_argument(
        "--debug", default=True, dest="debug", help="Enable debug mode."
    )

    args = parser.parse_args()

    app = Application(args.debug)
    gui.set_app(app)

    cache = {"recent": []}
    try:
        if args.cache_file_path:
            if os.path.exists(args.cache_file_path):
                with open(args.cache_file_path, "r") as f:
                    cache = json.load(f)
    except Exception as e:
        logger.warning(e)
    finally:
        cache["path"] = os.path.abspath(args.cache_file_path)
        app.cache = cache

    if args.project_file_path:
        logging.debug("Opening %s", args.project_file_path)
        with open(args.project_file_path, "r") as f:
            data = json.load(f)
            app.deserialize(data, args.project_file_path)

    if args.profile:
        with Profile() as profile:
            app.run()
            Stats(profile).strip_dirs().sort_stats(SortKey.CALLS).print_stats()
    else:
        app.run()

    logging.info("Exiting.")
