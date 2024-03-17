"""
TODO: 
    * Sequence editing, deleting, reordering
    * Preset editing 
    * Change tab color for currently playing automation, update on every cycle like update_clip_window(), may not be nessecary after
    * Auto reload code
    * Dont allow spaces and special characters in node names
    * Don't allow code to poison objects
    * Delete Channel "direction"

"""

import dearpygui.dearpygui as dpg
import model
import fixtures
import re
from copy import copy
import math
import time
import pickle
from threading import RLock, Thread, Event
import numpy as np
import os
import mido
from collections import defaultdict
import json
from cProfile import Profile
from pstats import SortKey, Stats
import argparse
import subprocess
import sys
import logging

logging.basicConfig(filename="log.txt",
                    filemode='w',
                    format='[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
                    level=logging.DEBUG)


logger = logging.getLogger(__name__)

_GUI = None

CODE_WINDOW_POS = (801, 38)
TOP_LEFT = (0, 18)
SCREEN_WIDTH = 1940
SCREEN_HEIGHT = 1150
PROJECT_EXTENSION = "ndmx"
NODE_EXTENSION = "ndmxc"
AXIS_MARGIN = 0.025
HUMAN_DELAY = 0.125
CLIP_VIEW = 0
TRACK_VIEW = 1
GLOBAL_VIEW = 2
DEFAULT_SEQUENCE_DURATION = 4

def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)

def norm_distance(p1, p2, x_limit, y_limit):
    np1 = p1[0]/x_limit[1], p1[1]/y_limit[1],
    np2 = p2[0]/x_limit[1], p2[1]/y_limit[1],
    return math.sqrt((np2[0] - np1[0]) ** 2 + (np2[1] - np1[1]) ** 2)

def inside(p1, rect):
    x = rect[0] <= p1[0] <= rect[1]
    y = rect[2] <= p1[1] <= rect[3]
    return x and y

def valid(*objs):
    return all([
        obj is not None and not getattr(obj, "deleted", False)
        for obj in objs
    ])


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

def get_clip_slot_group_tag(track_i, clip_i):
    return f"track[{track_i}].clip[{clip_i}].gui.table_group"

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



def show_callback(sender, app_data, user_data):
    dpg.configure_item(user_data, show=True)



class GuiAction:
    def __init__(self, params=None):
        global _GUI
        self.gui = _GUI
        self.state = self.gui.state
        self.params = params or {}

    def __call__(self, sender, app_data, user_data):
        self.gui.action(self)


class SelectTrack(GuiAction):
    def execute(self):
        # When user clicks on the track title, bring up the output configuration window.
        track = self.params["track"]
        if self.gui._active_track == track:
            return

        self.gui.save_last_active_clip()


        self.last_track = self.gui._active_track
        self.last_clip = self.gui._active_clip

        # Unset activate clip
        self.gui._active_clip = None
        self.gui._active_clip_slot = None
        for tag in self.gui.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)

        self.gui._active_track = track
        last_active_clip_id = self.gui.gui_state["track_last_active_clip"].get(self.gui._active_track.id)
        if last_active_clip_id is not None:
            self.gui._active_clip = self.gui.state.get_obj(last_active_clip_id)
            SelectClip({"track":self.gui._active_track, "clip":self.gui._active_clip}).execute()


class SelectEmptyClipSlot(GuiAction):

    def execute(self):
        new_track_i = self.params["track_i"]
        new_clip_i = self.params["clip_i"]
        self.old_clip_slot = self.gui._active_clip_slot

        self.gui._active_clip_slot = (new_track_i, new_clip_i)
        self.gui._active_track = self.state.tracks[new_track_i]

    def undo(self):
        if self.old_clip_slot is None:
            return
        self.gui._active_clip_slot = self.old_clip_slot
        self.gui._active_track = self.state.tracks[self.old_clip_slot[0]]


class SelectClip(GuiAction):

    def execute(self):
        track = self.params["track"]
        clip = self.params["clip"]

        self.gui.save_last_active_clip()
        self.last_track = self.gui._active_track
        self.last_clip = self.gui._active_clip
        
        self.gui._active_track = track
        self.gui._active_clip = clip

        for tag in self.gui.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(clip), show=True)
        if self.gui.code_view == CLIP_VIEW:
            dpg.configure_item(get_code_window_tag(clip), show=True)
        elif self.gui.code_view == TRACK_VIEW:
            dpg.configure_item(get_code_window_tag(track), show=True)
        else:
            dpg.configure_item(get_code_window_tag(self.state), show=True)

        if self.state.mode == "performance":
            self.gui.create_and_show_all_automation_window(None, None, clip)

    def undo(self):
        self.gui.save_last_active_clip()
        self.gui._active_track = self.last_track
        self.gui._active_clip = self.last_clip

        for tag in self.gui.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(self.last_clip), show=True)
       

class NewClip(GuiAction):

    def execute(self):
        track_i = self.params["track_i"]
        clip_i = self.params["clip_i"]
        
        track = self.state.tracks[track_i]
        action = self.params.get("action")

        if action == "create":
            result = self.gui.execute_wrapper(f"new_clip {track.id},{clip_i}")
            if not result.success:
                raise RuntimeError("Failed to create clip")

        # Delete the double_click handler to create clips
        dpg.delete_item(get_clip_slot_group_tag(track_i, clip_i) + ".clip.item_handler_registry")

        clip = track.clips[clip_i]

        group_tag = get_clip_slot_group_tag(track_i, clip_i)
        for slot, child_tags in dpg.get_item_children(group_tag).items():
            for child_tag in child_tags:
                dpg.delete_item(child_tag)

        with dpg.group(parent=group_tag, horizontal=True, horizontal_spacing=5):
            dpg.add_button(arrow=True, direction=dpg.mvDir_Right, tag=f"{clip.id}.gui.play_button", callback=self.gui.toggle_clip_play_callback, user_data=(track,clip))                        
        
        clip_tag = group_tag + ".clip"
        with dpg.group(parent=group_tag, tag=clip_tag, horizontal=True, horizontal_spacing=5):
            text_tag = f"{clip.id}.name"
            self.gui.create_passive_button(clip_tag, text_tag, clip.name, SelectClip({"track": track, "clip": clip}))

        def copy_clip_callback(sender, app_data, user_data):
            self.gui.copy_buffer = [user_data]

        for tag in [text_tag, text_tag+".filler"]:
            with dpg.popup(tag, mousebutton=1):
                def show_properties_window(sender, app_data, user_data):
                    self.gui._properties_buffer.clear()
                    dpg.configure_item(get_properties_window_tag(clip), show=True)
                dpg.add_menu_item(label="Properties", callback=show_properties_window)

                dpg.add_menu_item(label="Copy", callback=copy_clip_callback, user_data=clip)
                dpg.add_menu_item(label="Paste", callback=self.gui.paste_clip_callback, user_data=(track_i, clip_i))
                dpg.add_menu_item(label="Edit Code", callback=self.gui.open_code_editor, user_data=(track_i, clip_i))

        self.last_track = self.gui._active_track
        self.last_clip = self.gui._active_clip
        self.gui.save_last_active_clip()
        self.gui._active_track = track
        self.gui._active_clip = clip

        # Create the properties window 
        self.create_clip_properties_window(clip)

        # Add the associated node and code editor 
        self.create_node_editor_window(clip)
        self.gui.create_code_editor_window(clip)


    def create_clip_properties_window(self, clip):
        window_tag = get_properties_window_tag(clip)

        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH/3,SCREEN_HEIGHT/3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"
            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                def update_clip_buffer_callback(sender, app_data, user_data):
                    property_name = user_data
                    self.gui._properties_buffer["clip"][property_name] = app_data

                def save_clip_properties_callback(sender, app_data, user_data):
                    clip = user_data
                    for property_name, value in self.gui._properties_buffer["clip"].items():
                        setattr(clip, property_name, value)
                    dpg.configure_item(window_tag, show=False)

                def cancel_properties_callback(sender, app_data, user_data):
                    clip = user_data
                    dpg.set_value(f"{clip.id}.name", clip.name)
                    dpg.configure_item(window_tag, show=False)

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(source=f"{clip.id}.name", callback=update_clip_buffer_callback, user_data=("name"))

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save_clip_properties_callback, user_data=clip)
                        dpg.add_button(label="Cancel", callback=cancel_properties_callback, user_data=clip)

    def create_node_editor_window(self, clip):
        logging.debug("Creating Node Editor Window (%s)", clip.id)
        window_tag = get_node_window_tag(clip)
        self.gui.tags["hide_on_clip_selection"].append(window_tag)

        with dpg.window(
            tag=window_tag,
            label=f"Node Window | {clip.name}",
            width=800,
            height=570,
            pos=(0, 537),
            no_title_bar=True,
            no_move=True,

        ) as window:
            # Node Editor
            node_editor_tag = get_node_editor_tag(clip)
            dpg.add_node_editor(
                tag=node_editor_tag,
                user_data=("create", clip),
                minimap=True,
                minimap_location=dpg.mvNodeMiniMap_Location_BottomRight
            )

            menu_tag = f"{window_tag}.menu_bar"
            with dpg.menu_bar(tag=menu_tag):
                self.gui.add_node_menu(menu_tag, clip)

                preset_menu_tag = f"{menu_tag}.preset_menu"
                with dpg.menu(label="Presets", tag=preset_menu_tag):
                    dpg.add_menu_item(label="New Preset", callback=self.gui.create_and_show_save_presets_window, user_data=(clip, None))
                    dpg.add_menu_item(label="Reorder", callback=self.gui.create_and_show_reorder_window, user_data=(clip.presets, preset_menu_tag, get_preset_menu_bar_tag))
                    for preset in clip.presets:
                        self.gui.add_clip_preset_to_menu(clip, preset)

                dpg.add_menu_item(label="Show All Automation", callback=self.gui.create_and_show_all_automation_window, user_data=clip)


        ###############
        ### Restore ###
        ###############

        # Popup window for adding node elements
        popup_window_tag = get_node_window_tag(clip) + ".popup_menu"
        with dpg.window(tag=popup_window_tag, show=False, no_title_bar=True):
            self.gui.add_node_menu(popup_window_tag, clip)

        for input_index, input_channel in enumerate(clip.inputs):
            if input_channel.deleted:
                continue
            self.gui.add_source_node(sender=None, app_data=None, user_data=("restore", (clip, input_channel), False))

        for output_index, output_channel in enumerate(clip.outputs):
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                self.gui.add_output_group_node(clip, output_channel)
            else:
                self.gui.add_output_node(clip, output_channel)


class Gui:

    def __init__(self):
        global _GUI
        _GUI = self

        self.tags = {}
        
        self.state = model.ProgramState()

        self.gui_state = {
            "node_positions": {},
            "io_types": {
                "inputs": [None] * 5,
                "outputs": [None] * 5,
            },
            "io_args": {
                "inputs": [None] * 5,
                "outputs": [None] * 5,
            },
            "track_last_active_clip": {},
            "point_tags": [],
            "clip_preset_themes": {}
        }

        self.cache = {
            "recent": []
        }

        self._code_buffer = {}

        self.mouse_x, self.mouse_y = 0, 0
        self.mouse_drag_x, self.mouse_drag_y = 0, 0
        self.mouse_click_x, self.mouse_click_y = 0, 0
        self.mouse_clickr_x, self.mouse_clickr_y = 0, 0
        self.node_editor_window_is_focused = False
        self.code_view = CLIP_VIEW

        self._active_track = None
        self._active_clip = None
        self._active_clip_slot = None
        self._active_output_channel = None
        self._active_input_channel = None
        self._inspecter_x = list(range(500))

        self._last_selected_preset_button_tag = None
        self._last_selected_preset_theme_tag = None

        self._properties_buffer = defaultdict(dict)
        self._global_presets_buffer = {}
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

    def gui_lock_callback(func):
        def wrapper(self, sender, app_data, user_data):
            with self.lock:
                return func(self, sender, app_data, user_data)
        return wrapper

    def execute_wrapper(self, command):
        dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}] *")
        result = self.state.execute(command)
        return result

    def action(self, action):
        # Gui actions can modify state. Use the lock to make sure 
        # the enture state is updated before the GUI tries to render.
        with self.lock:
            action.execute()
        self.past_actions.append(action)

    def action_callback(self, sender, app_data, user_data):
        self.action(user_data)

    def run(self):
        self.initialize()
        self.main_loop()

    def main_loop(self):
        logging.debug("Starting main loop")
        thread = Thread(target=self.state_loop)
        thread.daemon = True
        thread.start()

        try:
            while dpg.is_dearpygui_running():
                self.update_clip_window()
                with self.lock:
                    self.update_gui_from_state()
                dpg.render_dearpygui_frame()
            dpg.destroy_context()
        except Exception as e:
            raise e
            logger.warning(e)
            print(e)

    def state_loop(self):
        period = 1/60.0
        while True:
            t_start = time.time()
            self.state.update()
            t_end = time.time()
            delta_t = t_end - t_start
            if delta_t < period:
                time.sleep(period-delta_t)
    
    def initialize(self):
        logging.debug("Initializing")
        self.tags = {
            "hide_on_clip_selection": [],
            "node_window": [],
        }
        dpg.create_context()

        # Themes
        with dpg.theme(tag="playhead_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvPlotCol_Line, (255, 255, 0, 255), tag="playhead_line.color", category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1, category=dpg.mvThemeCat_Plots)

        with dpg.theme(tag="bg_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvPlotCol_Line, (255, 255, 255, 30), category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 1, category=dpg.mvThemeCat_Plots)

        with dpg.theme(tag="automation_line.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvPlotCol_Line, (0, 200, 255), category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 3, category=dpg.mvThemeCat_Plots)

        with dpg.theme(tag="transport.play_button.pause.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (255, 255, 255, 30), category=dpg.mvThemeCat_Core)
        with dpg.theme(tag="transport.play_button.play.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 255, 0, 60), category=dpg.mvThemeCat_Core)

        with dpg.theme(tag="code_editor.global.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (100, 255, 0, 10), category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (100, 255, 0, 50), category=dpg.mvThemeCat_Core)
        with dpg.theme(tag="code_editor.track.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 10), category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (255, 255, 255, 50), category=dpg.mvThemeCat_Core)
        with dpg.theme(tag="code_editor.clip.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (0, 100, 255, 10), category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 100, 255, 50), category=dpg.mvThemeCat_Core)

        with dpg.theme(tag="selected_preset.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(target=dpg.mvThemeCol_Text, value=[0, 0, 0, 255], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(target=dpg.mvThemeCol_Button, value=[255, 255, 0, 255], category=dpg.mvThemeCat_Core)

        with dpg.theme(tag="not_selected_preset.theme"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(target=dpg.mvThemeCol_Text, value=[255, 255, 255, 255], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(target=dpg.mvThemeCol_Button, value=[50, 50, 50, 255], category=dpg.mvThemeCat_Core)

        self._active_track = self.state.tracks[0]

        #### Create Console ####
        console_window = dpg.window(tag="console.gui.window", label="Console", pos=(801,537), width=SCREEN_WIDTH-800, height=SCREEN_HEIGHT-520, no_move=True, no_title_bar=True, no_resize=True)
        with console_window as window:
            def clear_errors():
                self.state.log.clear()
            dpg.add_button(label="Clear", callback=clear_errors)
            dpg.add_input_text(tag="console.text", multiline=True, readonly=True, width=SCREEN_WIDTH-800-35, height=SCREEN_HEIGHT-520-70)

        #### Create Code Editor Window ####
        self.create_code_editor_window(self.state)
        for track in self.state.tracks:
            self.create_code_editor_window(track)

        #### Create Clip Window ####
        logging.debug("Creating Clip Window")
        clip_window = dpg.window(tag="clip.gui.window", label="Clip", pos=(0,18), width=800, height=520, no_move=True, no_title_bar=True, no_resize=True)
        with clip_window as window:
            table_tag = f"clip_window.table"
            with dpg.table(header_row=False, tag=table_tag,
                   borders_innerH=True, borders_outerH=True, borders_innerV=True,
                   borders_outerV=True, policy=dpg.mvTable_SizingStretchProp, resizable=True):

                for track_i in range(len(self.state.tracks)):
                    dpg.add_table_column()

                # Track Header Row
                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):
                        with dpg.table_cell():
                            with dpg.group(horizontal=True) as group_tag:

                                def show_track_output_configuration_window(sender, app_data, user_data):
                                    # Hide all track config windows
                                    for track in self.state.tracks:
                                        dpg.configure_item(get_output_configuration_window_tag(track), show=False)
                                    dpg.configure_item(get_output_configuration_window_tag(user_data), show=True)
                                    dpg.focus_item(get_output_configuration_window_tag(user_data))

                                text_tag = f"{track.id}.gui.button"
                                self.create_passive_button(group_tag, text_tag, track.name, single_click_callback=SelectTrack({"track":track}))

                                # Menu for track
                                for tag in [text_tag, text_tag+".filler"]:
                                    with dpg.popup(tag, mousebutton=1):
                                        dpg.add_menu_item(label="Properties", callback=show_track_output_configuration_window, user_data=track)

                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    with dpg.table_row(height=10):
                        for track_i, track in enumerate(self.state.tracks):
                            clip = track.clips[clip_i]
                            with dpg.table_cell():
                                group_tag = get_clip_slot_group_tag(track_i, clip_i)
                                with dpg.group(tag=group_tag, horizontal=True):

                                    with dpg.group(tag=group_tag + ".clip", horizontal=True, horizontal_spacing=5):
                                        # Always add elements for an empty clip, if the clip is not empty, then we will update it after.
                                        text_tag = f"{track.id}.{clip_i}.gui.text"
                                        self.create_passive_button(
                                            group_tag + ".clip", 
                                            text_tag, 
                                            "", 
                                            single_click_callback=SelectEmptyClipSlot({"track_i":track_i, "clip_i":clip_i}), 
                                            double_click_callback=NewClip({"track_i":track_i, "clip_i":clip_i, "action":"create"}),
                                        )
                                        # Menu for empty clip
                                        with dpg.popup(text_tag+".filler", mousebutton=1):
                                            dpg.add_menu_item(label="New Clip", callback=self.action_callback, user_data=NewClip({"track_i":track_i, "clip_i":clip_i, "action":"create"}))
                                            dpg.add_menu_item(label="Paste", callback=self.paste_clip_callback, user_data=(track_i, clip_i))

                            if clip is not None:
                                self.action(NewClip({"track_i":track_i, "clip_i":clip_i}))

        #### Mouse/Key Handlers ####
        logging.debug("Installing mouse/key handlers")
        with dpg.handler_registry():
            dpg.add_mouse_move_handler(callback=self.mouse_move_callback)
            dpg.add_mouse_click_handler(callback=self.mouse_click_callback)
            dpg.add_mouse_double_click_handler(callback=self.mouse_double_click_callback)
            dpg.add_key_press_handler(callback=self.key_press_callback)
            dpg.add_key_down_handler(callback=self.key_down_callback)
            dpg.add_key_release_handler(callback=self.key_release_callback)

        # Create Viewport
        logging.debug("Creating Viewport")
        dpg.create_viewport(title=f"NodeDMX [{self.state.project_name}] *", width=SCREEN_WIDTH, height=SCREEN_HEIGHT, x_pos=50, y_pos=0)

        # File Dialogs
        def save_callback(sender, app_data):
            file_path_name = app_data["file_path_name"]
            project_name = os.path.basename(file_path_name).replace(f".{PROJECT_EXTENSION}", "")
            root_dir = os.path.dirname(file_path_name)
            project_folder_path = os.path.join(root_dir, project_name)
            project_file_path = os.path.join(project_folder_path, f"{project_name}.{PROJECT_EXTENSION}")

            self.state.project_name = project_name
            self.state.project_folder_path = project_folder_path
            self.state.project_file_path = project_file_path
            self.save()

        def restore_callback(sender, app_data):
            self.open_project(app_data["file_path_name"])

        def restore_callback2(sender, app_data, user_data):
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
                modal=True
            )

            dpg.add_file_dialog(
                directory_selector=False, 
                show=False, 
                callback=load_custom_fixture, 
                tag="open_fixture_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True
            )

            for tag in ["open_file_dialog", "save_file_dialog"]:
                dpg.add_file_extension(f".{PROJECT_EXTENSION}", color=[255, 255, 0, 255], parent=tag)

            dpg.add_file_extension(f".fixture", color=[0, 255, 255, 255], parent="open_fixture_dialog")

            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open", callback=self.open_menu_callback)
                
                with dpg.menu(label="Open Recent"):
                    for filepath in self.cache["recent"]:
                        dpg.add_menu_item(label=os.path.basename(filepath), callback=restore_callback2, user_data=filepath)

                dpg.add_menu_item(label="Save", callback=self.save_menu_callback)
                dpg.add_menu_item(label="Save As", callback=self.save_as_menu_callback)

            with dpg.menu(label="View"):
                def show_io_window():
                    dpg.configure_item("io.gui.window", show=True); 
                    dpg.focus_item("io.gui.window")                    
                dpg.add_menu_item(label="I/O", callback=show_io_window)

                def show_inspector():
                    dpg.configure_item("inspector.gui.window", show=False)
                    dpg.configure_item("inspector.gui.window", show=True)
                    dpg.focus_item("inspector.gui.window")
                dpg.add_menu_item(label="Inspector", callback=show_inspector)

            with dpg.menu(label="Performance"):
                dpg.add_menu_item(label="All Presets", callback=self.create_and_show_performance_preset_window)

                def show_global_performance_presets_window():
                    dpg.configure_item("global_performance_preset.gui.window", show=True)
                    dpg.focus_item("global_performance_preset.gui.window")
                dpg.add_menu_item(label="Global Presets", callback=show_global_performance_presets_window)

                dpg.add_menu_item(label="Sequences", callback=self.create_and_show_track_sequences_window)



            # Transport 
            transport_start_x = 800
            dpg.add_button(label="Reset", callback=self.reset_time, pos=(transport_start_x-100, 0))

            transport_start_x = 800
            dpg.add_button(label="Tap Tempo", callback=self.tap_tempo, pos=(transport_start_x, 0))

            def update_tempo(sender, app_data):
                self.state.tempo = app_data
            #dpg.add_text("Tempo:", pos=(transport_start_x + 90,0))
            dpg.add_input_float(label="Tempo", default_value=self.state.tempo, pos=(transport_start_x + 75, 0), on_enter=True, callback=update_tempo, width=45, tag="tempo", step=0)

            dpg.add_button(label="[Paused]", callback=self.toggle_play_callback, pos=(transport_start_x + 220, 0), tag="play_button")
            dpg.bind_item_theme("play_button", "transport.play_button.pause.theme")

            def mode_change():
                self.state.mode = "edit" if self.state.mode == "performance" else "performance"
                dpg.configure_item("mode_button", label="Edit Mode" if self.state.mode == "edit" else "Performance Mode")
                dpg.set_item_pos("mode_button", (transport_start_x+1000+50, 0) if self.state.mode == "edit" else (transport_start_x+1000, 0))
            dpg.add_button(label="Edit Mode", callback=mode_change, pos=(transport_start_x+1000+50, 0), tag="mode_button")

            # Global Variables
            with dpg.value_registry():
                dpg.add_string_value(default_value="", tag="last_midi_message")

        ################
        #### Restore ###
        ################

        logging.debug("Restoring program state.")
        # Need to create this after the node_editor_windows
        for track in self.state.tracks:                           
            self.create_track_output_configuration_window(track)

        self.create_inspector_window()
        self.create_io_window()
        self.create_global_performance_preset_window()
        self.create_rename_window()

        logging.debug("Restoring GUI state.")
        self.restore_gui_state()

        dpg.setup_dearpygui()
        dpg.show_viewport()

    def open_menu_callback(self):
        dpg.configure_item("open_file_dialog", show=True)

    def save_menu_callback(self):
        if self.state.project_file_path is None:
            dpg.configure_item("save_file_dialog", show=True)
        else:
            self.save()

    def save_as_menu_callback(self):
        dpg.configure_item("save_file_dialog", show=True)

    def create_passive_button(self, group_tag, text_tag, text, single_click_callback=None, double_click_callback=None, user_data=None, double_click=False):
        dpg.add_text(parent=group_tag, default_value=text, tag=text_tag)
        dpg.add_text(parent=group_tag, default_value=" "*1000, tag=f"{text_tag}.filler")
        if single_click_callback is not None:
            self.register_handler(dpg.add_item_clicked_handler, group_tag, self.action_callback, single_click_callback)
        if double_click_callback is not None:
            self.register_handler(dpg.add_item_double_clicked_handler, group_tag, self.action_callback, double_click_callback)

    @gui_lock_callback
    def paste_clip_callback(self, sender, app_data, user_data):
        self.paste_clip(*user_data)

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

    def register_handler(self, add_item_handler_func, tag, function, user_data=None):
        handler_registry_tag = f"{tag}.item_handler_registry"
        if not dpg.does_item_exist(handler_registry_tag):
            dpg.add_item_handler_registry(tag=handler_registry_tag)
        add_item_handler_func(parent=handler_registry_tag, callback=function, user_data=user_data)
        dpg.bind_item_handler_registry(tag, handler_registry_tag)

    def reset_time(self):
        self.state.play_time_start_s = time.time() - HUMAN_DELAY

    def tap_tempo(self):
        self._tap_tempo_buffer.insert(0, time.time())
        self._tap_tempo_buffer.pop()
        dts = []
        for i in range(len(self._tap_tempo_buffer)-1):
            dt = abs(self._tap_tempo_buffer[i] - self._tap_tempo_buffer[i+1])
            if dt < 2:
                dts.append(dt)
        t = sum(dts)/len(dts)
        if t == 0:
            return
        self.state.tempo = round(60.0/t, 2)
        dpg.set_value("tempo", self.state.tempo)

    def update_clip_window(self):
        for track_i, track in enumerate(self.state.tracks):
            for clip_i, clip in enumerate(track.clips):
                clip_color = [0, 0, 0, 100]
                if self._active_clip_slot == (track_i, clip_i):
                    clip_color[2]+=  155
                if valid(clip) and clip == self._active_clip:
                    clip_color[2] += 255
                if valid(clip) and clip.playing:
                    clip_color[1] += 255
                if valid(clip) and not clip.playing:
                    clip_color[2] += 100
                    clip_color[1] += 50
                if not valid(clip):
                    clip_color[0] = 50
                    clip_color[1] = 50
                    clip_color[2] = 50
                if self._active_clip_slot == (track_i, clip_i):
                    clip_color[3] += 50
                dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=clip_color)
            if self._active_track == track:
                dpg.highlight_table_column("clip_window.table", track_i, color=[100, 100, 100, 255])
            else:
                dpg.highlight_table_column("clip_window.table", track_i, color=[0, 0, 0, 0])

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
                color = [int(255*v) for v in app_data]
            dpg.configure_item(f"{menu_theme}.text_color", value=color)
            dpg.configure_item(f"{menu_theme}.button_bg_color", value=color)
            self.gui_state["clip_preset_themes"][f"{menu_theme}.text_color"] = color
            self.gui_state["clip_preset_themes"][f"{menu_theme}.button_bg_color"] = color

        def duplicate_clip_preset(sender, app_data, user_data):
            clip, preset = user_data
            result = self.execute_wrapper(f"duplicate_clip_preset {clip.id} {preset.id}")
            if result.success:
                self.add_clip_preset_to_menu(clip, result.payload, before=preset)

        with dpg.menu(parent=preset_menu_tag, tag=preset_menu_bar, label=preset.name, drop_callback=self.print_callback, before=get_preset_menu_bar_tag(before) if valid(before) else 0):
            dpg.add_menu_item(tag=f"{preset_menu_bar}.activate", label="Activate", callback=self.play_clip_preset_callback, user_data=preset)
            dpg.add_menu_item(tag=f"{preset_menu_bar}.edit", label="Edit", callback=self.create_and_show_save_presets_window, user_data=(clip, preset))
            dpg.add_menu_item(tag=f"{preset_menu_bar}.duplicate", label="Duplicate", callback=duplicate_clip_preset, user_data=(clip, preset))
            with dpg.menu(label="Select Color"):
                with dpg.group(horizontal=True):
                    dpg.add_color_button(callback=set_color, default_value=(0, 200, 255))
                    dpg.add_color_button(callback=set_color, default_value=(0, 255, 100))
                    dpg.add_color_button(callback=set_color, default_value=(255, 100, 100))
                    dpg.add_color_button(callback=set_color, default_value=(255, 255, 255))
                    dpg.add_color_button(callback=set_color, default_value=(0, 0, 0))
                dpg.add_color_picker(display_type=dpg.mvColorEdit_uint8 , callback=set_color)
            dpg.add_menu_item(tag=f"{preset_menu_bar}.delete", label="Delete", callback=self.delete_clip_preset, user_data=preset)

        with dpg.theme(tag=menu_theme):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(tag=f"{menu_theme}.text_color", target=dpg.mvThemeCol_Text, value=[255, 255, 255, 255], category=dpg.mvThemeCat_Core)
                dpg.add_theme_color(tag=f"{menu_theme}.button_bg_color", target=dpg.mvThemeCol_Border, value=[255, 255, 255, 255], category=dpg.mvThemeCat_Core)
        dpg.bind_item_theme(preset_menu_bar, menu_theme)

    def create_and_show_save_presets_window(self, sender, app_data, user_data):
        clip, preset = user_data

        # If a valid preset was passed in, this means we're editing it.
        # Otherwise, we're creating a new one.
        editing = valid(preset)

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
                    presets.append({
                        "channel": channel.id,
                        "automation": self._clip_preset_buffer[channel.id],
                        "speed": dpg.get_value(f"preset.{i}.speed")
                        })
            
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
                        dpg.configure_item(get_preset_menu_bar_tag(preset), label=preset.name)
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

        with dpg.window(tag=preset_window_tag, modal=True, width=600, height=500, no_move=True):
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Preset Name")
                    dpg.add_input_text(tag="preset.name", default_value = preset.name if editing else "")

                with dpg.table_row():
                    dpg.add_text(default_value="Channel")
                    dpg.add_text(default_value="Preset")
                    dpg.add_text(default_value="Speed (2^n)")
                    dpg.add_text(default_value="Include")


                preset_data = {}
                speed_data = {}
                if editing:
                    preset_data = {
                        channel.id: (automation, speed)
                        for channel, automation, speed in preset.presets
                    }

                for i, channel in enumerate(clip.inputs):
                    # TODO: Include constants in presets
                    if not isinstance(channel, model.AutomatableSourceNode):
                        continue
                    
                    with dpg.table_row():
                        dpg.add_text(default_value=channel.name)
                        
                        with dpg.menu(tag=f"{channel.id}.select_preset_bar", label="Select Preset"):
                            for automation in channel.automations:
                                dpg.add_menu_item(label=automation.name, callback=set_automation, user_data=(channel, automation))
                        if channel.id in preset_data:
                            set_automation(None, None, (channel, preset_data[channel.id][0]))
                        elif valid(channel.active_automation):                            
                            set_automation(None, None, (channel, channel.active_automation))

                        dpg.add_input_int(tag=f"preset.{i}.speed", default_value=preset_data[channel.id][1] if channel.id in preset_data else channel.speed)

                        dpg.add_checkbox(tag=f"preset.{i}.include", default_value=channel.id in preset_data)

                with dpg.table_row():
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)

    def create_and_show_new_global_presets_window(self, sender, app_data, user_data):
        save_global_preset_window_tag = "global_preset_window"
        try:
            dpg.delete_item(save_global_preset_window_tag)
        except:
            pass

        def cancel(sender, app_data, user_data):
            dpg.delete_item(save_global_preset_window_tag)

        def save(sender, app_data, user_data):
            global_presets = []
            for i, track in enumerate(self.state.tracks):
                include = dpg.get_value(f"global_preset.{i}")
                if include:
                    track, clip, preset = self._global_presets_buffer[i]
                    global_presets.append(":".join([track.id, clip.id, preset.id]))
            
            if global_presets:
                name = dpg.get_value("global_preset.name")
                result = self.execute_wrapper(f"add_global_preset {','.join(global_presets)} {name}")
                if result.success:
                    dpg.configure_item(item=save_global_preset_window_tag, show=False)
                    self.create_global_performance_preset_window()
                    dpg.configure_item(item="global_performance_preset.gui.window", show=True)
                    dpg.focus_item("global_performance_preset.gui.window")
                    self._global_presets_buffer.clear()
                else:
                    logger.warning("Failed to add clip preset")

        with dpg.window(tag=save_global_preset_window_tag, no_title_bar=True, modal=True, width=500, height=500, no_move=True):
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
                            self._global_presets_buffer[int(i)] = (track, clip, preset) 
                            dpg.configure_item(item=f"{save_global_preset_window_tag}.menu_bar.{i}.title", label=title)
                        
                        with dpg.menu(tag=f"{save_global_preset_window_tag}.menu_bar.{i}.title", label="Select Clip Preset"):
                            for clip in track.clips:
                                if not valid(clip):
                                    continue
                                for preset in clip.presets:
                                    title = f"{clip.name}: {preset.name}"
                                    dpg.add_menu_item(label=title, callback=preset_selected, user_data=(i, title, track, clip, preset))

                        dpg.add_checkbox(tag=f"global_preset.{i}")

                with dpg.table_row():
                    dpg.add_group()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=save)
                        dpg.add_button(label="Cancel", callback=cancel)

    def play_clip_preset_callback(self, sender, app_data, user_data):
        preset = user_data
        preset.execute()
        if valid(self._active_input_channel):
            self.reset_automation_plot(self._active_input_channel)

        # Reset last button
        if self._last_selected_preset_button_tag is not None and self._last_selected_preset_theme_tag is not None:
            dpg.bind_item_theme(self._last_selected_preset_button_tag, self._last_selected_preset_theme_tag)
        
        # Set new button theme
        self._last_selected_preset_button_tag = get_preset_button_tag(preset)
        self._last_selected_preset_theme_tag = get_channel_preset_theme(preset)
        dpg.bind_item_theme(get_preset_button_tag(preset), "selected_preset.theme")

    def delete_clip_preset(self, sender, app_data, user_data):
        preset = user_data
        result = self.execute_wrapper(f"delete {preset.id}")
        if result.success:
            dpg.delete_item(get_preset_menu_bar_tag(preset))

    def add_node_menu(self, parent, clip):
        right_click_menu = "popup_menu" in dpg.get_item_alias(parent)

        with dpg.menu(parent=parent, label="Sources"):
            dpg.add_menu_item(label="Bool", callback=self.add_source_node, user_data=("create", (clip, "bool"), right_click_menu))
            dpg.add_menu_item(label="Integer", callback=self.add_source_node, user_data=("create", (clip, "int"), right_click_menu))
            dpg.add_menu_item(label="Float", callback=self.add_source_node, user_data=("create", (clip, "float"), right_click_menu))
            dpg.add_menu_item(label="Osc Integer", callback=self.add_source_node, user_data=("create", (clip, "osc_input_int"), right_click_menu))
            dpg.add_menu_item(label="Osc Float", callback=self.add_source_node, user_data=("create", (clip, "osc_input_float"), right_click_menu))
            dpg.add_menu_item(label="MIDI", callback=self.add_source_node, user_data=("create", (clip, "midi"), right_click_menu))
            dpg.add_menu_item(label="Color", callback=self.add_source_node, user_data=("create", (clip, "color"), right_click_menu))
            dpg.add_menu_item(label="Button", callback=self.add_source_node, user_data=("create", (clip, "button"), right_click_menu))

    def update_input_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.ext_set(app_data)

    def update_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.set(app_data)

    def update_channel_attr(self, sender, app_data, user_data):
        channel, attr = user_data
        setattr(channel, attr, app_data)

    @gui_lock_callback
    def add_source_node(self, sender, app_data, user_data):
        """Figure out the type then call the correct downstream add_*_source_node function."""
        action = user_data[0]
        args = user_data[1]
        if action == "restore":
            _, input_channel = args
            input_type = input_channel.input_type
        else: # create
            _, input_type = args

        if input_type in ["color", "button"]:
            self.add_standard_source_node(sender, app_data, user_data)
        else:
            self.add_automatable_source_node(sender, app_data, user_data)

    def add_standard_source_node(self, sender, app_data, user_data):
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
                
        else: # restore
            clip, input_channel = args

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y
        with dpg.node(label=input_channel.name, tag=node_tag, parent=node_editor_tag, pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0)):
            self.add_node_popup_menu(node_tag, clip, input_channel)
            parameters = getattr(input_channel, "parameters", [])
            for parameter_index, parameter in enumerate(parameters):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    if parameter.dtype == "bool":
                        dpg.add_checkbox(
                            label=parameter.name, 
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter, 
                            user_data=(input_channel, parameter_index), 
                            default_value=parameter.value
                        )
                    else:
                        dpg.add_input_text(
                            label=parameter.name, 
                            tag=f"{parameter.id}.value",
                            callback=self.update_parameter, 
                            user_data=(input_channel, parameter_index), 
                            width=70,
                            default_value=parameter.value if parameter.value is not None else "",
                            on_enter=True,
                        )

            with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel), attribute_type=dpg.mvNode_Attr_Static):
                kwargs = {}
                if input_channel.dtype == "any":
                    add_func = dpg.add_input_text
                elif input_channel.size == 1:
                    add_func = dpg.add_input_float if input_channel.dtype == "float" else dpg.add_input_int
                else:
                    add_func = dpg.add_drag_floatx 
                    kwargs["size"] = input_channel.size

                add_func(
                    label="out", 
                    tag=f"{input_channel.id}.value", 
                    width=90, 
                    on_enter=True,
                    default_value=input_channel.get(),
                    callback=self.update_input_channel_value_callback,
                    user_data=input_channel,
                    **kwargs
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
                    for other_input_channel in clip.inputs:
                        if other_input_channel.deleted:
                            continue
                        dpg.configure_item(get_source_node_window_tag(other_input_channel), show=False)
                    dpg.configure_item(get_source_node_window_tag(input_channel), show=True)
                    self._active_input_channel = input_channel

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=input_selected_callback, user_data=(clip, input_channel))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

    def add_automatable_source_node(self, sender, app_data, user_data):
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
        else: # restore
            clip, input_channel = args

        if right_click_menu:
            dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        window_x, window_y = dpg.get_item_pos(get_node_window_tag(clip))
        rel_mouse_x = self.mouse_clickr_x - window_x
        rel_mouse_y = self.mouse_clickr_y - window_y
        with dpg.node(label=input_channel.name, tag=node_tag, parent=node_editor_tag, pos=(rel_mouse_x, rel_mouse_y) if right_click_menu else (0, 0)):
            self.add_node_popup_menu(node_tag, clip, input_channel)
            parameters = getattr(input_channel, "parameters", [])
            
            # Special Min/Max Parameters
            def update_min_max_value(sender, app_data, user_data):
                clip, input_channel, parameter_index, min_max = user_data
                self.update_parameter(None, app_data, (input_channel, parameter_index))

                value = model.cast[input_channel.dtype](app_data)
                kwarg = {f"{min_max}_value": value}
                dpg.configure_item(f"{input_channel.id}.value", **kwarg)

                plot_tag = get_plot_tag(input_channel)
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.set_axis_limits(y_axis_limits_tag, input_channel.get_parameter("min").value, input_channel.get_parameter("max").value)
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
                        default_value=parameter.value if parameter.value is not None else "",
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
                        callback=self.update_parameter, 
                        user_data=(input_channel, parameter_index), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                    )

            with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel), attribute_type=dpg.mvNode_Attr_Static):
                # Input Knob
                add_func = dpg.add_drag_float if input_channel.dtype == "float" else dpg.add_drag_int
                add_func(
                    label="out", 
                    min_value=input_channel.get_parameter("min").value,
                    max_value=input_channel.get_parameter("max").value, 
                    tag=f"{input_channel.id}.value", 
                    width=75, 
                    callback=self.update_input_channel_value, 
                    user_data=input_channel
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
                        dpg.configure_item(get_source_node_window_tag(other_input_channel), show=False)
                    dpg.configure_item(get_source_node_window_tag(self._active_input_channel), show=True)
                    self.reset_automation_plot(input_channel)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=input_selected_callback, user_data=(clip, input_channel))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

    def update_input_channel_value_callback(self, sender, app_data, user_data):
        # If an input isn't connected to a node, the user can set it 
        if app_data is not None:
            input_channel = user_data
            result = self.execute_wrapper(f"update_channel_value {input_channel.id} {app_data}")
            if not result.success:
                raise RuntimeError(f"Failed to update channel value {input_channel.id}")

    def add_output_node(self, clip, output_channel):
        # This is the id used when adding links.
        attr_tag = get_node_attribute_tag(clip, output_channel)

        if dpg.does_item_exist(attr_tag):
            return

        node_tag = get_node_tag(clip, output_channel)
        with dpg.node(label="Output", tag=node_tag, parent=get_node_editor_tag(clip)):
            self.add_node_popup_menu(node_tag, clip, output_channel)

            with dpg.node_attribute(tag=attr_tag, attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="in", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="ch.", source=f"{output_channel.id}.dmx_address", width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text(source=f"{output_channel.id}.name", default_value=output_channel.name)

            # When user clicks on the output node it will populate the inspector.
            def output_selected_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)
                else:
                    self._active_output_channel = user_data

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=output_selected_callback, user_data=output_channel)
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

    def add_output_group_node(self, clip, output_channel_group):
        # This is the id used when adding links.
        node_tag = get_node_tag(clip, output_channel_group)
        if dpg.does_item_exist(node_tag):
            return

        with dpg.node(label=output_channel_group.name, tag=node_tag, parent=get_node_editor_tag(clip)):
            self.add_node_popup_menu(node_tag, clip, output_channel_group)
            for i, output_channel in enumerate(output_channel_group.outputs):
                attr_tag = get_node_attribute_tag(clip, output_channel)
                with dpg.node_attribute(tag=attr_tag, attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_int(label=output_channel.name.split(".")[-1] + f" [{output_channel.dmx_address}]", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

                # When user clicks on the output node it will populate the inspector.
                def output_selected_callback(sender, app_data, user_data):
                    # Right click menu
                    if app_data[0] == 1:
                        dpg.configure_item(f"{node_tag}.popup", show=True)
                    else:
                        self._active_output_channel_group = user_data

    def update_parameter_buffer_callback(self, sender, app_data, user_data):
        parameter, parameter_index = user_data
        if app_data is not None:
            self._properties_buffer["parameters"][parameter] = (parameter_index, app_data)

    def update_attr_buffer_callback(self, sender, app_data, user_data):
        attr_name, tag = user_data
        if app_data:
            self._properties_buffer["attrs"][attr_name] = (app_data, tag)

    def save_properties_callback(self, sender, app_data, user_data):
        obj = user_data[0]
        # Parameters
        for parameter, (parameter_index, value) in self._properties_buffer.get("parameters", {}).items():
            self.update_parameter(None, value, (obj, parameter_index))
            
        # Attributes
        for attribute_name, (value, tag) in self._properties_buffer.get("attrs", {}).items():
            setattr(obj, attribute_name, value)
            dpg.configure_item(tag, label=value)

        dpg.configure_item(get_properties_window_tag(obj), show=False)

    def create_properties_window(self, clip, obj):
        window_tag = get_properties_window_tag(obj)
        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH/3,SCREEN_HEIGHT/3),
            no_move=True,
            show=False,
            modal=True,
            popup=True,
            no_title_bar=True,
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    dpg.add_text(default_value="Type")
                    dpg.add_text(default_value=obj.nice_title)

                with dpg.table_row():
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(default_value=obj.name, callback=self.update_attr_buffer_callback, user_data=("name", get_node_tag(clip, obj)), tag=f"{obj.id}.name")

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
                                    default_value=parameter.value if parameter.value is not None else "",
                                )

                with dpg.table_row():
                    dpg.add_table_cell()
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Save", callback=self.save_properties_callback, user_data=(obj,))
                        
                        def cancel_properties_callback(sender, app_data, user_data):
                            obj = user_data
                            for parameter in obj.parameters:
                                dpg.set_value(f"{parameter.id}.value", parameter.value)
                            dpg.configure_item(window_tag, show=False)
                            dpg.set_value(f"{obj.id}.name", obj.name)
                        dpg.add_button(label="Cancel", callback=cancel_properties_callback, user_data=obj)

    def add_node_popup_menu(self, node_tag, clip, obj):
        def show_properties_window(sender, app_data, user_data):
            self._properties_buffer.clear()
            dpg.configure_item(get_properties_window_tag(user_data), show=True)

        with dpg.popup(parent=node_tag, tag=f"{node_tag}.popup", mousebutton=1):
            dpg.add_menu_item(label="Properties", callback=show_properties_window, user_data=obj)
            if isinstance(obj, model.MidiInput):
                dpg.add_menu_item(label="Update MIDI Map", callback=self.update_midi_map_node, user_data=obj)
                dpg.add_menu_item(label="Learn Input MIDI Map", callback=self.learn_midi_map_node, user_data=(obj, "input"))
                dpg.add_menu_item(label="Learn Output MIDI Map", callback=self.learn_midi_map_node, user_data=(obj, "output"))

                def unmap_midi(sender, app_data, user_data):
                    obj = user_data
                    result = self.execute_wrapper(f"unmap_midi {obj.id}")
                    if result.success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                        dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                dpg.add_menu_item(label="Clear MIDI Map", callback=unmap_midi, user_data=obj)
            if isinstance(obj, (model.SourceNode,)):
                dpg.add_menu_item(label="Delete", callback=self.delete_selected_nodes, user_data=clip)

    def update_midi_map_node(self, sender, app_data, user_data):
        result = self.execute_wrapper(f"midi_map {user_data.id}")
        if not result.success:
            raise RuntimeError("Failed to map midi")

    def learn_midi_map_node(self, sender, app_data, user_data):
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
                    self.update_parameter_by_name(obj, "id", f"{message.channel}/{note_control}")
                    result = self.execute_wrapper(f"midi_map {obj.id}")
                    if result.success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                        dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                        dpg.delete_item("midi_map_window")
                    else:
                        raise RuntimeError("Failed to map midi")
                else: #output
                    input_midi_device_name = device_name
                    while input_midi_device_name:
                        for i, output_device in model.MIDI_OUTPUT_DEVICES.items():
                            if output_device.device_name.startswith(input_midi_device_name):
                                output_device.map_channel(message.channel, note_control, obj)
                                logger.debug(f"Mapping {(message.channel, note_control)} to {output_device.device_name}")
                                dpg.delete_item("midi_map_window")
                                return
                        input_midi_device_name = input_midi_device_name[:-1]
                    logger.warning(f"Failed to find corresponding output MIDI device for {input_midi_device_name}")

        dpg.set_value("last_midi_message", "")

        with dpg.window(tag="midi_map_window", modal=True, width=300, height=300, no_move=True):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=save, user_data=obj)
                dpg.add_button(label="Cancel", callback=cancel, user_data=obj)

    def update_parameter(self, sender, app_data, user_data):
        if app_data is not None:
            obj, parameter_index = user_data
            result = self.execute_wrapper(f"update_parameter {obj.id} {parameter_index} {app_data}")
            if not result.success:
                raise RuntimeError("Failed to update parameter")
            if obj.parameters[parameter_index].name == "code":
                dpg.set_value(f"{obj.parameters[parameter_index].id}.value", obj.parameters[parameter_index].value.replace("[NEWLINE]", "\n"))
            else:
                dpg.set_value(f"{obj.parameters[parameter_index].id}.value", obj.parameters[parameter_index].value)
            return result.success

    def update_parameter_by_name(self, obj, parameter_name, value):
        obj.get_parameter(parameter_name).value = value

    def toggle_automation_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "manual" if input_channel.mode == "automation" else "automation"
    
    def enable_recording_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "armed"

    def create_code_editor_window(self, obj):
        tag = get_code_window_tag(obj)
        self.tags["hide_on_clip_selection"].append(tag)
        window = dpg.window(tag=tag, label="Code Editor", pos=(801,18), width=SCREEN_WIDTH-800, height=520, no_move=True, no_title_bar=True, no_resize=True)
        
        with window:
            def save():
                self.save_menu_callback()
                self._active_clip.code.reload()
                self._active_track.code.reload()
                self.state.code.reload()

            def hide_code_windows():
                for tag in self.tags["hide_on_clip_selection"]:
                    if "code_editor" in tag:
                        dpg.configure_item(tag, show=False)

            def show_global(sender, app_data, user_data):
                self.code_view = GLOBAL_VIEW
                hide_code_windows()
                dpg.configure_item(get_code_window_tag(self.state), show=True)

            def show_track(sender, app_data, user_data):
                self.code_view = TRACK_VIEW
                hide_code_windows()
                if valid(self._active_track):
                    dpg.configure_item(get_code_window_tag(self._active_track), show=True)
                
            def show_clip(sender, app_data, user_data):
                self.code_view = CLIP_VIEW
                hide_code_windows()
                if valid(self._active_clip):
                    dpg.configure_item(get_code_window_tag(self._active_clip), show=True)
                
            with dpg.group(horizontal=True, height=20):
                dpg.add_button(label="Save", callback=save)
                dpg.add_button(tag=tag + ".button.global", label="Global", callback=show_global)
                dpg.add_button(tag=tag + ".button.clip", label="Clip", callback=show_clip)
                dpg.add_button(tag=tag + ".button.track", label="Track", callback=show_track)

            dpg.add_input_text(tag=f"{tag}.text", default_value=obj.code.read(), multiline=True, readonly=False, tab_input=True, width=SCREEN_WIDTH-800-35, height=SCREEN_HEIGHT-520-70, on_enter=False)

        dpg.bind_item_theme(tag + ".button.track", "code_editor.track.theme")
        dpg.bind_item_theme(tag + ".button.clip", "code_editor.clip.theme")
        dpg.bind_item_theme(tag + ".button.global", "code_editor.global.theme")
        
        if isinstance(obj, model.Track):
            dpg.bind_item_theme(tag + ".text", "code_editor.track.theme")
        elif isinstance(obj, model.Clip):
            dpg.bind_item_theme(tag + ".text", "code_editor.clip.theme")
        else:
            dpg.bind_item_theme(tag + ".text", "code_editor.global.theme")


    def default_time_callback(self, sender, app_data, user_data):
        user_data.speed = 0

    def double_time_callback(self, sender, app_data, user_data):
        user_data.speed += 1

    def half_time_callback(self, sender, app_data, user_data):
        user_data.speed -= 1

    def create_automation_window(self, clip, input_channel):
        parent = get_source_node_window_tag(input_channel)
        with dpg.window(
            tag=parent,
            label=f"Automation Window",
            width=1120,
            height=520,
            pos=(799, 18),
            show=False,
            no_move=True,
            no_title_bar=True,

        ) as window:
            self.tags["hide_on_clip_selection"].append(parent)

            automation = input_channel.active_automation

            series_tag = f"{input_channel.id}.series"
            plot_tag = get_plot_tag(input_channel)
            playhead_tag = f"{input_channel.id}.gui.playhead"
            ext_value_tag = f"{input_channel.id}.gui.ext_value"
            menu_tag = f"{input_channel.id}.menu"

            with dpg.menu_bar(tag=menu_tag):

                dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_enable_button", label="Disable" if input_channel.mode == "automation" else "Enable", callback=self.toggle_automation_mode, user_data=input_channel)
                #dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_record_button", label="Record", callback=self.enable_recording_mode, user_data=input_channel)

                preset_menu_tag = f"{input_channel.id}.preset_menu"
                with dpg.menu(tag=preset_menu_tag, label="Automation"):
                    dpg.add_menu_item(label="New Automation", callback=self.add_preset, user_data=input_channel)
                    dpg.add_menu_item(label="Reorder", callback=self.create_and_show_reorder_window, user_data=(input_channel.automations, preset_menu_tag, get_preset_sub_menu_tag))
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

                    preset_menu_tag = f"{input_channel.id}.preset_menu"
                    preset_sub_menu_tag = get_preset_sub_menu_tag(automation)
                    dpg.configure_item(preset_sub_menu_tag, label=app_data)

                prop_x_start = 600
                dpg.add_text("Preset:", pos=(prop_x_start-200, 0))
                dpg.add_input_text(tag=f"{parent}.preset_name", label="", default_value="", pos=(prop_x_start-150, 0), on_enter=True, callback=update_preset_name, user_data=input_channel, width=100)
                
                dpg.add_text("Beats:", pos=(prop_x_start+200, 0))
                dpg.add_input_text(tag=f"{parent}.beats", label="", default_value=input_channel.active_automation.length, pos=(prop_x_start+230, 0), on_enter=True, callback=update_automation_length, user_data=input_channel, width=50)

            with dpg.plot(label=input_channel.active_automation.name, height=-1, width=-1, tag=plot_tag, query=True, callback=self.print_callback, anti_aliased=True, no_menus=True):
                min_value = input_channel.get_parameter("min").value
                max_value = input_channel.get_parameter("max").value
                x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
                y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                dpg.add_plot_axis(dpg.mvXAxis, label="x", tag=x_axis_limits_tag, no_gridlines=True)
                dpg.set_axis_limits(dpg.last_item(), AXIS_MARGIN, input_channel.active_automation.length)

                dpg.add_plot_axis(dpg.mvYAxis, label="y", tag=y_axis_limits_tag, no_gridlines=True)
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
                    dpg.add_menu_item(label="Double Automation", callback=self.double_automation)
                    dpg.add_menu_item(label="Duplicate Preset", callback=self.duplicate_channel_preset)
                    with dpg.menu(label="Set Quantize"):
                        dpg.add_menu_item(label="Off", callback=self.set_quantize, user_data=None)
                        dpg.add_menu_item(label="1 bar", callback=self.set_quantize, user_data=4)
                        dpg.add_menu_item(label="1/2", callback=self.set_quantize, user_data=2)
                        dpg.add_menu_item(label="1/4", callback=self.set_quantize, user_data=1)
                        dpg.add_menu_item(label="1/8", callback=self.set_quantize, user_data=0.5)
                        dpg.add_menu_item(label="1/16", callback=self.set_quantize, user_data=0.25)
                    with dpg.menu(label="Shift (Beats)"):
                        with dpg.menu(label="Left"):
                            dpg.add_menu_item(label="4", callback=self.shift_points, user_data=-4)
                            dpg.add_menu_item(label="2", callback=self.shift_points, user_data=-2)
                            dpg.add_menu_item(label="1", callback=self.shift_points, user_data=-1)
                            dpg.add_menu_item(label="1/2", callback=self.shift_points, user_data=-0.5)
                            dpg.add_menu_item(label="1/4", callback=self.shift_points, user_data=-0.25)
                        with dpg.menu(label="Right"):
                            dpg.add_menu_item(label="4", callback=self.shift_points, user_data=4)
                            dpg.add_menu_item(label="2", callback=self.shift_points, user_data=2)
                            dpg.add_menu_item(label="1", callback=self.shift_points, user_data=1)
                            dpg.add_menu_item(label="1/2", callback=self.shift_points, user_data=0.5)
                            dpg.add_menu_item(label="1/4", callback=self.shift_points, user_data=0.25)
                    with dpg.menu(label="Interpolation Mode"):
                        dpg.add_menu_item(label="Linear", callback=self.set_interpolation, user_data="linear")
                        dpg.add_menu_item(label="Nearest", callback=self.set_interpolation, user_data="nearest")
                        dpg.add_menu_item(label="Nearest Up", callback=self.set_interpolation, user_data="nearest-up")
                        dpg.add_menu_item(label="Zero", callback=self.set_interpolation, user_data="zero")
                        dpg.add_menu_item(label="S-Linear", callback=self.set_interpolation, user_data="slinear")
                        dpg.add_menu_item(label="Quadratic", callback=self.set_interpolation, user_data="quadratic")
                        dpg.add_menu_item(label="Cubic", callback=self.set_interpolation, user_data="cubic")
                        dpg.add_menu_item(label="Previous", callback=self.set_interpolation, user_data="previous")
                        dpg.add_menu_item(label="Next", callback=self.set_interpolation, user_data="next")

            dpg.bind_item_theme(playhead_tag, "playhead_line.theme")
            dpg.bind_item_theme(ext_value_tag, "bg_line.theme")
            dpg.bind_item_theme(series_tag, "automation_line.theme")

            def show_popup(sender, app_data, user_data):
                input_channel = user_data
                popup_tag = f"{input_channel.id}.gui.popup"
                # Right click
                if app_data[0] == 1:
                    dpg.configure_item(item=popup_tag, show=True)            

    def create_source_node_window(self, clip, input_channel):
        parent = get_source_node_window_tag(input_channel)
        self.tags["hide_on_clip_selection"].append(parent)
        
        input_type = input_channel.input_type

        # ColorNode
        if input_type == "color":
            def update_color(sender, app_data, user_data):
                # Update the Channel value
                self.update_channel_value(sender, app_data, user_data)

                # Update the node's color
                rgb = [clamp(v*255, 0, 255) for v in app_data]
                node_theme = get_node_tag(clip, input_channel) + ".theme"
                dpg.configure_item(f"{node_theme}.color1", value=rgb)
                dpg.configure_item(f"{node_theme}.color2", value=rgb)
                dpg.configure_item(f"{node_theme}.color3", value=rgb)

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
                        dpg.add_theme_color(tag=f"{node_theme}.color1", target=dpg.mvNodeCol_NodeBackground, value=default_color, category=dpg.mvThemeCat_Nodes)
                        dpg.add_theme_color(tag=f"{node_theme}.color2", target=dpg.mvNodeCol_NodeBackgroundHovered, value=default_color, category=dpg.mvThemeCat_Nodes)
                        dpg.add_theme_color(tag=f"{node_theme}.color3", target=dpg.mvNodeCol_NodeBackgroundSelected, value=default_color, category=dpg.mvThemeCat_Nodes)
                dpg.bind_item_theme(get_node_tag(clip, input_channel), node_theme)
                dpg.add_color_picker(width=height*0.8, height=height, callback=update_color, user_data=input_channel, default_value=default_color)
        
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

    def shift_points(self, sender, app_data, user_data):
        if valid(self._active_input_channel.active_automation):
            self._active_input_channel.active_automation.shift_points(user_data)
        self.reset_automation_plot(self._active_input_channel)

    def set_quantize(self, sender, app_data, user_data):
        self._quantize_amount = user_data
        self.reset_automation_plot(self._active_input_channel)

    def set_interpolation(self, sender, app_data, user_data):
        if valid(self._active_input_channel.active_automation):
            self._active_input_channel.active_automation.set_interpolation(user_data)
        self.reset_automation_plot(self._active_input_channel)

    def double_automation(self):
        if self._active_input_channel is None:
            return
        
        automation = self._active_input_channel.active_automation
        if automation is None:
            return

        result = self.execute_wrapper(f"double_automation {automation.id}")
        if result.success:
            self.reset_automation_plot(self._active_input_channel)

    def duplicate_channel_preset(self):
        if self._active_input_channel is None:
            return
        
        automation = self._active_input_channel.active_automation
        if automation is None:
            return

        result = self.execute_wrapper(f"duplicate_channel_preset {self._active_input_channel.id} {automation.id}")
        if result.success:
            automation = result.payload
            self.add_automation_tab(self._active_input_channel, automation)
            self.select_automation(None, None, (self._active_input_channel, automation))

    
    def delete_automation(self, sender, app_data, user_data):
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

    def add_preset(self, sender, app_data, user_data):
        input_channel = user_data
        result = self.execute_wrapper(f"add_automation {input_channel.id}")
        if result.success:
            automation = result.payload
            self.add_automation_tab(input_channel, automation)
            self.reset_automation_plot(input_channel)

    def add_automation_tab(self, input_channel, automation):
        preset_menu_tag = f"{input_channel.id}.preset_menu"
        preset_sub_menu_tag = get_preset_sub_menu_tag(automation)
        with dpg.menu(parent=preset_menu_tag, tag=preset_sub_menu_tag, label=automation.name):
            dpg.add_menu_item(tag=f"{preset_sub_menu_tag}.activate", label="Activate", callback=self.select_automation, user_data=(input_channel, automation))
            dpg.add_menu_item(tag=f"{preset_sub_menu_tag}.delete", label="Delete", callback=self.delete_automation, user_data=(input_channel, automation))

    def select_automation(self, sender, app_data, user_data):
        input_channel, automation = user_data
        result = self.execute_wrapper(f"set_active_automation {input_channel.id} {automation.id}")
        if result.success:
            self.reset_automation_plot(input_channel)

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
        dpg.set_axis_limits(x_axis_limits_tag, -AXIS_MARGIN, input_channel.active_automation.length+AXIS_MARGIN)

        dpg.set_value(f"{window_tag}.beats", value=automation.length)
        dpg.set_value(f"{window_tag}.preset_name", value=automation.name)

        # Always delete and redraw all the points
        for tag in self.gui_state["point_tags"]:
            dpg.delete_item(tag)
    
        self.gui_state["point_tags"].clear()
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
            self.gui_state["point_tags"].append(point_tag)

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
            for i in range(n_bars+1):
                tag = f"gui.quantization_series.{i}"
                value = i * self._quantize_amount
                dpg.add_line_series(
                    x=[value, value],
                    y=y_limits,
                    tag=tag,
                    parent=y_axis_limits_tag,
                )
                dpg.bind_item_theme(tag, "bg_line.theme")

    def create_inspector_window(self):
        with dpg.window(
            label=f"Inspector",
            width=750,
            height=600,
            pos=(810, 0),
            show=False,
            tag="inspector.gui.window"
        ) as window:
            with dpg.plot(label="Inspector", height=-1, width=-1):
                dpg.add_plot_axis(dpg.mvXAxis, label="x")
                dpg.set_axis_limits(dpg.last_item(), 0, len(self._inspecter_x))

                dpg.add_plot_axis(dpg.mvYAxis, label="y")
                dpg.add_line_series(
                    [],
                    [],
                    tag="inspector.series",
                    parent=dpg.last_item(),
                )

    def create_io_window(self):
        with dpg.window(
            label=f"I/O",
            width=750,
            height=300,
            pos=(1180, 0),
            tag="io.gui.window",
            show=False,
        ) as window:
            output_table_tag = f"io.outputs.table"
            input_table_tag = f"io.inputs.table"

            def set_io_type(sender, app_data, user_data):
                index, io_type, input_output, *args = user_data
                table_tag = f"io.{input_output}.table"
                self.gui_state["io_types"][input_output][index] = io_type.type
                dpg.configure_item(f"{table_tag}.{index}.type", label=io_type.nice_title)
                if not dpg.get_value(f"{table_tag}.{index}.arg"):
                    dpg.set_value(f"{table_tag}.{index}.arg", value=io_type.arg_template if not args else args[0])

                if args:
                    create_io(None, args[0], ("create", index, input_output))

            def create_io(sender, app_data, user_data):
                arg = app_data
                action = user_data[0]
                if action == "create":
                    _, index, input_output = user_data
                    io_type = self.gui_state["io_types"][input_output][index]
                    result = self.execute_wrapper(f"create_io {index} {input_output} {io_type} {arg}")
                    if not result.success:
                        raise RuntimeError("Failed to create IO")
                    io = result.payload
                    self.gui_state["io_args"][input_output][index] = arg
                else: # restore
                    _, index, io = user_data

                table_tag = f"io.{input_output}.table"
                dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io.args)

            def connect(sender, app_data, user_data):
                _, index, input_output = user_data
                
                result = self.execute_wrapper(f"connect_io {index} {input_output}")
                if not result.success:
                    raise RuntimeError("Failed to create IO")

                io = result.payload
                table_tag = f"io.{input_output}.table"
                dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io.args)

            with dpg.table(header_row=True, tag=input_table_tag, policy=dpg.mvTable_SizingStretchProp):
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
                        dpg.add_button(label="Select Input Type" if input_type is None else input_type.nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_input_type in model.ALL_INPUT_TYPES:
                                if io_input_type.type == "midi_input":
                                    with dpg.menu(label="MIDI"):
                                        for device_name in mido.get_input_names():
                                            dpg.add_menu_item(label=device_name, callback=set_io_type, user_data=(i, io_input_type, "inputs", device_name))
                                else:
                                    dpg.add_menu_item(label=io_input_type.nice_title, callback=set_io_type, user_data=(i, io_input_type, "inputs"))
                        
                        arg_tag = f"{input_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=create_io, user_data=("create", i, "inputs"))

                        connected_tag = f"{input_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "inputs"))

                        dpg.add_table_cell()

            with dpg.table(header_row=True, tag=output_table_tag, policy=dpg.mvTable_SizingStretchProp):
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
                        dpg.add_button(label="Select Output Type" if self.state.io_outputs[i] is None else self.state.io_outputs[i].nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_output_type in model.ALL_OUTPUT_TYPES:
                                if io_output_type.type == "midi_output":
                                    with dpg.menu(label="MIDI"):
                                        for device_name in mido.get_output_names():
                                            dpg.add_menu_item(label=device_name, callback=set_io_type, user_data=(i, io_output_type, "outputs", device_name))
                                else:
                                    dpg.add_menu_item(label=io_output_type.nice_title, callback=set_io_type, user_data=(i, io_output_type, "outputs"))

                        arg_tag = f"{output_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=create_io, user_data=("create", i, "outputs"))
                        
                        connected_tag = f"{output_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "outputs"))

                        dpg.add_table_cell()

    def create_and_show_all_automation_window(self, sender, app_data, user_data):
        clip = user_data
        try:
            pos = dpg.get_item_pos("all_automation.gui.window")
            dpg.delete_item("all_automation.gui.window")
        except Exception as e:
            pos = (100, 100)

        def activate_automation(sender, app_data, user_data):
            clip, channel, automation = user_data
            channel.set_active_automation(automation)

            for channel in clip.inputs:
                if not isinstance(channel, model.AutomatableSourceNode):
                    continue
                for automation in channel.automations:
                    dpg.bind_item_theme(get_automation_button_tag(automation), "selected_preset.theme" if channel.active_automation == automation else "not_selected_preset.theme")

        with dpg.window(
            label=f"All Automation",
            width=600,
            height=400,
            pos=pos,
            show=True,
            tag="all_automation.gui.window"
        ) as window:
            dpg.add_button(label="Refresh", callback=self.create_and_show_all_automation_window, user_data=clip)
            with dpg.table(tag="all_automation.table"):

                for channel in clip.inputs:
                    if not isinstance(channel, model.AutomatableSourceNode):
                        continue
                    dpg.add_table_column(label=channel.name)

                with dpg.table_row():
                    for channel in clip.inputs:
                        if not isinstance(channel, model.AutomatableSourceNode):
                            continue
                        with dpg.table_cell():
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="1", callback=self.default_time_callback, user_data=channel)
                                dpg.add_button(label="x2", callback=self.double_time_callback, user_data=channel)
                                dpg.add_button(label="/2", callback=self.half_time_callback, user_data=channel)
                            for automation in channel.automations:
                                #dpg.add_button(tag=get_preset_button_tag(preset), label=preset.name, callback=activate_automation, user_data=(track, clip, preset))
                                dpg.add_button(tag=get_automation_button_tag(automation), label=automation.name, callback=activate_automation, user_data=(clip, channel, automation))
                                dpg.bind_item_theme(dpg.last_item(), "selected_preset.theme" if channel.active_automation == automation else "not_selected_preset.theme")

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

            dpg.move_item(get_obj_tag_func(container[i1]), parent=parent_tag, before=get_obj_tag_func(container[i2]))

            self.create_and_show_reorder_window(sender, app_data, user_data)

        with dpg.window(
            label=f"Reorder",
            width=800,
            height=800,
            pos=pos,
            tag="reorder.gui.window"
        ) as window:
            with dpg.table(tag="", header_row=False):
                dpg.add_table_column()
                dpg.add_table_column()
                for i, obj in enumerate(container):
                    with dpg.table_row():
                        dpg.add_text(default_value=obj.name)
                        with dpg.group(horizontal=True):
                            dpg.add_button(label=" - ", callback=move, user_data=(container, i, i-1))
                            dpg.add_button(label=" + ", callback=move, user_data=(container, i, i+1))

    def create_and_show_performance_preset_window(self):
        try:
            pos = dpg.get_item_pos("performance_preset.gui.window")
            dpg.delete_item("performance_preset.gui.window")
        except Exception as e:
            pos = (100, 100)

        def play_clip_and_preset(sender, app_data, user_data):
            track, clip, preset = user_data
            self.play_clip_callback(None, None, (track, clip))
            self.play_clip_preset_callback(None, None, preset)
            SelectClip({"track":track, "clip":clip}).execute()

        with dpg.window(
            label=f"Clip Presets",
            width=800,
            height=800,
            pos=pos,
            show=True,
            tag="performance_preset.gui.window"
        ) as window:
            dpg.add_button(label="Refresh", callback=self.create_and_show_performance_preset_window)
            with dpg.table(tag="performance_preset.table"):

                for i, track in enumerate(self.state.tracks):
                    dpg.add_table_column(label=track.name)

                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    with dpg.table_row():
                        for track_i, track in enumerate(self.state.tracks):
                            clip = track.clips[clip_i]
                            with dpg.table_cell():
                                if clip is None:
                                    with dpg.group():
                                        pass
                                else:
                                    with dpg.group(tag=f"{clip.id}.performance_preset_window.group"):
                                        if not clip.presets:
                                            continue
                                        dpg.add_text(source=f"{clip.id}.name")
                                        for preset in clip.presets:
                                            dpg.add_button(tag=get_preset_button_tag(preset), label=preset.name, callback=play_clip_and_preset, user_data=(track, clip, preset))
                                            dpg.bind_item_theme(dpg.last_item(), get_channel_preset_theme(preset))

    def create_global_performance_preset_window(self):
        try:
            dpg.delete_item("global_performance_preset.gui.window")
        except Exception as e:
            pass

        def play_global_clip_preset(sender, app_data, user_data):
            global_preset = user_data
            global_preset.execute()

        with dpg.window(
            label=f"Global Presets",
            width=800,
            height=800,
            pos=(100, 100),
            show=False,
            tag="global_performance_preset.gui.window"
        ) as window:
            with dpg.menu_bar():
                dpg.add_menu_item(label="New Global Preset", callback=self.create_and_show_new_global_presets_window)

            with dpg.table(tag="global_performance_preset.table"):
                dpg.add_table_column(label="Preset")
                for global_preset in self.state.global_presets:
                    with dpg.table_row():
                        dpg.add_button(label=global_preset.name, callback=play_global_clip_preset, user_data=global_preset)

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
            tag="sequences.gui.window"
        ) as window:
            with dpg.table(tag="sequences.table"):
                for i, track in enumerate(self.state.tracks):
                    dpg.add_table_column(label=track.name)

                with dpg.table_row():
                    for track in self.state.tracks:
                        with dpg.group():
                            dpg.add_menu_item(label="New Sequence", callback=self.create_and_show_new_sequences_window, user_data=(track, None))
                            dpg.add_menu_item(label="Reorder", callback=self.create_and_show_reorder_window, user_data=(track.sequences, get_sequences_group_tag(track), get_sequence_button_tag))

                def start_sequence(sender, app_data, user_data):
                    track, sequence = user_data
                    track.sequence = sequence
                    self.state.start()

                with dpg.table_row():
                    for track in self.state.tracks:
                        with dpg.group(tag=get_sequences_group_tag(track)):
                            for sequence in track.sequences:
                                dpg.add_button(tag=get_sequence_button_tag(sequence), label=sequence.name, callback=start_sequence, user_data=(track, sequence))
                                with dpg.popup(dpg.last_item()):
                                    dpg.add_menu_item(label="Edit", callback=self.create_and_show_new_sequences_window, user_data=(track, sequence))

    def create_and_show_new_sequences_window(self, sender, app_data, user_data):
        track, sequence = user_data
        editing = valid(sequence)

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
                data = json.dumps({"sequence_info":sequence_info, "name":name, "track":track.id, "sequence_id":sequence.id if editing else None})

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
            dpg.configure_item(item=f"{new_sequence_window}.menu_bar.{i}.title", label=title)

        def set_duration(sender, app_data, user_data):
            duration = app_data
            i = user_data
            self._new_sequence_duration[int(i)] = duration
            dpg.set_value(f"{new_sequence_window}.{i}.duration", duration)

        self._n_sequence_rows = 0
        with dpg.window(label="New Sequence", tag=new_sequence_window, no_title_bar=True, modal=True, width=500, height=500, no_move=True, pos=(500, 500)):    

            def add_rows(sender, app_data, callback):
                final_n_rows = app_data

                if self._n_sequence_rows < final_n_rows:
                    for i in range(self._n_sequence_rows, final_n_rows):
                        with dpg.table_row(parent=f"{new_sequence_window}.table", before=f"{new_sequence_window}.table.save_cancel_row"):
                            self._new_sequence_duration[int(i)] = DEFAULT_SEQUENCE_DURATION 

                            with dpg.menu(tag=f"{new_sequence_window}.menu_bar.{i}.title", label="Select Clip Preset"):
                                for clip in track.clips:
                                    if not valid(clip):
                                        continue
                                    for preset in clip.presets:
                                        title = f"{clip.name}: {preset.name}"
                                        dpg.add_menu_item(label=title, callback=preset_selected, user_data=(i, title, clip, preset))                            

                            dpg.add_input_int(tag=f"{new_sequence_window}.{i}.duration", default_value=DEFAULT_SEQUENCE_DURATION, callback=set_duration, user_data=i)
                        
                        self._n_sequence_rows += 1

            with dpg.table(tag=f"{new_sequence_window}.table", header_row=False, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(width=100)
                dpg.add_table_column()
                dpg.add_table_column()

                with dpg.table_row():
                    dpg.add_text(default_value="Sequence Name")
                    dpg.add_input_text(tag="sequence.name", default_value=sequence.name if editing else "")

                with dpg.table_row():
                    dpg.add_text(default_value="Num. Entries ")
                    dpg.add_input_int(default_value=len(sequence.sequence_info) if editing else 1, callback=add_rows, on_enter=True)

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

    def create_track_output_configuration_window(self, track, show=False):
        window_tag = get_output_configuration_window_tag(track)
        with dpg.window(
            tag=window_tag,
            label=f"Output Configuration",
            width=400,
            height=SCREEN_HEIGHT * 5/6,
            pos=(799,60),
            show=show,
        ) as window:
            output_table_tag = f"{window_tag}.output_table"

            with dpg.group(horizontal=True):
                def set_track_title_button_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        track.name = app_data
                        dpg.set_value(user_data, track.name)
                track_title_tag = f"{track.id}.gui.button"
                dpg.add_input_text(tag=f"{track.id}.name", default_value=track.name, user_data=track_title_tag, callback=set_track_title_button_text, width=75)

                dpg.add_button(
                    label="Add Output",
                    callback=self.add_track_output,
                    user_data=("create", track)
                )
                dpg.add_button(label="Add Fixture")    
                with dpg.popup(dpg.last_item(), mousebutton=0):
                    for fixture in fixtures.FIXTURES:
                        dpg.add_menu_item(label=fixture.name, callback=self.add_fixture, user_data=(track, fixture))

                    def open_fixture_dialog():
                        dpg.configure_item("open_fixture_dialog", show=True)
                    dpg.add_menu_item(label="Custom", callback=open_fixture_dialog)

            with dpg.table(header_row=True, tag=output_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="DMX Ch.", tag=f"{output_table_tag}.column.dmx_address")
                dpg.add_table_column(label="Name", tag=f"{output_table_tag}.column.name")
                dpg.add_table_column(tag=f"{output_table_tag}.column.delete", width=10)

        ###############
        ### Restore ###
        ###############
        for output_index, output_channel in enumerate(track.outputs):
            if output_channel.deleted:
                continue
            if isinstance(output_channel, model.DmxOutputGroup):
                self.add_track_output_group(sender=None, app_data=None, user_data=("restore", track, output_channel))
            else:
                self.add_track_output(sender=None, app_data=None, user_data=("restore", track, output_channel))

    def create_rename_window(self):
        # Rename popup window
        with dpg.window(tag="rename_node.popup", label="Rename", no_title_bar=True, no_background=False, modal=False, show=False, autosize=True, pos=(2*SCREEN_WIDTH/5, SCREEN_HEIGHT/3)):
            def set_name_property(sender, app_data, user_data):
                if self._active_clip is not None and app_data:
                    node_editor_tag = get_node_editor_tag(self._active_clip)
                    items = dpg.get_selected_nodes(node_editor_tag)
                    # Renaming a node
                    if items:
                        item = items[0]
                        alias = dpg.get_item_alias(item)
                        node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                        obj = self.state.get_obj(node_id)
                        obj.name = app_data
                        dpg.configure_item(get_node_tag(self._active_clip, obj), label=obj.name)
                        dpg.set_value(f"{obj.id}.name", obj.name)
                    # Renaming a clip
                    else:
                        self._active_clip.name = app_data
                        dpg.set_value(f"{self._active_clip.id}.name", app_data)
                dpg.configure_item("rename_node.popup", show=False)
            dpg.add_input_text(tag="rename_node.text", on_enter=True, callback=set_name_property)

    def add_track_output(self, sender, app_data, user_data):  
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            address = user_data[2] if len(user_data) == 3   else 1
            result = self.execute_wrapper(f"create_output {track.id} {address}")
            if not result.success:
                return
            output_channel = result.payload
        else: # restore
            output_channel = user_data[2]

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(tag=f"{output_channel.id}.dmx_address", width=75, default_value=output_channel.dmx_address, callback=self.update_channel_attr, user_data=(output_channel, "dmx_address"))
            dpg.add_input_text(tag=f"{output_channel.id}.name", default_value=output_channel.name, callback=self.update_channel_attr, user_data=(output_channel, "name"), width=150)
            dpg.add_button(label="X", callback=self._delete_track_output, user_data=(track, output_channel))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_node(clip, output_channel)

    def add_track_output_group(self, sender, app_data, user_data):
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            starting_address = user_data[2]
            group_name = user_data[3]
            channel_names = user_data[4]
            result = self.execute_wrapper(f"create_output_group {track.id} {starting_address} {group_name} {','.join(channel_names)}")
            if not result.success:
                return
            output_channel_group = result.payload
        else: # restore
            output_channel_group = user_data[2]

        def update_channel_group_address(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_starting_address(app_data)

        def update_channel_group_name(sender, app_data, user_data):
            output_channel_group = user_data
            output_channel_group.update_name(app_data)
            if valid(self._active_clip):
                dpg.configure_item(get_node_tag(self._active_clip, output_channel_group), label=app_data)

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel_group.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(tag=f"{output_channel_group.id}.dmx_address", width=75, default_value=output_channel_group.dmx_address, callback=update_channel_group_address, user_data=output_channel_group)
            dpg.add_input_text(tag=f"{output_channel_group.id}.name", default_value=output_channel_group.name, callback=update_channel_group_name, user_data=output_channel_group, width=150)
            dpg.add_button(label="X", callback=self._delete_track_output_group, user_data=(track, output_channel_group))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_group_node(clip, output_channel_group)

    def add_fixture(self, sender, app_data, user_data):
        track = user_data[0]
        fixture = user_data[1]
        starting_address = fixture.address

        for output_channel in track.outputs:
            starting_address = max(starting_address, output_channel.dmx_address + 1)

        self.add_track_output_group(None, None, ("create", track, starting_address, fixture.name, fixture.channels))

    ###

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


    @gui_lock_callback
    def _delete_track_output(self, _, __, user_data):
        track, output_channel = user_data
        # Delete the entire window, since we will remake it later.
        parent = get_output_configuration_window_tag(track)
        dpg.delete_item(parent)

        result = self.execute_wrapper(f"delete {output_channel.id}")
        if result.success:
            # Delete each Node from each clip's node editor
            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue
                self._delete_node_gui(get_node_tag(clip, output_channel), output_channel.id)

            # Remake the window
            self.create_track_output_configuration_window(track, show=True)
        else:
            RuntimeError(f"Failed to delete: {output_channel.id}")

    @gui_lock_callback
    def _delete_track_output_group(self, _, __, user_data):
        track, output_channel_group = user_data
        # Delete the entire window, since we will remake it later.
        parent = get_output_configuration_window_tag(track)
        dpg.delete_item(parent)

        result = self.execute_wrapper(f"delete {output_channel_group.id}")
        if result.success:
            # Delete each Node from each clip's node editor
            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue
                self._delete_node_gui(get_node_tag(clip, output_channel_group), output_channel_group.id)

            # Remake the window
            self.create_track_output_configuration_window(track, show=True)
        else:
            RuntimeError(f"Failed to delete: {output_channel_group.id}")
    
    @gui_lock_callback
    def delete_selected_nodes(self, sender, app_data, user_data):
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

        for item in dpg.get_selected_links(node_editor_tag):
            alias = dpg.get_item_alias(item)
            link_key = alias.replace(".gui.link", "")
            self._delete_link(alias, link_key, self._active_clip)

    def copy_node_position(self, from_clip, from_obj, to_clip, to_obj):
        from_pos = dpg.get_item_pos(get_node_tag(from_clip, from_obj))
        dpg.set_item_pos(get_node_tag(to_clip, to_obj), from_pos)

    def copy_selected(self):
        window_tag_alias = dpg.get_item_alias(dpg.get_active_window())
        if window_tag_alias is None:
            return

        new_copy_buffer = []
        if window_tag_alias.endswith("node_window"):
            node_editor_tag = get_node_editor_tag(self._active_clip)
            for item in dpg.get_selected_nodes(node_editor_tag):                    
                alias = dpg.get_item_alias(item)
                item_id = alias.replace(".node", "").rsplit(".", 1)[-1]
                obj = self.state.get_obj(item_id)
                if isinstance(obj, (model.DmxOutputGroup, model.DmxOutput)):
                    continue
                new_copy_buffer.append(obj)
            for item in dpg.get_selected_links(node_editor_tag):
                alias = dpg.get_item_alias(item)
                link_key = alias.replace(".gui.link", "")
                new_copy_buffer.append(link_key)

        elif window_tag_alias == "clip.gui.window":
            if self._active_clip_slot is not None:
                clip = self.state.tracks[self._active_clip_slot[0]].clips[self._active_clip_slot[0]]
                if clip is not None:
                    new_copy_buffer.append(clip)

        if new_copy_buffer:
            self.copy_buffer = new_copy_buffer

    def paste_selected(self):
        window_tag_alias = dpg.get_item_alias(dpg.get_active_window())
        if window_tag_alias is None:
            return

        if window_tag_alias.endswith("node_window"):
            # First add any nodes
            duplicate_map = {}
            link_ids = []
            for obj in self.copy_buffer:
                if isinstance(obj, str):
                    link_ids.append(obj)
                elif isinstance(obj, model.SourceNode):
                    result = self.execute_wrapper(f"duplicate_node {self._active_clip.id} {obj.id}")
                    if result.success:
                        new_input_channel = result.payload
                        self.add_source_node(sender=None, app_data=None, user_data=("restore", (self._active_clip, new_input_channel), False))
                        self.copy_node_position(self._active_clip, obj, self._active_clip, new_input_channel)
                        duplicate_map[obj.id] = new_input_channel
                    else:
                        raise RuntimeError(f"Failed to duplicate {obj.id}")
                else:
                        raise RuntimeError(f"Failed to duplicate {obj.id}")

        elif window_tag_alias == "clip.gui.window":
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
            self.action(NewClip({"track_i":track_i, "clip_i":clip_i}))
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
        dpg.configure_item("play_button", label="[Playing]" if self.state.playing else "[Paused]")

        # Cache the active clip, since it can change while this function is running
        c_active_clip = self._active_clip
        c_active_input_channel = self._active_input_channel

        if valid(c_active_clip):
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

        # Update automation points
        if valid(c_active_input_channel) and isinstance(c_active_input_channel, model.AutomatableSourceNode) and valid(c_active_input_channel.active_automation):
            automation = c_active_input_channel.active_automation
            xs = np.arange(0, c_active_input_channel.active_automation.length, 0.01)
            ys = c_active_input_channel.active_automation.f(xs).astype(float if c_active_input_channel.dtype == "float" else int)
            dpg.configure_item(
               f"{c_active_input_channel.id}.series",
                x=xs,
                y=ys,
            )

        # Update Inspector
        if valid(self._active_output_channel):
            dpg.configure_item(
                    "inspector.series",
                    x=self._inspecter_x,
                    y=self._active_output_channel.history[-1 - len(self._inspecter_x):-1],
                )

        # Set the play heads to the correct position
        if valid(self._active_clip, self._active_input_channel):
            if isinstance(self._active_input_channel, model.AutomatableSourceNode) and valid(self._active_input_channel.active_automation):
                playhead_tag = f"{self._active_input_channel.id}.gui.playhead"
                ext_value_tag = f"{self._active_input_channel.id}.gui.ext_value"
                dpg.configure_item(f"{self._active_input_channel.id}.gui.automation_enable_button", label="Disable" if self._active_input_channel.mode == "automation" else "Enable")

                playhead_color = {
                    "armed": [255, 100, 100, 255],
                    "recording": [255, 0, 0, 255],
                    "automation": [255, 255, 0, 255],
                    "manual": [200, 200, 200, 255],
                }
                dpg.configure_item("playhead_line.color", value=playhead_color[self._active_input_channel.mode])
                y_axis_limits_tag = f"{self._active_input_channel.id}.plot.y_axis_limits"
                playhead_x = self._active_input_channel.last_beat % self._active_input_channel.active_automation.length if self._active_input_channel.mode in ["automation", "armed", "recording"] else 0
                dpg.configure_item(
                    playhead_tag, 
                    x=[playhead_x, playhead_x],
                    y=dpg.get_axis_limits(y_axis_limits_tag),
                )

                x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                dpg.configure_item(
                    ext_value_tag, 
                    x=dpg.get_axis_limits(x_axis_limits_tag),
                    y=[self._active_input_channel.ext_get(), self._active_input_channel.ext_get()]
                )

        if model.LAST_MIDI_MESSAGE is not None:
            device_name, message = model.LAST_MIDI_MESSAGE
            channel = message.channel
            note_control, _ = model.midi_value(message)
            dpg.set_value("last_midi_message", f"{device_name}: {channel}/{note_control}")

        # Update IO Window
        red = (255, 0, 0, 100)
        green = [0, 255, 0, 255]
        for inout in ["inputs", "outputs"]:
            for i in range(5):
                table_tag = f"io.{inout}.table"
                io = self.state.io_inputs[i] if inout == "inputs" else self.state.io_outputs[i]
                color = red if io is None or not io.connected() else green
                if color == green:
                    alpha = 255 - int(clamp((time.time() - io.last_io_time)/0.25, 0, 0.5)*255)
                    color[3] = alpha
                dpg.highlight_table_cell(table_tag, i, 3, color=color)

        # Update Console
        console_text = "\n-\n\n".join(str(e) for e in self.state.log[::-1])
        dpg.set_value("console.text", console_text)

    def mouse_inside_window(self, window_tag):
        window_x, window_y = dpg.get_item_pos(window_tag)
        window_x2, window_y2 = window_x + dpg.get_item_width(window_tag), window_y + dpg.get_item_height(window_tag)
        return inside((self.mouse_x, self.mouse_y), (window_x, window_x2, window_y+10, window_y2))

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
                window_x2, window_y2 = window_x + dpg.get_item_width(tag), window_y + dpg.get_item_height(tag)
                if inside((self.mouse_x, self.mouse_y), (window_x, window_x2, window_y+10, window_y2)):
                    popup_menu_tag = get_node_window_tag(self._active_clip) + ".popup_menu"
                    dpg.configure_item(popup_menu_tag, pos=(self.mouse_x, self.mouse_y))
                    dpg.configure_item(popup_menu_tag, show=True)
                    dpg.focus_item(popup_menu_tag)

    def mouse_double_click_callback(self, sender, app_data, user_data):
        window_tag = dpg.get_item_alias(dpg.get_item_parent(dpg.get_active_window()))
        mouse_pos = dpg.get_mouse_pos()

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
                    x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                    y_axis_limits_tag = f"{self._active_input_channel.id}.plot.y_axis_limits"

                    # Clicked on a point, try to delete it.
                    if norm_distance((x,y), plot_mouse_pos, dpg.get_axis_limits(x_axis_limits_tag), dpg.get_axis_limits(y_axis_limits_tag)) <= 0.015:
                        if point == first_point or point == last_point:
                            return
                        result = self.execute_wrapper(f"delete_automation_point {automation.id} {point.id}")
                        if result.success:
                            dpg.delete_item(f"{point.id}.gui.point")
                            return
                        else:
                            raise RuntimeError("Failed to delete automation point")

                point = self._quantize_point(*plot_mouse_pos, self._active_input_channel.dtype, automation.length, quantize_x=False)
                result = self.execute_wrapper(
                    f"add_automation_point {automation.id} {point[0]},{point[1]}"
                )
                if result.success:
                    self.reset_automation_plot(self._active_input_channel)

    @gui_lock_callback
    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)
        #print(key_n)
        if key == " ":
            self.toggle_play_callback()
        elif key_n in [8, 46] and self.node_editor_window_is_focused and self.ctrl:
            self.delete_selected_nodes(None, None, self._active_clip)
        elif key_n in [120]:
            if self._active_input_channel is not None:
                self.enable_recording_mode(None, None, self._active_input_channel)
        elif key_n in [9]: # tab
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
                    self.add_source_node(None, None,( "create", (self._active_clip, "int"), False))
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_source_node(None, None, ("create", (self._active_clip, "bool"), False))
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_source_node(None, None, ("create", (self._active_clip, "float"), False))
        elif key in ["T"]:
            if self.state.mode == "performance":
                self.tap_tempo()
        elif key in ["V"]:
            if self.ctrl:
                    self.paste_selected()
        elif key in ["R"]:
            if self.ctrl:
                if valid(self._active_clip):
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
                self.reset_time()
        elif key in ["N"]:
            if self.ctrl:
                for track_i, track in enumerate(self.state.tracks):
                    if track == self._active_track:
                        for clip_i, clip in enumerate(track.clips):
                            if clip is None:
                                self.action(NewClip({"track_i":track_i, "clip_i":clip_i, "action":"create"}))
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
        keys = app_data
        if not isinstance(app_data, list):
            keys = [app_data]

        if 17 in keys:
            self.ctrl = True
        if 16 in keys:
            self.shift = True

    def key_release_callback(self, sender, app_data, user_data):
        if not isinstance(app_data, int):
            return
        key_n = app_data
        key = chr(key_n)
        if key_n == 17:
            self.ctrl = False
        if key_n == 16:
            self.shift = False


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
            left_point, right_point = automation.points[index-1], automation.points[index+1]
            if x < left_point.x:
                x = left_point.x
            if x > right_point.x:
                x = right_point.x
            x, y = self._quantize_point(x, y, automation.dtype, automation.length, quantize_x=True)

        result = self.execute_wrapper(f"update_automation_point {automation.id} {point.id} {x},{y}")
        if not result.success:
            raise RuntimeError("Failed to update automation point")

        dpg.set_value(sender, (x, y))

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
                    if not valid(input_channel):
                        continue
                    tag = get_node_tag(clip, input_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)
                for output_channel in clip.outputs:
                    if not valid(output_channel):
                        continue
                    tag = get_node_tag(clip, output_channel)
                    node_positions[tag] = dpg.get_item_pos(tag)

        gui_data = self.gui_state.copy()

        gui_data.update({
            "node_positions": node_positions,
        })

        data = {
            "state": self.state.serialize(),
            "gui": gui_data
        }

        with open(self.state.project_file_path, "w") as f:
            f.write(json.dumps(data, indent=4, sort_keys=False))

        dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}]")

    def save_code(self):
        if not os.path.exists(self.state.project_folder_path):
            os.mkdir(self.state.project_folder_path)
            os.mkdir(os.path.join(self.state.project_folder_path, "code"))

        self.state.code.save(dpg.get_value(get_code_window_tag(self.state) + ".text"))
        for track in self.state.tracks:
            track.code.save(dpg.get_value(get_code_window_tag(track) + ".text"))
            for clip in track.clips:
                if valid(clip):
                    clip.code.save(dpg.get_value(get_code_window_tag(clip) + ".text"))

    def restore_gui_state(self):
        for tag, pos in self.gui_state["node_positions"].items():
            try:
                dpg.set_item_pos(tag, pos)
            except:
                pass
        for inout in ["inputs", "outputs"]:
            for i, args in enumerate(self.gui_state["io_args"][inout]):
                if not valid(args):
                    continue
                dpg.set_value(f"io.{inout}.table.{i}.arg", args)

        for inout in ["inputs", "outputs"]:
            for i, io_type in enumerate(self.gui_state["io_types"][inout]):
                if not valid(io_type):
                    continue
                io_type_class = model.IO_TYPES[io_type]
                dpg.configure_item(f"io.{inout}.table.{i}.type", label=io_type_class.nice_title)

        for theme_tag, color in self.gui_state["clip_preset_themes"].items():
            dpg.configure_item(theme_tag, value=color)

    def save_last_active_clip(self):
        if valid(self._active_track) and valid(self._active_clip):
            self.gui_state["track_last_active_clip"][self._active_track.id] = self._active_clip.id

    def deserialize(self, data):
        self.state.deserialize(data["state"])
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

    def open_code_editor(self, sender, app_data, user_data):
        track_i, clip_i = user_data
        clip = self.state.tracks[track_i].clips[clip_i]
        if valid(clip):
            # TODO: Make configurable 
            subprocess.Popen(["C:\\Program Files\\Sublime Text\\sublime_text.exe", clip.code.file_path_name])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NodeDMX [BETA]")
    parser.add_argument("--project", 
                        default=None,
                        dest="project_file_path",
                        help="Project file path.")

    parser.add_argument("--cache", 
                        default=".cache",
                        dest="cache_file_path",
                        help="Cached data.")

    parser.add_argument("--profile", 
                        default=False,
                        dest="profile",
                        help="Enable profiling.")

    args = parser.parse_args()

    gui = Gui()

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
        gui.cache = cache

    if args.project_file_path:
        logging.debug("Opening %s", args.project_file_path)
        with open(args.project_file_path, 'r') as f:
            data = json.load(f)
            gui.deserialize(data)

    if args.profile:
         with Profile() as profile:
            gui.run()
            Stats(profile).strip_dirs().sort_stats(SortKey.CALLS).print_stats()
    else:
        gui.run()

    logging.info("Exiting.")