"""
TODO:
    * Copy/paste bugs
    * Convert remaining windows to objects
    * Sequence editing, deleting, reordering
    * Preset editing
    * README
    * Install instructions
    * Documentation
    * Create new track and scene feature
    * OSC remote control of clips/preeets
    * Fix code view at init

"""
import dearpygui.dearpygui as dpg
import math
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
NODE_EXTENSION = "ndmxc"
VARIABLE_NAME_PATTERN = r"[a-zA-Z_][a-zA-Z\d_]*$"
HUMAN_DELAY = 0.125

DEFAULT_SEQUENCE_DURATION = 4  # beats


def get_node_editor_tag(clip):
    return f"{clip.id}.gui.node_window.node_editor"


def get_output_configuration_window_tag(track):
    return f"{track.id}.gui.output_configuration_window"


def get_io_matrix_window_tag(clip):
    return f"{clip.id}.gui.io_matrix_window"


def get_source_node_window_tag(input_channel, is_id=False):
    return f"{input_channel if is_id else input_channel.id}.gui.source_node_window"


def get_properties_window_tag(obj):
    return f"{obj.id}.gui.properties_window"


def get_plot_tag(input_channel):
    return f"{input_channel.id}.plot"


def get_node_tag(clip, obj):
    return f"{get_node_editor_tag(clip)}.{obj.id}.node"


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


class Application:
    def __init__(self, debug):
        # Debug flag.
        self.debug = debug

        # Model and state logic
        self.state = model.ProgramState()

        # State of GUI elements.
        self.gui_state = {
            # Position of the nodes in the node editor.
            "node_positions": {},
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

        # Position of the mouse last time it was right clicked.
        self.mouse_clickr_x, self.mouse_clickr_y = 0, 0

        # Current code view mode.
        self.code_view = gui.GLOBAL_VIEW

        # Whether the node editor window is in focus.
        self.node_editor_window_is_focused = False

        # Whether keyboard mode is enabled.
        self.keyboard_mode = False

        self._active_track = None
        self._active_clip = None
        self._active_clip_slot = None
        self._active_output_channel = None
        self._active_input_channel = None

        self._last_selected_preset_button_tag = None
        self._last_selected_preset_theme_tag = None

        self._properties_buffer = defaultdict(dict)
        self._clip_preset_buffer = {}
        self._new_sequence_buffer = {}
        self._new_sequence_duration = {}
        self._n_sequence_rows = 0

        self._tap_tempo_buffer = [0, 0, 0, 0]
        self._quantize_amount = None

        self.ctrl = False
        self.shift = False

        self.copy_buffer = []

        self.lock = RLock()
        self.past_actions = []

        self.performance_preset_window = None
        self.global_performance_preset_window = None
        self.save_new_global_performance_preset_window = None
        self.clip_automation_presets_window = None
        self.global_storage_debug_window = None
        self.io_window = None
        self.inspector_window = None
        self.rename_window = None
        self.track_properties_windows = {}
        self.console_window = None
        self.remap_midi_device_window = None

    def run(self):
        """Initialize then run the main loop."""
        self.initialize()
        self.main_loop()

    def initialize(self):
        # Init main context.
        logging.debug("Create dearpygui context")
        dpg.create_context()

        # Global Variables
        logging.debug("Initializing global value registry")
        with dpg.value_registry():
            dpg.add_string_value(default_value="", tag="last_midi_message")
            dpg.add_int_value(default_value=0, tag="n_global_storage_elements")

        logging.debug("Initializing window size")
        self.update_window_size_info(gui.SCREEN_WIDTH, gui.SCREEN_HEIGHT)
        dpg.set_viewport_resize_callback(callback=self.resize_windows_callback)

        # Create main viewport.
        logging.debug("Create viewport")
        dpg.create_viewport(
            title=f"NodeDMX [{self.state.project_name}] *",
            width=gui.SCREEN_WIDTH,
            height=gui.SCREEN_HEIGHT,
            x_pos=50,
            y_pos=0,
        )

        #### Init Themes ####
        logging.debug("Creating themes")
        self.create_themes()

        #### Create Console ####
        logging.debug("Creating console window")
        self.console_window = gui.ConsoleWindow(self.state)

        logging.debug("Creating performance windows")
        self.performance_preset_window = gui.PerformancePresetWindow(self.state)
        self.save_new_global_performance_preset_window = (
            gui.SaveNewGlobalPerformancePresetWindow(self.state)
        )
        self.global_performance_preset_window = gui.GlobalPerformancePresetWindow(
            self.state
        )
        self.clip_automation_presets_window = gui.ClipAutomationPresetWindow(self.state)
        self.add_new_trigger_window = gui.AddNewTriggerWindow(self.state)
        self.manage_trigger_window = gui.ManageTriggerWindow(self.state)
        self.remap_midi_device_window = gui.RemapMidiDeviceWindow(self.state)

        #### Help Window ####
        self.help_window = gui.HelpWindow(self.state)

        #### Global Storage Debug Window ####
        self.global_storage_debug_window = gui.GlobalStorageDebugWindow(self.state)

        #### Create Code Editor Windows ####
        logging.debug("Creating code editor windows")
        # Create the global code editor and individual Track code editor windows.
        # Clip editor windows will be created when Clips are created.
        self.create_code_editor_window(self.state)
        for track in self.state.tracks:
            self.create_code_editor_window(track)

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

        self.restore_gui_state()

        dpg.setup_dearpygui()
        dpg.show_viewport()

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
                    self.update_gui_from_state()
                dpg.render_dearpygui_frame()
            dpg.destroy_context()
        except Exception as e:
            logger.warning(e)
            if self.debug:
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
        dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}] *")
        result = self.state.execute(command)
        return result

    def action(self, action: gui.GuiAction):
        # Gui actions can modify state. Use the lock to make sure
        # the enture state is updated before the GUI tries to render.
        with self.lock:
            action.execute()
        self.past_actions.append(action)

    def update_window_size_info(self, new_width, new_height):
        gui.SCREEN_WIDTH = new_width
        gui.SCREEN_HEIGHT = new_height

        clip_window_pos = (0, 18)
        clip_window_size = (
            gui.CLIP_WINDOW_PERCENT[0] * new_width,
            gui.CLIP_WINDOW_PERCENT[1] * new_height,
        )

        code_window_pos = (clip_window_size[0], 18)
        code_window_size = (new_width - clip_window_size[0], clip_window_size[1])

        node_window_pos = (0, 18 + clip_window_size[1])
        node_window_size = (
            gui.NODE_WINDOW_PERCENT[0] * new_width,
            new_height - clip_window_size[1],
        )

        console_window_pos = (node_window_size[0], 18 + code_window_size[1])
        console_window_size = (
            new_width - node_window_size[0],
            new_height - code_window_size[1],
        )

        gui.WINDOW_INFO = {
            "clip_pos": clip_window_pos,
            "clip_size": clip_window_size,
            "code_pos": code_window_pos,
            "code_size": code_window_size,
            "console_pos": console_window_pos,
            "console_size": console_window_size,
            "node_pos": node_window_pos,
            "node_size": node_window_size,
        }

    def update_clip_window(self):
        for track_i, track in enumerate(self.state.tracks):
            for clip_i, clip in enumerate(track.clips):
                clip_color = [0, 0, 0, 100]

                if self._active_clip_slot == (track_i, clip_i):
                    clip_color[2] += 155

                if util.valid(clip) and clip == self._active_clip:
                    clip_color[2] += 255

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

    def add_clip_preset_to_menu(self, clip, preset, before=None):
        window_tag = get_node_window_tag(clip)
        menu_tag = f"{window_tag}.menu_bar"
        preset_menu_tag = f"{menu_tag}.preset_menu"
        preset_menu_bar = get_preset_menu_bar_tag(preset)
        menu_theme = get_channel_preset_theme(preset)

        def set_color(sender, app_data, user_data):
            if app_data is None:
                color = dpg.get_value(sender)
            else:
                color = [int(255 * v) for v in app_data]
            dpg.configure_item(f"{menu_theme}.text_color", value=color)
            dpg.configure_item(f"{menu_theme}.button_bg_color", value=color)
            self.gui_state["clip_preset_themes"][f"{menu_theme}.text_color"] = color
            self.gui_state["clip_preset_themes"][
                f"{menu_theme}.button_bg_color"
            ] = color

        def duplicate_clip_preset(sender, app_data, user_data):
            clip, preset = user_data
            result = self.execute_wrapper(
                f"duplicate_clip_preset {clip.id} {preset.id}"
            )
            if result.success:
                self.add_clip_preset_to_menu(clip, result.payload, before=preset)

        with dpg.menu(
            parent=preset_menu_tag,
            tag=preset_menu_bar,
            label=preset.name,
            drop_callback=self.print_callback,
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
                callback=self.create_and_show_save_presets_window,
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

        with dpg.theme(tag=menu_theme):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(
                    tag=f"{menu_theme}.text_color",
                    target=dpg.mvThemeCol_Text,
                    value=[255, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )
                dpg.add_theme_color(
                    tag=f"{menu_theme}.button_bg_color",
                    target=dpg.mvThemeCol_Border,
                    value=[255, 255, 255, 255],
                    category=dpg.mvThemeCat_Core,
                )
        dpg.bind_item_theme(preset_menu_bar, menu_theme)

    def create_automation_window(self, clip, input_channel):
        parent = get_source_node_window_tag(input_channel)
        with dpg.window(
            tag=parent,
            label="Automation Window",
            width=1120,
            height=520,
            pos=(799, 18),
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

    def create_code_editor_window(self, obj):
        code_window = gui.CodeWindow(self.state, obj)
        self.tags["hide_on_clip_selection"].append(code_window.tag)

    def create_source_node_window(self, clip, input_channel):
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
                label=f"Automation Window",
                width=width,
                height=height,
                pos=(799, 18),
                show=False,
                no_move=True,
                no_title_bar=True,
            ) as window:
                default_color = input_channel.get()
                node_theme = get_node_tag(clip, input_channel) + ".theme"
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
                dpg.bind_item_theme(get_node_tag(clip, input_channel), node_theme)
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
                label=f"Automation Window",
                width=width,
                height=height,
                pos=(799, 18),
                show=False,
                no_move=True,
                no_title_bar=True,
            ) as window:
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
        series_tag = f"{input_channel.id}.series"
        plot_tag = get_plot_tag(input_channel)
        x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
        y_axis_limits_tag = f"{plot_tag}.y_axis_limits"

        dpg.configure_item(plot_tag, label=input_channel.active_automation.name)
        dpg.set_axis_limits(
            x_axis_limits_tag,
            -gui.AXIS_MARGIN,
            input_channel.active_automation.length + gui.AXIS_MARGIN,
        )

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

    def create_and_show_reorder_window(self, sender, app_data, user_data):
        container, parent_tag, get_obj_tag_func = user_data

        try:
            pos = dpg.get_item_pos("reorder.gui.window")
            dpg.delete_item("reorder.gui.window")
        except Exception as e:
            pos = (100, 100)

        def swap(container, i, j):
            pass

        def move(sender2, app_data2, user_data2):
            container, current_i, new_i = user_data2

            if new_i < 0 or new_i >= len(container):
                return

            i1 = min(current_i, new_i)
            i2 = max(current_i, new_i)

            obj1 = container[i1]
            obj2 = container[i2]
            container[i1] = obj2
            container[i2] = obj1

            dpg.move_item(
                get_obj_tag_func(container[i1]),
                parent=parent_tag,
                before=get_obj_tag_func(container[i2]),
            )

            self.create_and_show_reorder_window(sender, app_data, user_data)

        with dpg.window(
            label=f"Reorder", width=800, height=800, pos=pos, tag="reorder.gui.window"
        ) as window:
            with dpg.table(tag="", header_row=False):
                dpg.add_table_column()
                dpg.add_table_column()
                for i, obj in enumerate(container):
                    with dpg.table_row():
                        dpg.add_text(default_value=obj.name)
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label=" - ",
                                callback=move,
                                user_data=(container, i, i - 1),
                            )
                            dpg.add_button(
                                label=" + ",
                                callback=move,
                                user_data=(container, i, i + 1),
                            )

    def create_and_show_track_sequences_window(self):
        try:
            dpg.delete_item("sequences.gui.window")
        except Exception as e:
            pass

        with dpg.window(
            label=f"Sequences",
            width=800,
            height=800,
            pos=(100, 100),
            tag="sequences.gui.window",
        ) as window:
            with dpg.table(tag="sequences.table"):
                for i, track in enumerate(self.state.tracks):
                    dpg.add_table_column(label=track.name)

                with dpg.table_row():
                    for track in self.state.tracks:
                        with dpg.group():
                            dpg.add_menu_item(
                                label="New Sequence",
                                callback=self.create_and_show_new_sequences_window,
                                user_data=(track, None),
                            )
                            dpg.add_menu_item(
                                label="Reorder",
                                callback=self.create_and_show_reorder_window,
                                user_data=(
                                    track.sequences,
                                    get_sequences_group_tag(track),
                                    get_sequence_button_tag,
                                ),
                            )

                def start_sequence(sender, app_data, user_data):
                    track, sequence = user_data
                    track.sequence = sequence
                    self.state.start()

                with dpg.table_row():
                    for track in self.state.tracks:
                        with dpg.group(tag=get_sequences_group_tag(track)):
                            for sequence in track.sequences:
                                dpg.add_button(
                                    tag=get_sequence_button_tag(sequence),
                                    label=sequence.name,
                                    callback=start_sequence,
                                    user_data=(track, sequence),
                                )
                                with dpg.popup(dpg.last_item()):
                                    dpg.add_menu_item(
                                        label="Edit",
                                        callback=self.create_and_show_new_sequences_window,
                                        user_data=(track, sequence),
                                    )

    def create_and_show_new_sequences_window(self, sender, app_data, user_data):
        track, sequence = user_data
        editing = util.valid(sequence)

        new_sequence_window = "new_sequence_window"
        try:
            dpg.delete_item(new_sequence_window)
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item(new_sequence_window)

        def save(sender, app_data, user_data):
            sequence_info = []
            for i in range(self._n_sequence_rows):
                seq_info = self._new_sequence_buffer.get(i)
                duration = self._new_sequence_duration.get(i)
                if seq_info and duration:
                    seq_info.append(duration)
                    sequence_info.append(seq_info)
                    self.state.log.append("Invalid sequence entry")

            if sequence_info:
                name = dpg.get_value("sequence.name")
                data = json.dumps(
                    {
                        "sequence_info": sequence_info,
                        "name": name,
                        "track": track.id,
                        "sequence_id": sequence.id if editing else None,
                    }
                )

                result = self.execute_wrapper(f"add_sequence {data}")
                if result.success:
                    dpg.configure_item(item=new_sequence_window, show=False)
                    self.create_and_show_track_sequences_window()
                    dpg.configure_item(item="sequences.gui.window", show=True)
                    dpg.focus_item("sequences.gui.window")
                    self._new_sequence_buffer.clear()
                    self._new_sequence_duration.clear()
                    self._n_sequence_rows = 0
                else:
                    self.state.log.append("Failed to add sequence")

        def preset_selected(sender, app_data, user_data):
            i, title, clip, preset = user_data
            self._new_sequence_buffer[int(i)] = [clip.id, preset.id]
            dpg.configure_item(
                item=f"{new_sequence_window}.menu_bar.{i}.title", label=title
            )

        def set_duration(sender, app_data, user_data):
            duration = app_data
            i = user_data
            self._new_sequence_duration[int(i)] = duration
            dpg.set_value(f"{new_sequence_window}.{i}.duration", duration)

        self._n_sequence_rows = 0
        with dpg.window(
            label="New Sequence",
            tag=new_sequence_window,
            no_title_bar=True,
            modal=True,
            width=500,
            height=500,
            no_move=True,
            pos=(500, 500),
        ):

            def add_rows(sender, app_data, callback):
                final_n_rows = app_data

                if self._n_sequence_rows < final_n_rows:
                    for i in range(self._n_sequence_rows, final_n_rows):
                        with dpg.table_row(
                            parent=f"{new_sequence_window}.table",
                            before=f"{new_sequence_window}.table.save_cancel_row",
                        ):
                            self._new_sequence_duration[
                                int(i)
                            ] = DEFAULT_SEQUENCE_DURATION

                            with dpg.menu(
                                tag=f"{new_sequence_window}.menu_bar.{i}.title",
                                label="Select Clip Preset",
                            ):
                                for clip in track.clips:
                                    if not util.valid(clip):
                                        continue
                                    for preset in clip.presets:
                                        title = f"{clip.name}: {preset.name}"
                                        dpg.add_menu_item(
                                            label=title,
                                            callback=preset_selected,
                                            user_data=(i, title, clip, preset),
                                        )

                            dpg.add_input_int(
                                tag=f"{new_sequence_window}.{i}.duration",
                                default_value=DEFAULT_SEQUENCE_DURATION,
                                callback=set_duration,
                                user_data=i,
                            )

                        self._n_sequence_rows += 1

            with dpg.table(
                tag=f"{new_sequence_window}.table",
                header_row=False,
                policy=dpg.mvTable_SizingStretchProp,
            ):
                dpg.add_table_column(width=100)
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Sequence Name")
                    dpg.add_input_text(
                        tag="sequence.name",
                        default_value=sequence.name if editing else "",
                    )

                with dpg.table_row():
                    dpg.add_text(default_value="Num. Entries ")
                    dpg.add_input_int(
                        default_value=len(sequence.sequence_info) if editing else 1,
                        callback=add_rows,
                        on_enter=True,
                    )

                # Empty Row
                with dpg.table_row():
                    dpg.add_text()
                    dpg.add_text()

                with dpg.table_row():
                    dpg.add_text(default_value="Clip Preset")
                    dpg.add_text(default_value="Duration (Beats)")

                if editing:
                    add_rows(None, len(sequence.sequence_info), None)
                    for i, si in enumerate(sequence.sequence_info):
                        clip, preset, duration = si
                        title = f"{clip.name}: {preset.name}"
                        preset_selected(None, None, (i, title, clip, preset))
                        set_duration(None, duration, i)
                else:
                    # Start with 1 row
                    add_rows(None, 1, None)

                with dpg.table_row(tag=f"{new_sequence_window}.table.save_cancel_row"):
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)

    def create_themes(self):
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
                    dpg.mvThemeCol_Button, (0, 255, 0, 60), category=dpg.mvThemeCat_Core
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
                    value=[255, 255, 0, 255],
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

    def _delete_node_gui(self, node_tag, obj_id):
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        obj = self.state.get_obj(obj_id)
        channels_to_delete = []
        if isinstance(obj, model.SourceNode):
            # Input Nodes (also need to delete automation window)
            channels_to_delete = [obj]
            source_node_window_tag = get_source_node_window_tag(obj_id, is_id=True)
            if source_node_window_tag in all_aliases:
                dpg.delete_item(source_node_window_tag)
                self.tags["hide_on_clip_selection"].remove(source_node_window_tag)

        # Finally, delete the node from the Node Editor
        dpg.delete_item(node_tag)

    def copy_selected(self):
        new_copy_buffer = []

        # Copying from Node Editor
        if self._active_clip is not None:
            node_editor_tag = get_node_editor_tag(self._active_clip)
            for item in dpg.get_selected_nodes(node_editor_tag):
                alias = dpg.get_item_alias(item)
                item_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                obj = self.state.get_obj(item_id)
                if isinstance(obj, (model.DmxOutputGroup, model.DmxOutput)):
                    continue
                new_copy_buffer.append(obj)

        elif window_tag_alias == "clip.gui.window":
            if self._active_clip is not None:
                new_copy_buffer.append(self._active_clip)

        if new_copy_buffer:
            self.copy_buffer = new_copy_buffer

    def paste_selected(self):
        if self._active_clip is not None:
            for obj in self.copy_buffer:
                if isinstance(obj, model.SourceNode):
                    result = self.execute_wrapper(
                        f"duplicate_node {self._active_clip.id} {obj.id}"
                    )
                    if result.success:
                        new_input_channel = result.payload
                        self.add_source_node_callback(
                            sender=None,
                            app_data=None,
                            user_data=(
                                "restore",
                                (self._active_clip, new_input_channel),
                                True,
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

        for i, old_channel in enumerate(clip.inputs):
            self.copy_node_position(clip, old_channel, new_clip, new_clip.inputs[i])

        for i, old_channel in enumerate(clip.outputs):
            self.copy_node_position(clip, old_channel, new_clip, new_clip.outputs[i])

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

    def get_all_valid_track_output_channels(self, clip):
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
            "play_button", label="[Playing]" if self.state.playing else "[Paused]"
        )

        # Cache the active clip, since it can change while this function is running
        c_active_clip = self._active_clip
        c_active_input_channel = self._active_input_channel

        if util.valid(c_active_clip):
            # This is only setting the GUI value, so we only need to update the active clip.
            for dst_channel in self.get_all_valid_dst_channels(c_active_clip):
                if hasattr(dst_channel, "dmx_address"):
                    tag = get_output_node_value_tag(c_active_clip, dst_channel)
                else:
                    tag = f"{dst_channel.id}.value"
                dpg.set_value(tag, dst_channel.get())

            # This is only setting the GUI value, so we only need to update the active clip.
            for src_channel in self.get_all_valid_node_src_channels(c_active_clip):
                tag = f"{src_channel.id}.value"
                dpg.set_value(tag, src_channel.get())
                if src_channel.input_type == "color":
                    # Update the node's color
                    node_theme = get_node_tag(c_active_clip, src_channel) + ".theme"
                    rgb = src_channel.get()
                    dpg.configure_item(f"{node_theme}.color1", value=rgb)
                    dpg.configure_item(f"{node_theme}.color2", value=rgb)
                    dpg.configure_item(f"{node_theme}.color3", value=rgb)

        # Update automation points
        if (
            util.valid(c_active_input_channel)
            and isinstance(c_active_input_channel, model.AutomatableSourceNode)
            and util.valid(c_active_input_channel.active_automation)
        ):
            automation = c_active_input_channel.active_automation
            xs = np.arange(0, c_active_input_channel.active_automation.length, 0.01)
            ys = c_active_input_channel.active_automation.f(xs).astype(
                float if c_active_input_channel.dtype == "float" else int
            )
            dpg.configure_item(
                f"{c_active_input_channel.id}.series",
                x=xs,
                y=ys,
            )

        # Update Inspector
        if util.valid(self._active_output_channel):
            self.inspector_window.update()

        # Update GlobalStorageDebugWindow
        if len(model.GlobalStorage.items()) != dpg.get_value(
            "n_global_storage_elements"
        ):
            self.global_storage_debug_window.reset(
                show=self.global_storage_debug_window.shown
            )

        for i, (name, obj) in enumerate(model.GlobalStorage.items()):
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
        window_x, window_y = dpg.get_item_pos(window_tag)
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

    # TODO: Turn into class
    def create_and_show_save_presets_window(self, sender, app_data, user_data):
        clip, preset = user_data

        # If a valid preset was passed in, this means we're editing it.
        # Otherwise, we're creating a new one.
        editing = util.valid(preset)

        window_tag = get_node_window_tag(clip)
        preset_window_tag = "preset_window"
        try:
            dpg.delete_item(preset_window_tag)
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item(preset_window_tag)

        def save(sender, app_data, user_data):
            presets = []
            for i, channel in enumerate(clip.inputs):
                include = dpg.get_value(f"preset.{i}.include")
                if include:
                    presets.append(
                        {
                            "channel": channel.id,
                            "automation": self._clip_preset_buffer[channel.id],
                            "speed": None
                            if channel.is_constant
                            else dpg.get_value(f"preset.{i}.speed"),
                        }
                    )

            if presets:
                data = {
                    "clip": clip.id,
                    "name": dpg.get_value("preset.name"),
                    "preset_info": presets,
                    "preset_id": preset.id if editing else None,
                }

                result = self.execute_wrapper(f"add_clip_preset {json.dumps(data)}")
                if result.success:
                    if editing:
                        dpg.configure_item(
                            get_preset_menu_bar_tag(preset), label=preset.name
                        )
                    else:
                        new_preset = result.payload
                        self.add_clip_preset_to_menu(clip, new_preset)

                    self._clip_preset_buffer.clear()
                    dpg.delete_item("preset_window")
                else:
                    logger.warning("Failed to add clip preset")

        def set_automation(sender, app_data, user_data):
            channel, automation = user_data
            self._clip_preset_buffer[channel.id] = automation.id
            dpg.configure_item(f"{channel.id}.select_preset_bar", label=automation.name)

        def set_value(sender, app_data, user_data):
            channel = user_data
            value = app_data
            self._clip_preset_buffer[channel.id] = value

        with dpg.window(
            tag=preset_window_tag, modal=True, width=600, height=500, no_move=True
        ):
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Preset Name")
                    dpg.add_input_text(
                        tag="preset.name", default_value=preset.name if editing else ""
                    )

                with dpg.table_row():
                    dpg.add_text(default_value="Channel")
                    dpg.add_text(default_value="Preset/Value")
                    dpg.add_text(default_value="Speed (2^n)")
                    dpg.add_text(default_value="Include")

                preset_data = {}

                if editing:
                    preset_data = {
                        channel.id: (automation, speed)
                        for channel, automation, speed in preset.presets
                    }

                for i, channel in enumerate(clip.inputs):
                    with dpg.table_row():
                        # Name column
                        dpg.add_text(default_value=channel.name)

                        # Preset/Value column
                        if channel.is_constant:
                            kwargs = {}
                            if channel.dtype == "any":
                                add_func = dpg.add_input_text
                            elif channel.size == 1:
                                add_func = (
                                    dpg.add_input_float
                                    if channel.dtype == "float"
                                    else dpg.add_input_int
                                )
                            else:
                                add_func = dpg.add_drag_floatx
                                kwargs["size"] = channel.size
                            add_func(
                                width=90,
                                default_value=channel.get(),
                                callback=set_value,
                                user_data=channel,
                                **kwargs,
                            )

                        else:
                            with dpg.menu(
                                tag=f"{channel.id}.select_preset_bar",
                                label="Select Preset",
                            ):
                                for automation in channel.automations:
                                    dpg.add_menu_item(
                                        label=automation.name,
                                        callback=set_automation,
                                        user_data=(channel, automation),
                                    )

                        if channel.id in preset_data:
                            if channel.is_constant:
                                set_value(None, preset_data[channel.id][0], channel)
                            else:
                                set_automation(
                                    None, None, (channel, preset_data[channel.id][0])
                                )
                        else:
                            if channel.is_constant:
                                set_value(None, channel.get(), channel)
                            elif util.valid(channel.active_automation):
                                set_automation(
                                    None, None, (channel, channel.active_automation)
                                )

                        # Speed column
                        if channel.is_constant:
                            dpg.add_text(label="")
                        else:
                            dpg.add_input_int(
                                tag=f"preset.{i}.speed",
                                default_value=preset_data[channel.id][1]
                                if channel.id in preset_data
                                else channel.speed,
                            )

                        # Include column
                        dpg.add_checkbox(
                            tag=f"preset.{i}.include",
                            default_value=channel.id in preset_data,
                        )

                with dpg.table_row():
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)

    def create_standard_source_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
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

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y
        with dpg.node(
            label=input_channel.name,
            tag=node_tag,
            parent=node_editor_tag,
            pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0),
        ):
            self.create_node_popup_menu(node_tag, clip, input_channel)
            parameters = getattr(input_channel, "parameters", [])
            for parameter_index, parameter in enumerate(parameters):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    if parameter.dtype == "bool":
                        dpg.add_checkbox(
                            label=parameter.name,
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter_callback,
                            user_data=(input_channel, parameter_index),
                            default_value=parameter.value,
                        )
                    else:
                        dpg.add_input_text(
                            label=parameter.name,
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter_callback,
                            user_data=(input_channel, parameter_index),
                            width=70,
                            default_value=parameter.value
                            if parameter.value is not None
                            else "",
                            on_enter=True,
                        )

            with dpg.node_attribute(
                tag=get_node_attribute_tag(clip, input_channel),
                attribute_type=dpg.mvNode_Attr_Static,
            ):
                kwargs = {}
                if input_channel.dtype == "any":
                    add_func = dpg.add_input_text
                elif input_channel.size == 1:
                    add_func = (
                        dpg.add_input_float
                        if input_channel.dtype == "float"
                        else dpg.add_input_int
                    )
                else:
                    add_func = dpg.add_drag_floatx
                    kwargs["size"] = input_channel.size

                add_func(
                    label="out",
                    tag=f"{input_channel.id}.value",
                    width=90,
                    on_enter=True,
                    default_value=input_channel.get(),
                    callback=self.update_input_channel_ext_value_callback,
                    user_data=input_channel,
                    **kwargs,
                )

            # Create Automation Editor
            self.create_source_node_window(
                clip,
                input_channel,
            )

            # When user clicks on the node, bring up the configuration window.
            def input_selected_callback(sender, app_data, user_data):
                # Show right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    clip, input_channel = user_data
                    gui.SelectInputNode(
                        {"clip": clip, "channel": input_channel}
                    ).execute()

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(
                    callback=input_selected_callback, user_data=(clip, input_channel)
                )
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

    def create_automatable_source_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
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

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y
        with dpg.node(
            label=input_channel.name,
            tag=node_tag,
            parent=node_editor_tag,
            pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0),
        ):
            self.create_node_popup_menu(node_tag, clip, input_channel)
            parameters = getattr(input_channel, "parameters", [])

            # Special Min/Max Parameters
            def update_min_max_value(sender, app_data, user_data):
                clip, input_channel, parameter_index, min_max = user_data
                self.update_parameter_callback(
                    None, app_data, (input_channel, parameter_index)
                )

                value = model.cast[input_channel.dtype](app_data)
                kwarg = {f"{min_max}_value": value}
                dpg.configure_item(f"{input_channel.id}.value", **kwarg)

                plot_tag = get_plot_tag(input_channel)
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.set_axis_limits(
                    y_axis_limits_tag,
                    input_channel.get_parameter("min").value,
                    input_channel.get_parameter("max").value,
                )
                self.reset_automation_plot(input_channel)

            for parameter_index, name in enumerate(["min", "max"]):
                parameter = input_channel.get_parameter(name)
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name,
                        tag=f"{parameter.id}.value",
                        callback=update_min_max_value,
                        user_data=(clip, input_channel, parameter_index, name),
                        width=70,
                        default_value=parameter.value
                        if parameter.value is not None
                        else "",
                        on_enter=True,
                        decimal=True,
                    )

            for parameter_index, parameter in enumerate(parameters):
                if parameter.name in ["min", "max"]:
                    continue
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name,
                        tag=f"{parameter.id}.value",
                        callback=self.update_parameter_callback,
                        user_data=(input_channel, parameter_index),
                        width=70,
                        default_value=parameter.value
                        if parameter.value is not None
                        else "",
                        on_enter=True,
                    )

            with dpg.node_attribute(
                tag=get_node_attribute_tag(clip, input_channel),
                attribute_type=dpg.mvNode_Attr_Static,
            ):
                # Input Knob
                add_func = (
                    dpg.add_drag_float
                    if input_channel.dtype == "float"
                    else dpg.add_drag_int
                )
                add_func(
                    label="out",
                    min_value=input_channel.get_parameter("min").value,
                    max_value=input_channel.get_parameter("max").value,
                    tag=f"{input_channel.id}.value",
                    width=75,
                    callback=self.update_input_channel_ext_value_callback,
                    user_data=input_channel,
                )

            # Create Automation Editor
            self.create_automation_window(
                clip,
                input_channel,
            )

            # When user clicks on the node, bring up the automation window.
            def input_selected_callback(sender, app_data, user_data):
                # Show right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    clip, input_channel = user_data
                    self._active_input_channel = input_channel
                    for other_input_channel in clip.inputs:
                        if other_input_channel.deleted:
                            continue
                        dpg.configure_item(
                            get_source_node_window_tag(other_input_channel), show=False
                        )
                    dpg.configure_item(
                        get_source_node_window_tag(self._active_input_channel),
                        show=True,
                    )
                    self.reset_automation_plot(input_channel)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(
                    callback=input_selected_callback, user_data=(clip, input_channel)
                )
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

    def create_output_node(self, clip, output_channel):
        # This is the id used when adding links.
        attr_tag = get_node_attribute_tag(clip, output_channel)

        if dpg.does_item_exist(attr_tag):
            return

        node_tag = get_node_tag(clip, output_channel)
        with dpg.node(label="Output", tag=node_tag, parent=get_node_editor_tag(clip)):
            self.create_node_popup_menu(node_tag, clip, output_channel)

            with dpg.node_attribute(
                tag=attr_tag, attribute_type=dpg.mvNode_Attr_Static
            ):
                dpg.add_input_int(
                    label="in",
                    tag=get_output_node_value_tag(clip, output_channel),
                    width=50,
                    readonly=True,
                    step=0,
                )

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(
                    label="ch.",
                    source=f"{output_channel.id}.dmx_address",
                    width=50,
                    readonly=True,
                    step=0,
                )

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text(
                    source=f"{output_channel.id}.name",
                    default_value=output_channel.name,
                )

            # When user clicks on the output node it will populate the inspector.
            def output_selected_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    self._active_output_channel = user_data

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(
                    callback=output_selected_callback, user_data=output_channel
                )
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

    def create_output_group_node(self, clip, output_channel_group):
        # This is the id used when adding links.
        node_tag = get_node_tag(clip, output_channel_group)
        if dpg.does_item_exist(node_tag):
            return

        with dpg.node(
            label=output_channel_group.name,
            tag=node_tag,
            parent=get_node_editor_tag(clip),
        ):
            self.create_node_popup_menu(node_tag, clip, output_channel_group)
            for i, output_channel in enumerate(output_channel_group.outputs):
                attr_tag = get_node_attribute_tag(clip, output_channel)
                with dpg.node_attribute(
                    tag=attr_tag, attribute_type=dpg.mvNode_Attr_Static
                ):
                    dpg.add_input_int(
                        label=output_channel.name.split(".")[-1]
                        + f" [{output_channel.dmx_address}]",
                        tag=get_output_node_value_tag(clip, output_channel),
                        width=50,
                        readonly=True,
                        step=0,
                    )

                # When user clicks on the output node it will populate the inspector.
                def output_selected_callback(sender, app_data, user_data):
                    # Right click menu
                    if app_data[0] == 1:
                        dpg.configure_item(f"{node_tag}.popup", show=True)
                    else:
                        self._active_output_channel_group = user_data

    def create_node_popup_menu(self, node_tag, clip, obj):
        def show_properties_window(sender, app_data, user_data):
            self._properties_buffer.clear()
            dpg.configure_item(get_properties_window_tag(user_data), show=True)

        with dpg.popup(parent=node_tag, tag=f"{node_tag}.popup", mousebutton=1):
            if not isinstance(obj, (model.DmxOutputGroup, model.DmxOutput)):
                dpg.add_menu_item(
                    label="Properties", callback=show_properties_window, user_data=obj
                )

            if isinstance(obj, model.MidiInput):
                dpg.add_menu_item(
                    label="Update MIDI Map",
                    callback=self.update_midi_map_node_callback,
                    user_data=obj,
                )
                dpg.add_menu_item(
                    label="Learn Input MIDI Map",
                    callback=self.create_and_show_learn_midi_map_window_callback,
                    user_data=(obj, "input"),
                )
                dpg.add_menu_item(
                    label="Learn Output MIDI Map",
                    callback=self.create_and_show_learn_midi_map_window_callback,
                    user_data=(obj, "output"),
                )

                def unmap_midi(sender, app_data, user_data):
                    obj = user_data
                    result = self.execute_wrapper(f"unmap_midi {obj.id}")
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

                dpg.add_menu_item(
                    label="Clear MIDI Map", callback=unmap_midi, user_data=obj
                )

            if isinstance(obj, (model.SourceNode,)):

                def copy(sender, app_data, user_data):
                    self.copy_buffer = [user_data]

                dpg.add_menu_item(
                    label="Copy",
                    callback=copy,
                    user_data=obj,
                )

                def delete(sender, app_data, user_data):
                    clip, obj = user_data
                    self.delete_node(clip, obj)

                dpg.add_menu_item(
                    label="Delete", callback=delete, user_data=(clip, obj)
                )

    def create_node_menu(self, parent, clip):
        right_click_menu = "popup_menu" in dpg.get_item_alias(parent)

        with dpg.menu(parent=parent, label="Sources"):
            dpg.add_menu_item(
                label="Bool",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "bool"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Integer",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "int"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Float",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "float"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Osc Integer",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "osc_input_int"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Osc Float",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "osc_input_float"), right_click_menu),
            )
            dpg.add_menu_item(
                label="MIDI",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "midi"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Color",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "color"), right_click_menu),
            )
            dpg.add_menu_item(
                label="Button",
                callback=self.add_source_node_callback,
                user_data=("create", (clip, "button"), right_click_menu),
            )

        with dpg.menu(parent=parent, label="Edit"):
            dpg.add_menu_item(
                label="Copy",
                callback=self.copy_selected,
            )

            def paste():
                self.paste_selected()
                dpg.configure_item(parent, show=False)

            dpg.add_menu_item(
                label="Paste",
                callback=paste,
            )

            dpg.add_menu_item(
                label="Delete",
                callback=self.delete_selected_nodes_callback,
                user_data=clip,
            )

    def create_properties_window(self, clip, obj):
        window_tag = get_properties_window_tag(obj)
        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(gui.SCREEN_WIDTH / 3, gui.SCREEN_HEIGHT / 3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

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
                        user_data=("name", get_node_tag(clip, obj)),
                        tag=f"{obj.id}.name",
                        no_spaces=True,
                    )

                if isinstance(obj, model.Parameterized):
                    for parameter_index, parameter in enumerate(obj.parameters):
                        with dpg.table_row():
                            dpg.add_text(default_value=parameter.name)
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

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.create_output_node(clip, output_channel)

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
            if util.valid(self._active_clip):
                dpg.configure_item(
                    get_node_tag(self._active_clip, output_channel_group),
                    label=app_data,
                )

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

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.create_output_group_node(clip, output_channel_group)

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
                dpg.add_menu_item(
                    label="Triggers",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.manage_trigger_window),
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
                    user_data=gui.ShowWindow(self.performance_preset_window),
                )

                dpg.add_menu_item(
                    label="Global Presets",
                    callback=self.action_callback,
                    user_data=gui.ShowWindow(self.global_performance_preset_window),
                )

                dpg.add_menu_item(
                    label="Sequences",
                    callback=self.create_and_show_track_sequences_window,
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
            gui.SelectInputNode(
                {"clip": self._active_clip, "channel": input_channel}
            ).execute()

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

    def duplicate_channel_preset_callback(self):
        if self._active_input_channel is None:
            return

        automation = self._active_input_channel.active_automation
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
                self._delete_node_gui(
                    get_node_tag(clip, output_channel), output_channel.id
                )

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
                self._delete_node_gui(
                    get_node_tag(clip, output_channel_group), output_channel_group.id
                )

            # Remake the window
            self.track_properties_windows[track.id].reset()
        else:
            RuntimeError(f"Failed to delete: {output_channel_group.id}")

    @gui_lock
    def delete_selected_nodes_callback(self, sender, app_data, user_data):
        clip = user_data
        node_editor_tag = get_node_editor_tag(clip)

        for item in dpg.get_selected_nodes(node_editor_tag):
            alias = dpg.get_item_alias(item)
            node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
            # Deleting outputs from the Node Editor GUI is not allowed.
            if "DmxOutput" in node_id:
                continue
            result = self.execute_wrapper(f"delete_node {clip.id} {node_id}")
            if result.success:
                self._delete_node_gui(alias, node_id)
            else:
                RuntimeError(f"Failed to delete: {node_id}")

    def delete_node(self, clip, obj):
        result = self.execute_wrapper(f"delete_node {clip.id} {obj.id}")
        if result.success:
            self._delete_node_gui(get_node_tag(clip, obj), obj.id)
        else:
            RuntimeError(f"Failed to delete: {node_id}")

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
            dpg.configure_item(tag, label=value)

        dpg.configure_item(get_properties_window_tag(obj), show=False)

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
    def add_source_node_callback(self, sender, app_data, user_data):
        """Figure out the type then call the correct downstream add_*_source_node function."""
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

        # This will only exist if the user has opened the "Presets Performance Window"
        preset_button_tag = get_preset_button_tag(preset)
        if dpg.does_item_exist(preset_button_tag):
            # Reset last button
            if (
                self._last_selected_preset_button_tag is not None
                and self._last_selected_preset_theme_tag is not None
            ):
                dpg.bind_item_theme(
                    self._last_selected_preset_button_tag,
                    self._last_selected_preset_theme_tag,
                )

            # Set new button theme
            self._last_selected_preset_button_tag = preset_button_tag
            self._last_selected_preset_theme_tag = get_channel_preset_theme(preset)

            dpg.bind_item_theme(preset_button_tag, "selected_preset.theme")

    def play_clip_callback(self, sender, app_data, user_data):
        track, clip = user_data
        if self.ctrl:
            result = self.execute_wrapper(f"play_clip {track.id} {clip.id}")
        else:
            result = self.execute_wrapper(f"set_clip {track.id} {clip.id}")

    def toggle_clip_play_callback(self, sender, app_data, user_data):
        track, clip = user_data
        result = self.execute_wrapper(f"toggle_clip {track.id} {clip.id}")

    def toggle_play_callback(self):
        self.state.toggle_play()
        if self.state.playing:
            dpg.bind_item_theme("play_button", "transport.play_button.play.theme")
        else:
            dpg.bind_item_theme("play_button", "transport.play_button.pause.theme")

    def update_midi_map_node_callback(self, sender, app_data, user_data):
        result = self.execute_wrapper(f"midi_map {user_data.id}")
        if not result.success:
            raise RuntimeError("Failed to map midi")

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
            tag="midi_map_window", modal=True, width=300, height=300, no_move=True
        ):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=save, user_data=obj)
                dpg.add_button(label="Cancel", callback=cancel, user_data=obj)

    def resize_windows_callback(self, sender, app_data, user_data):
        if app_data is None:
            new_width = gui.SCREEN_WIDTH
            new_height = gui.SCREEN_HEIGHT
        else:
            new_width, new_height = app_data[2:4]
        self.update_window_size_info(new_width, new_height)

        # Clip window
        dpg.set_item_pos("clip.gui.window", gui.WINDOW_INFO["clip_pos"])
        dpg.set_item_width("clip.gui.window", gui.WINDOW_INFO["clip_size"][0])
        dpg.set_item_height("clip.gui.window", gui.WINDOW_INFO["clip_size"][1])

        # Code windows
        def resize_code_window(obj):
            window_tag = get_code_window_tag(obj)
            if not dpg.does_item_exist(window_tag):
                return
            dpg.set_item_pos(window_tag, gui.WINDOW_INFO["code_pos"])
            dpg.set_item_width(window_tag, gui.WINDOW_INFO["code_size"][0])
            dpg.set_item_height(window_tag, gui.WINDOW_INFO["code_size"][1])
            dpg.set_item_width(
                window_tag + ".text", gui.WINDOW_INFO["code_size"][0] * 0.98
            )
            dpg.set_item_height(
                window_tag + ".text", gui.WINDOW_INFO["code_size"][1] * 0.91
            )

        resize_code_window(self.state)
        for track in self.state.tracks:
            resize_code_window(track)
            for clip in track.clips:
                if util.valid(clip):
                    resize_code_window(clip)

        # Console winodws
        dpg.set_item_pos("console.gui.window", gui.WINDOW_INFO["console_pos"])
        dpg.set_item_width("console.gui.window", gui.WINDOW_INFO["console_size"][0])
        dpg.set_item_height("console.gui.window", gui.WINDOW_INFO["console_size"][1])

        # Node Windows
        for track in self.state.tracks:
            resize_code_window(track)
            for clip in track.clips:
                if util.valid(clip):
                    window_tag = get_node_window_tag(clip)
                    if not dpg.does_item_exist(window_tag):
                        continue
                    dpg.set_item_pos(window_tag, gui.WINDOW_INFO["node_pos"])
                    dpg.set_item_width(window_tag, gui.WINDOW_INFO["node_size"][0])
                    dpg.set_item_height(window_tag, gui.WINDOW_INFO["node_size"][1])

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
                self.node_editor_window_is_focused = self.mouse_inside_window(tag)
        elif app_data == 1:
            self.mouse_clickr_x, self.mouse_clickr_y = self.mouse_x, self.mouse_y

            # Right clicking things should disable focus
            self.node_editor_window_is_focused = False

            # Show popup menu
            # TODO: Interfering with node properties
            if self._active_clip is not None and self.ctrl:
                tag = get_node_window_tag(self._active_clip)
                window_x, window_y = dpg.get_item_pos(tag)
                window_x2, window_y2 = window_x + dpg.get_item_width(
                    tag
                ), window_y + dpg.get_item_height(tag)
                if util.inside(
                    (self.mouse_x, self.mouse_y),
                    (window_x, window_x2, window_y + 10, window_y2),
                ):
                    popup_menu_tag = (
                        get_node_window_tag(self._active_clip) + ".popup_menu"
                    )
                    dpg.configure_item(popup_menu_tag, pos=(self.mouse_x, self.mouse_y))
                    dpg.configure_item(popup_menu_tag, show=True)
                    dpg.focus_item(popup_menu_tag)

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

        if key_n in [18]:
            self.keyboard_mode_change_callback(None, None, None)

        if self.keyboard_mode:
            return

        if key == " " and self.shift:
            self.toggle_play_callback()
        elif key_n in [8, 46] and self.node_editor_window_is_focused and self.ctrl:
            self.delete_selected_nodes_callback(None, None, self._active_clip)
        elif key_n in [120]:
            if self._active_input_channel is not None:
                self.enable_recording_mode_callback(
                    None, None, self._active_input_channel
                )
        elif key_n in [9]:  # tab
            pass
        elif key in ["C"]:
            if self.ctrl:
                self.copy_selected()
        elif key in ["O"]:
            if self.ctrl:
                self.open_menu_callback()
        elif key in ["I"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_source_node_callback(
                        None, None, ("create", (self._active_clip, "int"), False)
                    )
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_source_node_callback(
                        None, None, ("create", (self._active_clip, "bool"), False)
                    )
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_source_node_callback(
                        None, None, ("create", (self._active_clip, "float"), False)
                    )
        elif key in ["T"]:
            if self.state.mode == "performance":
                self.tap_tempo_callback()
        elif key in ["V"]:
            if self.ctrl:
                self.paste_selected()
        elif key in ["R"]:
            if self.ctrl:
                if util.valid(self._active_clip):
                    node_editor_tag = get_node_editor_tag(self._active_clip)
                    items = dpg.get_selected_nodes(node_editor_tag)
                    if items:
                        item = items[0]
                        alias = dpg.get_item_alias(item)
                        node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                        obj = self.state.get_obj(node_id)
                        dpg.set_value("rename_node.text", obj.name)
                    else:
                        dpg.set_value("rename_node.text", self._active_clip.name)
                    dpg.configure_item("rename_node.popup", show=True)
                    dpg.focus_item("rename_node.text")
            elif self.state.mode == "performance":
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
        key = chr(key_n)

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
        self.save_code()

        node_positions = {}
        for track_i, track in enumerate(self.state.tracks):
            for clip in track.clips:
                if clip is None:
                    continue

                for input_channel in clip.inputs:
                    if not util.valid(input_channel):
                        continue
                    tag = get_node_tag(clip, input_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)
                for output_channel in clip.outputs:
                    if not util.valid(output_channel):
                        continue
                    tag = get_node_tag(clip, output_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)

        gui_data = self.gui_state.copy()

        gui_data.update(
            {
                "node_positions": node_positions,
            }
        )

        data = {"state": self.state.serialize(), "gui": gui_data}

        with open(self.state.project_file_path, "w") as f:
            f.write(json.dumps(data, indent=4, sort_keys=False))

        dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}]")

    def save_code(self):
        if not os.path.exists(self.state.project_folder_path):
            os.mkdir(self.state.project_folder_path)
            os.mkdir(os.path.join(self.state.project_folder_path, "code"))

        self.state.code.save(dpg.get_value(get_code_window_tag(self.state) + ".text"))
        self.state.code.reload()
        for track in self.state.tracks:
            track.code.save(dpg.get_value(get_code_window_tag(track) + ".text"))
            track.code.reload()
            for clip in track.clips:
                if util.valid(clip):
                    clip.code.save(dpg.get_value(get_code_window_tag(clip) + ".text"))
                    clip.code.reload()

    def restore_gui_state(self):
        for tag, pos in self.gui_state["node_positions"].items():
            try:
                dpg.set_item_pos(tag, pos)
            except:
                pass
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

        for theme_tag, color in self.gui_state["clip_preset_themes"].items():
            dpg.configure_item(theme_tag, value=color)

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
    parser = argparse.ArgumentParser(description="NodeDMX [BETA]")
    parser.add_argument(
        "--project", default=None, dest="project_file_path", help="Project file path."
    )

    parser.add_argument(
        "--cache", default=".cache", dest="cache_file_path", help="Cached data."
    )

    parser.add_argument(
        "--profile", default=False, dest="profile", help="Enable profiling."
    )

    parser.add_argument(
        "--debug", default=False, dest="debug", help="Enable debug mode."
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
