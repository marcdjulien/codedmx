import dearpygui.dearpygui as dpg
import model
import fixtures
import re
from copy import copy
import math
import time
import pickle
from threading import RLock
import numpy as np
import os
import mido

from cProfile import Profile
from pstats import SortKey, Stats

TOP_LEFT = (0, 18)
SCREEN_WIDTH = 1940
SCREEN_HEIGHT = 1150
PROJECT_EXTENSION = "ndmx"
NODE_EXTENSION = "ndmxc"
AXIS_MARGIN = 0.025
HUMAN_DELAY = 0.125

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


class GuiState:
    def __init__(self):
        self.node_positions = {}
        self.io_types = {
            "inputs": {},
            "outputs": {},
        }
        self.io_args = {
            "inputs": {},
            "outputs": {},
        }
        self.axis_limits = {}
        self.track_last_active_clip = {}
        self.point_tags = []


def get_node_editor_tag(clip):
    return f"{clip.id}.gui.node_window.node_editor"

def get_output_configuration_window_tag(track):
    return f"{track.id}.gui.output_configuration_window"

def get_io_matrix_window_tag(clip):
    return f"{clip.id}.gui.io_matrix_window"

def get_automation_window_tag(input_channel, is_id=False):
    return f"{input_channel if is_id else input_channel.id}.gui.automation_window"

def get_properties_window_tag(obj):
    return f"{obj.id}.gui.properties_window"

def get_plot_tag(input_channel):
    return f"{input_channel.id}.plot"

def get_node_tag(clip, obj):
    return f"{get_node_editor_tag(clip)}.{obj.id}.node"

def get_node_window_tag(clip):
    return f"{clip.id}.gui.node_window"

def get_node_attribute_tag(clip, channel):
    return f"{clip.id}.{channel.id}.node_attribute"

def get_output_node_value_tag(clip, output_channel):
    return f"{clip.id}.{output_channel.id}.output.value"

def get_group_tag(track_i, clip_i):
    return f"track[{track_i}].clip[{clip_i}].gui.table_group"

def show_callback(sender, app_data, user_data):
    dpg.configure_item(user_data, show=True)

class Gui:

    def __init__(self):
        self.tags = {}
        
        self.state = None
        self.new_state = None
        
        self.gui_state = None
        self.new_gui_state = None

        self.mouse_x, self.mouse_y = 0, 0
        self.mouse_drag_x, self.mouse_drag_y = 0, 0
        self.mouse_click_x, self.mouse_click_y = 0, 0
        self.mouse_clickr_x, self.mouse_clickr_y = 0, 0
        self.node_editor_window_is_focused = False

        self._active_track = None
        self._active_clip = None
        self._active_clip_slot = None
        self._active_output_channel = None
        self._active_input_channel = None
        self._inspecter_x = list(range(500))

        self._last_add_function_node = None
        self._custom_node_to_save = None

        self._tap_tempo_buffer = [0, 0, 0, 0, 0, 0]
        self._quantize_amount = None

        self.ctrl = False
        self.shift = False

        self.copy_buffer = []

        self.gui_lock = RLock()
    
    def warning(self, message):
        print(f"[!] {message}")

    def execute_wrapper(self, command):
        dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}] *")
        return self.state.execute(command)

    def run(self, state, gui_state):
        self.state = state
        self.gui_state = gui_state
        self.tags = {
            "hide_on_clip_selection": [],
            "node_window": [],
        }
        dpg.create_context()
        #dpg.show_metrics()
        #dpg.show_item_registry()

        self._active_track = self.state.tracks[0]

        #### Create Clip Window ####
        clip_window = dpg.window(tag="clip.gui.window", label="Clip", pos=(0,18), width=800, height=520, no_move=True, no_title_bar=True, no_resize=True)
        with clip_window as window:
            table_tag = f"clip_window.table"
            with dpg.table(header_row=False, tag=table_tag,
                   borders_innerH=True, borders_outerH=True, borders_innerV=True,
                   borders_outerV=True, policy=dpg.mvTable_SizingStretchProp, resizable=True):

                for track_i in range(len(self.state.tracks)):
                    dpg.add_table_column()

                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):
                        with dpg.table_cell():
                            with dpg.group(horizontal=True) as group_tag:
                                # When user clicks on the track title, bring up the output configuration window.
                                def select_track(sender, app_data, user_data):
                                    if self._active_track == user_data:
                                        return

                                    self.save_last_active_clip()

                                    # Unset activate clip
                                    self._active_clip = None
                                    for tag in self.tags["hide_on_clip_selection"]:
                                        dpg.configure_item(tag, show=False)

                                    self._active_track = user_data
                                    last_active_clip_id = self.gui_state.track_last_active_clip.get(self._active_track.id)
                                    if last_active_clip_id is not None:
                                        self._active_clip = self.state.get_obj(last_active_clip_id)
                                        self.select_clip_callback(None, None, (self._active_track, self._active_clip))

                                    self.update_clip_window()

                                def show_track_output_configuration_window(sender, app_data, user_data):
                                    # Hide all track config windows
                                    for track in self.state.tracks:
                                        dpg.configure_item(get_output_configuration_window_tag(track), show=False)
                                    dpg.configure_item(get_output_configuration_window_tag(user_data), show=True)
                                    dpg.focus_item(get_output_configuration_window_tag(user_data))
                                    
                                text_tag = f"{track.id}.gui.button"
                                self.add_passive_button(group_tag, text_tag, track.name, single_click_callback=select_track, user_data=track)

                                # Menu for track
                                for tag in [text_tag, text_tag+".filler"]:
                                    with dpg.popup(tag, mousebutton=1):
                                        dpg.add_menu_item(label="Properties", callback=show_track_output_configuration_window, user_data=track)


                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    # Row
                    with dpg.table_row(height=10):
                        for track_i, track in enumerate(self.state.tracks):
                            # Col
                            clip = track.clips[clip_i]
                            with dpg.table_cell() as cell_tag:
                                group_tag = get_group_tag(track_i, clip_i)
                                with dpg.group(tag=group_tag, horizontal=True, horizontal_spacing=5):
                                    # Always add elements for an empty clip, if the clip is not empty, then we will update it after.
                                    text_tag = f"{track.id}.{clip_i}.gui.text"
                                    self.add_passive_button(
                                        group_tag, 
                                        text_tag, 
                                        "", 
                                        single_click_callback=self.select_clip_slot_clip_callback, 
                                        double_click_callback=self.create_new_clip, 
                                        user_data=("create", track_i, clip_i)
                                    )
                                    # Menu for empty clip
                                    with dpg.popup(text_tag+".filler", mousebutton=1):
                                        dpg.add_menu_item(label="New Clip", callback=self.create_new_clip, user_data=("create", track_i, clip_i))
                                        dpg.add_menu_item(label="Paste", callback=self.paste_clip_callback, user_data=(track_i, clip_i))

                                    if clip is not None:
                                        self.populate_clip_slot(track_i, clip_i)

                self.update_clip_window()

        #### Mouse/Key Handlers ####
        with dpg.handler_registry():
            dpg.add_mouse_move_handler(callback=self.mouse_move_callback)
            dpg.add_mouse_click_handler(callback=self.mouse_click_callback)
            dpg.add_mouse_double_click_handler(callback=self.mouse_double_click_callback)
            dpg.add_key_press_handler(callback=self.key_press_callback)
            dpg.add_key_down_handler(callback=self.key_down_callback)
            dpg.add_key_release_handler(callback=self.key_release_callback)

        # Create Viewport
        dpg.create_viewport(title=f"NodeDMX [{self.state.project_name}] *", width=SCREEN_WIDTH, height=SCREEN_HEIGHT, x_pos=50, y_pos=0)

        # File Dialogs
        def save_callback(sender, app_data):
            self.state.project_filepath = app_data["file_path_name"]
            if not self.state.project_filepath.endswith(f".{PROJECT_EXTENSION}"):
                self.state.project_filepath += f".{PROJECT_EXTENSION}"
            self.save()

        def restore_callback(sender, app_data):
            self.restore(app_data["file_path_name"])

        def save_custom_node(sender, app_data):
            if self._custom_node_to_save is None:
                return

            file_path_name = app_data["file_path_name"]
            if not file_path_name.endswith(f".{NODE_EXTENSION}"):
                file_path_name += f".{NODE_EXTENSION}"

            with open(file_path_name, "w") as f:
                f.write(f"n_inputs:{self._custom_node_to_save.parameters[0].value}\n")
                f.write(f"n_outputs:{self._custom_node_to_save.parameters[1].value}\n")
                f.write(self._custom_node_to_save.parameters[2].value.replace("[NEWLINE]", "\n"))

        def load_custom_node(sender, app_data):
            file_path_name = app_data["file_path_name"]
            n_inputs = None
            n_outputs = None
            code = ""
            with open(file_path_name, "r") as f:
                for line in f:
                    if line.startswith("n_inputs"):
                        n_inputs = line.split(":")[-1]
                    elif line.startswith("n_outputs"):
                        n_outputs = line.split(":")[-1]
                    else:
                        code += line

            if any(thing is None for thing in [n_inputs, n_outputs, code]):
                self.log("Failed to parse custom node")
                return

            self.add_custom_function_node(None, None, ("create", ("custom", f"{n_inputs},{n_outputs},{code}", self._active_clip), False))

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
                directory_selector=True, 
                show=False, 
                callback=save_custom_node, 
                tag="save_custom_node_dialog",
                cancel_callback=self.print_callback, 
                width=700,
                height=400,
                modal=True,
            )

            dpg.add_file_dialog(
                directory_selector=False, 
                show=False, 
                callback=load_custom_node, 
                tag="open_custom_node_dialog",
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

            for tag in ["open_custom_node_dialog", "save_custom_node_dialog"]:
                dpg.add_file_extension(f".{NODE_EXTENSION}", color=[0, 255, 255, 255], parent=tag)

            dpg.add_file_extension(f".fixture", color=[0, 255, 255, 255], parent="open_fixture_dialog")

            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open", callback=self.open_menu_callback)
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

            with dpg.menu(label="Test"):
                def test1():
                    self.create_new_clip(None, None, ("create", "1", "1"))
                    self.add_input_node(None, None, ("create", (self.state.tracks[1].clips[1], "int"), False))
                    self.add_input_node(None, None, ("create", (self.state.tracks[1].clips[1], "bool"), False))
                    self.add_input_node(None, None, ("create", (self.state.tracks[1].clips[1], "float"), False))
                    self.add_function_node(None, None, ("create", ("demux", 5, self.state.tracks[1].clips[1]), False))
                dpg.add_menu_item(label="Test 1", callback=test1)

            # Transport 
            transport_start_x = 800
            dpg.add_button(label="Reset", callback=self.reset_time, pos=(transport_start_x-100, 0))

            transport_start_x = 800
            dpg.add_button(label="Tap Tempo", callback=self.tap_tempo, pos=(transport_start_x, 0))

            def update_tempo(sender, app_data):
                self.state.tempo = app_data
            #dpg.add_text("Tempo:", pos=(transport_start_x + 90,0))
            dpg.add_input_float(label="Tempo", default_value=self.state.tempo, pos=(transport_start_x + 75, 0), on_enter=True, callback=update_tempo, width=45, tag="tempo", step=0)

            def toggle_play(sender):
                self.state.playing = not self.state.playing
            dpg.add_button(label="[Play]", callback=toggle_play, pos=(transport_start_x + 165, 0), tag="play_button")

            def mode_change():
                self.state.mode = "edit" if self.state.mode == "performance" else "performance"
                dpg.configure_item("mode_button", label="Edit Mode" if self.state.mode == "edit" else "Performance Mode")
                dpg.set_item_pos("mode_button", (transport_start_x+1000+50, 0) if self.state.mode == "edit" else (transport_start_x+1000, 0))
            dpg.add_button(label="Edit Mode", callback=mode_change, pos=(transport_start_x+1000+50, 0), tag="mode_button")

            # Global Variables
            with dpg.value_registry():
                dpg.add_string_value(default_value="", tag="io_matrix.source_filter_text")
                dpg.add_string_value(default_value="", tag="io_matrix.destination_filter_text")
                dpg.add_string_value(default_value="", tag="last_midi_message")


        ################
        #### Restore ###
        ################

        # Need to create this after the node_editor_windows
        for track in self.state.tracks:                           
            self.create_track_output_configuration_window(track)

        self.create_inspector_window()
        self.create_io_window()

        dpg.setup_dearpygui()
        dpg.show_viewport()

        self.restore_gui_state()

        return self.main_loop()
    
    def open_menu_callback(self):
        dpg.configure_item("open_file_dialog", show=True)

    def save_menu_callback(self):
        if self.state.project_filepath is None:
            dpg.configure_item("save_file_dialog", show=True)
        self.save()

    def save_as_menu_callback(self):
        dpg.configure_item("save_file_dialog", show=True)

    def add_passive_button(self, group_tag, text_tag, text, single_click_callback=None, double_click_callback=None, user_data=None, double_click=False):
        dpg.add_text(parent=group_tag, default_value=text, tag=text_tag)
        dpg.add_text(parent=group_tag, default_value=" "*1000, tag=f"{text_tag}.filler")
        if single_click_callback is not None:
            self.register_handler(dpg.add_item_clicked_handler, group_tag, single_click_callback, user_data)
        if double_click_callback is not None:
            self.register_handler(dpg.add_item_double_clicked_handler, group_tag, double_click_callback, user_data)

    def create_new_clip(self, sender, app_data, user_data):
        action, track_i, clip_i = user_data
        group_tag = group_tag = get_group_tag(track_i, clip_i)
        track = self.state.tracks[int(track_i)]

        if action == "create":
            success, clip = self.execute_wrapper(f"new_clip {track.id},{clip_i}")
            if not success:
                raise RuntimeError("Failed to create clip")
        else: # restore
            clip = self.state.tracks[int(track_i)].clips[int(clip_i)]
        
        self.populate_clip_slot(track_i, clip_i)
        self.update_clip_window()

    def populate_clip_slot(self, track_i, clip_i):
        group_tag = get_group_tag(track_i, clip_i)
        track = self.state.tracks[int(track_i)]
        clip = track.clips[int(clip_i)]

        for slot, child_tags in dpg.get_item_children(group_tag).items():
            for child_tag in child_tags:
                dpg.delete_item(child_tag)

        self.add_clip_elements(track, clip, group_tag, track_i, clip_i)
        
        with self.gui_lock:
            self.create_node_editor_window(clip)
    
    def paste_clip_callback(self, sender, app_data, user_data):
        self.paste_clip(*user_data)
        self.update_clip_window()

    def play_clip_callback(self, sender, app_data, user_data):
        track, clip = user_data
        if self.execute_wrapper(f"toggle_clip {track.id} {clip.id}"):
            self.update_clip_window()

    def add_clip_elements(self, track, clip, group_tag, track_i, clip_i):
        dpg.add_button(arrow=True, direction=dpg.mvDir_Right, tag=f"{clip.id}.gui.play_button", callback=self.play_clip_callback, user_data=(track,clip), parent=group_tag)                        
        
        text_tag = f"{clip.id}.name"
        self.add_passive_button(group_tag, text_tag, clip.name, self.select_clip_callback, user_data=(track, clip))

        def copy_clip_callback(sender, app_data, user_data):
            self.copy_buffer = [user_data]

        for tag in [text_tag, text_tag+".filler"]:
            with dpg.popup(tag, mousebutton=1):
                dpg.add_menu_item(label="Copy", callback=copy_clip_callback, user_data=clip)
                dpg.add_menu_item(label="Paste", callback=self.paste_clip_callback, user_data=(track_i, clip_i))

        self.save_last_active_clip()
        self._active_track = track
        self._active_clip = clip

    def register_handler(self, add_item_handler_func, tag, function, user_data):
        handler_registry_tag = f"{tag}.item_handler_registry"
        if not dpg.does_item_exist(handler_registry_tag):
            dpg.add_item_handler_registry(tag=handler_registry_tag)
        add_item_handler_func(parent=handler_registry_tag, callback=function, user_data=user_data)
        dpg.bind_item_handler_registry(tag, handler_registry_tag)

    def reset_time(self):
        self.state.play_time_start = time.time() - HUMAN_DELAY
        self.state.beats_since_start = time.time()

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

                # In edit mode the active clip should always play.
                if clip is not None:
                    if self.state.mode == "edit":
                        if self._active_clip == clip:
                            if not clip.playing:
                                clip.start()
                        else:
                            clip.stop()

                active = 155 if self._active_clip_slot == (track_i, clip_i) else 0
                if clip is not None and clip == self._active_clip:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 155, 155, 100 + active])                    
                elif clip is not None and clip.playing:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 255, 10, 200 + active])
                elif clip is not None and not clip.playing:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 50, 100, 100 + active])
                else:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[50, 50, 50, 100 + active])                    

            if self._active_track == track:
                dpg.highlight_table_column("clip_window.table", track_i, color=[100, 100, 100, 255])
            else:
                dpg.highlight_table_column("clip_window.table", track_i, color=[0, 0, 0, 0])

    def main_loop(self):
        print("Running main loop")
        try:
            while dpg.is_dearpygui_running():
                with self.gui_lock:
                    self.update_state_from_gui()
                self.state.update()
                with self.gui_lock:
                    self.update_gui_from_state()
                dpg.render_dearpygui_frame()
            
            dpg.destroy_context()
        except:
            print("\n\n\n")
            print(model.UUID_DATABASE)
            print([dpg.get_item_alias(item) for item in dpg.get_all_items()])
            raise

        # TODO: Close old window.
        return self.new_state, self.new_gui_state

    def create_node_editor_window(self, clip):
        window_tag = get_node_window_tag(clip)
        self.tags["hide_on_clip_selection"].append(window_tag)

        with dpg.window(
            tag=window_tag,
            label=f"Node Window | {clip.name}",
            width=SCREEN_WIDTH * 9.9 / 10,
            height=570,
            pos=(0, 537),
            no_title_bar=True,
            no_move=True,

        ) as window:
            # Node Editor
            node_editor_tag = get_node_editor_tag(clip)
            dpg.add_node_editor(
                callback=self.add_link_callback,
                delink_callback=self.delete_link_callback,
                tag=node_editor_tag,
                user_data=("create", clip),
                minimap=True,
                minimap_location=dpg.mvNodeMiniMap_Location_BottomRight
            )
            with dpg.menu_bar() as menu_tag:
                self.add_node_menu(menu_tag, clip)


                def show_io_matrix(sender, app_Data, user_data):
                    clip = user_data
                    dpg.configure_item(get_io_matrix_window_tag(clip), show=False)
                    dpg.configure_item(get_io_matrix_window_tag(clip), show=True)
                    self.update_io_matrix_window(clip)


                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="I/O Matrix", callback=show_io_matrix, user_data=clip)

                dpg.add_menu_item(label="[Ctrl+Del]", callback=self.delete_selected_nodes)

                def show_presets_window():
                    pass
                dpg.add_menu_item(label="Presets", callback=show_presets_window, user_data=clip)

                dpg.add_text(default_value="Clip Name:")

                def set_clip_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        clip.name = app_data
                dpg.add_input_text(source=f"{clip.id}.name", width=75, callback=set_clip_text)



        ###############
        ### Restore ###
        ###############
        self.create_io_matrix_window(clip, show=False)

        # Popup window for adding node elements
        popup_window_tag = get_node_window_tag(clip) + ".popup_menu"
        with dpg.window(tag=popup_window_tag, show=False, no_title_bar=True):
            self.add_node_menu(popup_window_tag, clip)

        for input_index, input_channel in enumerate(clip.inputs):
            if input_channel.deleted:
                continue
            self.add_input_node(sender=None, app_data=None, user_data=("restore", (clip, input_channel), False))

        for output_index, output_channel in enumerate(clip.outputs):
            if output_channel.deleted:
                continue
            self.add_output_node(clip, output_channel)

        for node_index, node in enumerate(clip.node_collection.nodes):
            if node.deleted:
                continue
            if isinstance(node, model.FunctionCustomNode):
                self.add_custom_function_node(sender=None, app_data=None, user_data=("restore", (clip, node), False))
            else:
                self.add_function_node(sender=None, app_data=None, user_data=("restore", (clip, node), False))

        for link_index, link in enumerate(clip.node_collection.links):
            if link.deleted:
                continue
            self.add_link_callback(sender=None, app_data=None, user_data=("restore", clip, link.src_channel, link.dst_channel))


    def toggle_node_editor_fullscreen(self):
        if self.state.mode != "edit":
            return
        
        if not valid(self._active_clip):
            return

        window_tag = get_node_window_tag(self._active_clip)
        cur_pos = tuple(dpg.get_item_pos(window_tag))
        print(cur_pos)
        if cur_pos == TOP_LEFT:
            dpg.configure_item(window_tag, pos=self._old_node_editor_pos)
            dpg.configure_item(window_tag, height=self._old_node_editor_height)
            dpg.configure_item(window_tag, width=self._old_node_editor_width)
        else:
            self._old_node_editor_pos = dpg.get_item_pos(window_tag)
            self._old_node_editor_height = dpg.get_item_height(window_tag)
            self._old_node_editor_width = dpg.get_item_width(window_tag)
            dpg.configure_item(window_tag, pos=TOP_LEFT)
            dpg.configure_item(window_tag, height=SCREEN_HEIGHT)
            dpg.configure_item(window_tag, width=SCREEN_WIDTH)
    
    def add_node_menu(self, parent, clip):
        right_click_menu = "popup_menu" in dpg.get_item_alias(parent)

        with dpg.menu(parent=parent, label="Inputs"):
            dpg.add_menu_item(label="Bool", callback=self.add_input_node, user_data=("create", (clip, "bool"), right_click_menu))
            dpg.add_menu_item(label="Integer", callback=self.add_input_node, user_data=("create", (clip, "int"), right_click_menu))
            dpg.add_menu_item(label="Float", callback=self.add_input_node, user_data=("create", (clip, "float"), right_click_menu))
            dpg.add_menu_item(label="Osc Integer", callback=self.add_input_node, user_data=("create", (clip, "osc_input_int"), right_click_menu))
            dpg.add_menu_item(label="Osc Float", callback=self.add_input_node, user_data=("create", (clip, "osc_input_float"), right_click_menu))
            dpg.add_menu_item(label="MIDI", callback=self.add_input_node, user_data=("create", (clip, "midi"), right_click_menu))

        with dpg.menu(parent=parent, label="Functions"):
            dpg.add_menu_item(
                label="Binary Operator", user_data=("create", ("binary_operator", ",", clip), right_click_menu), callback=self.add_function_node
            )
            with dpg.menu(label="Demux"):
                for i in range(2, 33):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("demux", i, clip), right_click_menu), callback=self.add_function_node
                    )
            with dpg.menu(label="Multiplexer"):
                for i in range(2, 16):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("multiplexer", i, clip), right_click_menu), callback=self.add_function_node
                    )
            with dpg.menu(label="Aggregator"):
                for i in range(2, 16):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("aggregator", i, clip), right_click_menu), callback=self.add_function_node
                    )
            with dpg.menu(label="Separator"):
                for i in range(2, 16):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("separator", i, clip), right_click_menu), callback=self.add_function_node
                    )
            dpg.add_menu_item(
                label="Random", user_data=("create", ("random", ",", clip), right_click_menu), callback=self.add_function_node
            )                
            dpg.add_menu_item(
                label="Sample", user_data=("create", ("sample", ",", clip), right_click_menu), callback=self.add_function_node
            ) 
            dpg.add_menu_item(
                label="Buffer", user_data=("create", ("buffer", ",", clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="Changing", user_data=("create", ("changing", ",", clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="ToggleOnChange", user_data=("create", ("toggle_on_change", ",", clip), right_click_menu), callback=self.add_function_node
            )   
            with dpg.menu(label="Last Changed"):
                for i in range(2, 16):
                    dpg.add_menu_item(  
                        label=i, user_data=("create", ("last_changed", i, clip), right_click_menu), callback=self.add_function_node
                    )  
            dpg.add_menu_item(
                label="Passthrough", user_data=("create", ("passthrough", ",", clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="Canvas 1x8", user_data=("create", ("canvas1x8", ",", clip), right_click_menu), callback=self.add_function_node
            )   
            dpg.add_menu_item(
                label="Pixel Mover 1", user_data=("create", ("pixelmover1", ",", clip), right_click_menu), callback=self.add_function_node
            )
        with dpg.menu(parent=parent,label="Custom"):
            dpg.add_menu_item(
                label="New Custom Node", user_data=("create", ("custom", ",", clip), right_click_menu), callback=self.add_custom_function_node
            ) 
            dpg.add_menu_item(
                label="Load Custom Node", user_data=(clip, right_click_menu), callback=self.load_custom_node_callback
            ) 
    
    def update_input_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.ext_set(app_data)

    def update_channel_value(self, sender, app_data, user_data):
        channel = user_data
        channel.set(app_data)

    def update_channel_attr(self, sender, app_data, user_data):
        channel, attr = user_data
        setattr(channel, attr, app_data)

    def gui_lock_callback(func):
        def wrapper(self, sender, app_data, user_data):
            with self.gui_lock:
                return func(self, sender, app_data, user_data)
        return wrapper

    @gui_lock_callback
    def add_input_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
        if action == "create":
            clip, dtype = args
            success, input_channel = self.execute_wrapper(f"create_input {clip.id} {dtype}")
            if not success:  
                raise RuntimeError("Failed to create input")
            success = self.execute_wrapper(f"add_automation {input_channel.id}")
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
                self.update_parameter(None, app_data, (clip, input_channel, parameter_index))

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
                        user_data=(clip, input_channel, parameter_index), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                    )


            with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel), attribute_type=dpg.mvNode_Attr_Output):
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
                action
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
                        dpg.configure_item(get_automation_window_tag(other_input_channel), show=False)
                    dpg.configure_item(get_automation_window_tag(self._active_input_channel), show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=input_selected_callback, user_data=(clip, input_channel))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            self.create_properties_window(clip, input_channel)

            self.update_io_matrix_window(clip)

    @gui_lock_callback
    def add_function_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        right_click_menu = user_data[2]
        if action == "create":
            node_type = args[0]
            node_args = args[1]
            clip = args[2]
            success, node = state.execute(f"create_node {clip.id} {node_type} {node_args}")
            if not success:
                return
            self._last_add_function_node = (sender, app_data, user_data)
        else: # restore
            clip, node = args

        dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        parent = get_node_editor_tag(clip)
        parameters = node.parameters
        input_channels = node.inputs
        output_channels = node.outputs

        node_tag = get_node_tag(clip, node)
        with dpg.node(parent=get_node_editor_tag(clip), tag=node_tag, label=node.name):

            self.add_node_popup_menu(node_tag, clip, node)

            for parameter_index, parameter in enumerate(parameters):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name, 
                        tag=f"{parameter.id}.value",
                        callback=self.update_parameter, 
                        user_data=(clip, node, parameter_index), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                    )

            for input_index, input_channel in enumerate(input_channels):
                with dpg.node_attribute(tag=get_node_attribute_tag(clip, input_channel)):
                    kwargs = {}
                    if input_channel.dtype == "any":
                        add_func = dpg.add_input_text
                    elif input_channel.size == 1:
                        add_func = dpg.add_input_float if input_channel.dtype == "float" else dpg.add_input_int
                    else:
                        add_func = dpg.add_drag_floatx 
                        kwargs["size"] = input_channel.size

                    add_func(
                        label=input_channel.name, 
                        tag=f"{input_channel.id}.value", 
                        width=90, 
                        on_enter=True,
                        default_value=input_channel.get(),
                        callback=self.update_input_channel_value_callback,
                        user_data=input_channel,
                        **kwargs
                    )

            for output_index, output_channel in enumerate(output_channels):
                with dpg.node_attribute(tag=get_node_attribute_tag(clip, output_channel), attribute_type=dpg.mvNode_Attr_Output):
                    if output_channel.dtype == "any":
                        dpg.add_input_text(tag=f"{output_channel.id}.value", readonly=True, width=100)
                    elif output_channel.size == 1:
                        add_func = dpg.add_input_float if output_channel.dtype == "float" else dpg.add_input_int
                        add_func(label=output_channel.name, tag=f"{output_channel.id}.value", width=90)
                    else:
                        add_func = dpg.add_drag_floatx 
                        add_func(label=output_channel.name, tag=f"{output_channel.id}.value", width=90, size=output_channel.size)



            def node_selcted_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=node_selcted_callback, user_data=(clip, node))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

        self.create_properties_window(clip, node)

        self.update_io_matrix_window(clip)

    @gui_lock_callback
    def add_custom_function_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "create":
            node_type = args[0]
            node_args = args[1]
            clip = args[2]
            success, node = state.execute(f"create_node {clip.id} {node_type} {node_args}")
            if not success:
                return
        else: # restore
            clip, node = args

        dpg.configure_item(get_node_window_tag(clip) + ".popup_menu", show=False)

        parent = get_node_editor_tag(clip)
        parameters = node.parameters
        input_channels = node.inputs
        output_channels = node.outputs

        node_tag = get_node_tag(clip, node)
        with dpg.node(parent=get_node_editor_tag(clip), tag=node_tag, label=node.name):

            self.add_node_popup_menu(node_tag, clip, node)

            # Parameter 0 = n_inputs
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    label=node.parameters[0].name, 
                    tag=f"{node.parameters[0].id}.value",
                    callback=self.update_custom_node_attributes, 
                    user_data=(clip, node, 0), 
                    width=70,
                    default_value=node.parameters[0].value if node.parameters[0].value is not None else "0",
                    on_enter=True,
                    decimal=True,
                )

            # Parameter 1 = n_outputs
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    label=node.parameters[1].name, 
                    tag=f"{node.parameters[1].id}.value",
                    callback=self.update_custom_node_attributes, 
                    user_data=(clip, node, 1), 
                    width=70,
                    default_value=node.parameters[1].value if node.parameters[1].value is not None else "0",
                    on_enter=True,
                    decimal=True,
                )

            for input_index, input_channel in enumerate(input_channels):
                self.add_custom_node_input_attribute(clip, node, input_channel)

            for output_index, output_channel in enumerate(output_channels):
                self.add_custom_node_output_attribute(clip, node, output_channel)

            def node_selcted_callback(sender, app_data, user_data):
                # Right click menu
                if app_data[0] == 1:
                    dpg.configure_item(f"{node_tag}.popup", show=True)

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=node_selcted_callback, user_data=(clip, node))
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

        self.create_custom_node_properties_window(clip, node)

        self.update_io_matrix_window(clip)

    def add_custom_node_input_attribute(self, clip, node, channel):
        with dpg.node_attribute(parent=get_node_tag(clip, node), tag=get_node_attribute_tag(clip, channel)):
            dpg.add_input_text(
                label=channel.name, 
                tag=f"{channel.id}.value", 
                width=90, 
                on_enter=True,
                default_value=channel.get(),
                callback=self.update_input_channel_value_callback,
                user_data=channel
            )

    def add_custom_node_output_attribute(self, clip, node, channel):
        with dpg.node_attribute(parent=get_node_tag(clip, node), tag=get_node_attribute_tag(clip, channel), attribute_type=dpg.mvNode_Attr_Output):
            dpg.add_input_text(label=channel.name, tag=f"{channel.id}.value", width=90)

    def update_custom_node_attributes(self, sender, app_data, user_data):
        with self.gui_lock:
            n = int(app_data)
            clip, node, parameter_index = user_data
            success, results = self.execute_wrapper(f"update_parameter {node.id} {parameter_index} {n}")
            if success:
                delta, channels = results
                for channel in channels:
                    if delta > 0:
                        if parameter_index == 0:
                            self.add_custom_node_input_attribute(clip, node, channel)
                        else:
                            self.add_custom_node_output_attribute(clip, node, channel)
                    elif delta < 0:
                        self.delete_associated_links([channel])
                        dpg.delete_item(get_node_attribute_tag(clip, channel))
                self.update_io_matrix_window(clip)

    def update_input_channel_value_callback(self, sender, app_data, user_data):
        # If an input isn't connected to a node, the user can set it 
        if app_data is not None:
            input_channel = user_data
            success = self.execute_wrapper(f"update_channel_value {input_channel.id} {app_data}")
            if not success:
                raise RuntimeError(f"Failed to update channel value {input_channel.id}")

    def load_custom_node_callback(self, sender, app_data, user_data):
        clip = user_data
        dpg.configure_item("open_custom_node_dialog", show=True)

    def add_output_node(self, clip, output_channel):
        # This is the id used when adding links.
        attr_tag = get_node_attribute_tag(clip, output_channel)

        if dpg.does_item_exist(attr_tag):
            return

        node_tag = get_node_tag(clip, output_channel)
        with dpg.node(label="Output", tag=node_tag, parent=get_node_editor_tag(clip)):

            self.add_node_popup_menu(node_tag, clip, output_channel)

            with dpg.node_attribute(tag=attr_tag):
                dpg.add_input_int(label="In", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="Ch.", source=f"{output_channel.id}.dmx_channel", width=50, readonly=True, step=0)

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

        self.update_io_matrix_window(clip)

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
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    # No source
                    def set_name_property(sender, app_data, user_data):
                        if app_data:
                            obj.name = app_data
                            dpg.configure_item(get_node_tag(clip, obj), label=obj.name)
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(default_value=obj.name, on_enter=True, callback=set_name_property)

                if isinstance(obj, model.Parameterized):
                    for parameter_index, parameter in enumerate(obj.parameters):
                        with dpg.table_row():
                            dpg.add_text(default_value=parameter.name)
                            dpg.add_input_text(
                                source=f"{parameter.id}.value",
                                callback=self.update_parameter, 
                                user_data=(clip, obj, parameter_index), 
                                default_value=parameter.value if parameter.value is not None else "",
                                on_enter=True,
                            )

                if isinstance(obj, model.FunctionNode):
                    for parameter_index, parameter in enumerate(obj.parameters):
                        with dpg.table_row():
                            dpg.add_text(default_value=parameter.name)
                            dpg.add_input_text(
                                source=f"{parameter.id}.value",
                                callback=self.update_parameter, 
                                user_data=(clip, obj, parameter_index), 
                                default_value=parameter.value if parameter.value is not None else "",
                                on_enter=True,
                            )

    def create_custom_node_properties_window(self, clip, node):
        window_tag = get_properties_window_tag(node)
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
        ) as window:
            properties_table_tag = f"{window_tag}.properties_table"

            with dpg.table(header_row=True, tag=properties_table_tag, policy=dpg.mvTable_SizingStretchProp):
                dpg.add_table_column(label="Property")
                dpg.add_table_column(label="Value")

                with dpg.table_row():
                    # No source
                    def set_name_property(sender, app_data, user_data):
                        if app_data:
                            node.name = app_data
                            dpg.configure_item(get_node_tag(clip, node), label=node.name)
                    dpg.add_text(default_value="Name")
                    dpg.add_input_text(default_value=node.name, on_enter=True, callback=set_name_property)

                # Inputs
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[0].name)
                    dpg.add_input_text(
                        source=f"{node.parameters[0].id}.value",
                        callback=self.update_custom_node_attributes, 
                        user_data=(clip, node, 0), 
                        default_value=node.parameters[0].value if node.parameters[0].value is not None else "",
                        on_enter=True,
                    )

                # Outputs
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[1].name)
                    dpg.add_input_text(
                        source=f"{node.parameters[1].id}.value",
                        callback=self.update_custom_node_attributes, 
                        user_data=(clip, node, 1), 
                        default_value=node.parameters[1].value if node.parameters[1].value is not None else "",
                        on_enter=True,
                    )

                # Code                        
                with dpg.table_row():
                    dpg.add_text(default_value=node.parameters[2].name)
                    with dpg.group():
                        default_value = node.parameters[2].value.replace("[NEWLINE]", "\n") if node.parameters[2].value is not None else ""
                        with dpg.value_registry():
                            dpg.add_string_value(tag=f"{node}.code.text", default_value=default_value)
                
                        def log(sender, app_data, user_data):
                            dpg.set_value(f"{user_data}.code.text", app_data)

                        def save_code():
                            self.update_parameter(None, dpg.get_value(f"{node}.code.text"), (clip, node, 2))
                        
                        dpg.add_input_text(
                            source=f"{node.parameters[2].id}.value",
                            callback=log, 
                            user_data=node, 
                            default_value=default_value,
                            multiline=True,
                            tab_input=True,
                            width=300,
                            height=400
                        )
                        dpg.add_button(label="Save", callback=save_code)

    def add_node_popup_menu(self, node_tag, clip, obj):
        def show_properties_window(sender, app_data, user_data):
            dpg.configure_item(get_properties_window_tag(user_data), show=True)

        def save_custom_node(sender, app_data, user_data):
            self._custom_node_to_save = user_data
            dpg.configure_item("save_custom_node_dialog", show=True)

        def create_and_show_connect_to_window(sender, app_data, user_data):
            clip, src = user_data
            try:
                dpg.delete_item("connect_to_window")
            except:
                pass

            def connect_node_and_hide_window(sender, app_data, user_data):
                self.connect_nodes(*user_data)
                dpg.configure_item("connect_to_window", show=False)

            with dpg.window(tag="connect_to_window", no_title_bar=True, max_size=(200, 400), pos=(self.mouse_x, self.mouse_y)):
                    # TODO: Finish
                    with dpg.menu(label="Search"):
                        dpg.add_input_text()

                    with dpg.menu(label="Clip Outputs"):
                        with dpg.menu(label="All (Starting at)"):
                            for i, output_channel in enumerate(clip.outputs):
                                dpg.add_menu_item(label=output_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, clip.outputs[i::]))

                        for output_channel in clip.outputs:
                            if valid(output_channel):
                                dpg.add_menu_item(label=output_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, [output_channel]))

                    for dst_node in clip.node_collection.nodes:
                        if valid(dst_node) and dst_node != src:
                            with dpg.menu(label=dst_node.name):
                                with dpg.menu(label="All (Starting at)"):
                                    for i, dst_channel in enumerate(dst_node.inputs):
                                        dpg.add_menu_item(label=dst_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, dst_node.inputs[i::]))
    
                                for dst_channel in dst_node.inputs:
                                    dpg.add_menu_item(label=dst_channel.name, callback=connect_node_and_hide_window, user_data=(clip, src, [dst_channel]))


        with dpg.popup(parent=node_tag, tag=f"{node_tag}.popup", mousebutton=1):
            dpg.add_menu_item(label="Properties", callback=show_properties_window, user_data=obj)
            if isinstance(obj, (model.FunctionNode, model.ClipInputChannel)):
                dpg.add_menu_item(label="Connect To ...", callback=create_and_show_connect_to_window, user_data=(clip, obj))
            if isinstance(obj, model.FunctionCustomNode):
                dpg.add_menu_item(label="Save", callback=save_custom_node, user_data=obj)
            if isinstance(obj, model.MidiInput):
                dpg.add_menu_item(label="Update Map MIDI", callback=self.update_midi_map_node, user_data=obj)
                dpg.add_menu_item(label="Learn Map MIDI", callback=self.learn_midi_map_node, user_data=obj)

                def unmap_midi(sender, app_data, user_data):
                    obj = user_data
                    success = self.execute_wrapper(f"unmap_midi {obj.id}")
                    if success:
                        device_parameter_id = obj.get_parameter_id("device")
                        id_parameter_id = obj.get_parameter_id("id")
                        dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                        dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                dpg.add_menu_item(label="Unmap MIDI", callback=unmap_midi, user_data=obj)

    def update_midi_map_node(self, sender, app_data, user_data):
        self.execute_wrapper(f"midi_map {user_data.id}")

    def learn_midi_map_node(self, sender, app_data, user_data):
        obj = user_data
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
                note_control = message.control if message.is_cc() else message.note
                self.update_parameter_by_name(obj, "device", device_name)
                self.update_parameter_by_name(obj, "id", f"{message.channel}/{note_control}")
                success = self.execute_wrapper(f"midi_map {obj.id}")
                if success:
                    device_parameter_id = obj.get_parameter_id("device")
                    id_parameter_id = obj.get_parameter_id("id")
                    dpg.set_value(f"{device_parameter_id}.value", obj.get_parameter("device").value)
                    dpg.set_value(f"{id_parameter_id}.value", obj.get_parameter("id").value)
                    dpg.delete_item("midi_map_window")

        dpg.set_value("last_midi_message", "")

        with dpg.window(tag="midi_map_window", modal=True, width=300, height=300):
            dpg.add_text("Incoming MIDI: ")
            dpg.add_text(source="last_midi_message")
            dpg.add_button(label="Save", callback=save, user_data=obj)

    def update_parameter(self, sender, app_data, user_data):
        if app_data:
            clip, node, parameter_index = user_data
            success, _ = self.execute_wrapper(f"update_parameter {node.id} {parameter_index} {app_data}")
            if not success:
                raise RuntimeError("Failed to update parameter")

    def update_parameter_by_name(self, obj, parameter_name, value):
        obj.get_parameter(parameter_name).value = value

    def add_link_callback(self, sender, app_data, user_data):
        action, clip = user_data[0:2]

        if action == "create":
            if app_data is not None:
                src_tag, dst_tag = app_data
                src_tag = (dpg.get_item_alias(src_tag) or src_tag).replace(".node_attribute", "")
                dst_tag = (dpg.get_item_alias(dst_tag) or dst_tag).replace(".node_attribute", "")
                src_channel_id = src_tag.split(".", 1)[-1]
                dst_channel_id = dst_tag.split(".", 1)[-1]
                src_channel = self.state.get_obj(src_channel_id)
                dst_channel = self.state.get_obj(dst_channel_id)
            else:
                src_channel, dst_channel = user_data[2:4]
            
            success = self.execute_wrapper(f"create_link {clip.id} {src_channel.id} {dst_channel.id}")
            if not success:
                raise RuntimeError("Failed to create link")
        else: # restore
            src_channel, dst_channel = user_data[2:4]

        src_node_attribute_tag = get_node_attribute_tag(clip, src_channel)
        dst_node_attribute_tag = get_node_attribute_tag(clip, dst_channel)
        link_tag = f"{src_node_attribute_tag}:{dst_node_attribute_tag}.gui.link"
        dpg.add_node_link(src_node_attribute_tag, dst_node_attribute_tag, parent=get_node_editor_tag(clip), tag=link_tag)
        self.update_io_matrix_window(clip)

    def delete_link_callback(self, sender, app_data, user_data):
        alias = dpg.get_item_alias(app_data) or app_data
        clip = user_data[1]
        self._delete_link(alias, alias.replace(".gui.link", ""), clip)

    def connect_nodes(self, clip, src, dst_channels):
        src_channels = []
        if isinstance(src, model.Channel):
            src_channels.append(src)
        if isinstance(src, model.FunctionNode):
            src_channels.extend(src.outputs)

        for src_channel in src_channels:
            for dst_channel in dst_channels:
                if any(link.dst_channel == dst_channel and valid(link) for link in clip.node_collection.links):
                    continue
                self.add_link_callback(None, None, ("create", clip, src_channel, dst_channel))
                break

    def create_io_matrix_window(self, clip, pos=(810, 0), show=True, width=800, height=800):
        io_matrix_tag = get_io_matrix_window_tag(clip)

        def clear_filters(sender, app_data, user_data):
            dpg.set_value("io_matrix.source_filter_text", "")
            dpg.set_value("io_matrix.destination_filter_text", "")
            self.update_io_matrix_window(user_data)

        # Create the I/O Matrix windows after restoring links
        with dpg.window(
            tag=io_matrix_tag,
            label=f"I/O Matrix | {clip.name} ",
            width=width,
            height=height,
            pos=pos, 
            show=show,
            horizontal_scrollbar=True,
            on_close=clear_filters,
            user_data=clip
        ) as window:
            w = 50
            h = 25

            table_tag = f"{io_matrix_tag}.table"
            with dpg.child_window(horizontal_scrollbar=True,track_offset=1.0, tracked=True, tag=f"{io_matrix_tag}.child"):
                def io_matrix_checkbox_callback(sender, app_data, user_data):
                    clip, src_channel, dst_channel = user_data
                    if app_data:
                        self.add_link_callback(None, None, ("create", clip, src_channel, dst_channel))
                    else:
                        link_key = f"{get_node_attribute_tag(clip, src_channel)}:{get_node_attribute_tag(clip, dst_channel)}.gui.link"
                        self.delete_link_callback(None, link_key, (None, clip))
                    dpg.set_value(sender, clip.node_collection.link_exists(src_channel, dst_channel))
                
                def callback(sender, app_data, user_data):
                    self.update_io_matrix_window(user_data)


                dpg.add_input_text(label="Source Filter", on_enter=True, width=100, pos=(10, 25), source=f"io_matrix.source_filter_text", callback=callback, user_data=clip)
                dpg.add_input_text(label="Dest. Filter", on_enter=True, width=100, pos=(210, 25), source=f"io_matrix.destination_filter_text", callback=callback, user_data=clip)
                dpg.add_button(label="Clear Filters", width=100, pos=(410, 25), callback=clear_filters, user_data=clip)
                
                srcs = []
                dsts = []

                src_filter_key = (dpg.get_value("io_matrix.source_filter_text") or "").split()
                dst_filter_key = (dpg.get_value("io_matrix.destination_filter_text") or "").split()

                def join(str1, str2):
                    return f"{str1}.{str2}"

                def matching(name, toks):
                    return all(tok in name for tok in toks) or not toks

                for channel in self.get_all_valid_node_src_channels(clip):
                    if matching(join("Clip", channel.name), src_filter_key):
                        srcs.append(("Clip", channel))

                for channel in self.get_all_valid_dst_channels(clip):
                    if matching(join("Clip", channel.name), dst_filter_key):
                        dsts.append(("Clip", channel))

                y_start = 50
                x_start = 10
                xsum = 10
                all_xpos = []
                for dst_index, (name, dst_channel) in enumerate(dsts):
                    text = join(name, dst_channel.name)
                    xpos = x_start + xsum*8
                    if hasattr(dst_channel, "dmx_channel"):
                        # Should match the link naming scheme
                        tag = get_node_attribute_tag(clip, dst_channel).replace(".node", ".io_matrix_text")
                    else: # a track output
                        tag = f"{dst_channel.id}.io_matrix_text"
                    all_xpos.append(xpos)
                    dpg.add_button(label=text, pos=(xpos, y_start), tag=tag)
                    xsum += len(text)

                for src_index, (name, src_channel) in enumerate(srcs):
                    ypos = h * (src_index + 1)
                    text = join(name, src_channel.name)
                    tag = f"{src_channel.id}.io_matrix_text"
                    dpg.add_button(label=text, pos=(x_start, y_start + ypos), tag=tag)
                    for dst_index, (name, dst_channel) in enumerate(dsts):
                        # TODO: Figure out how to make io matrix not slow down program
                        continue
                        dpg.add_checkbox(
                                tag=f"{get_io_matrix_window_tag(clip)}.{src_channel.id}:{dst_channel.id}.gui.checkbox",
                                default_value=clip.node_collection.link_exists(src_channel, dst_channel),
                                callback=io_matrix_checkbox_callback,
                                user_data=(clip, src_channel, dst_channel),
                                pos=(all_xpos[dst_index] + 50, y_start + ypos)
                        )

    def update_io_matrix_window(self, clip):
        io_matrix_tag = get_io_matrix_window_tag(clip)
        if not dpg.is_item_shown(io_matrix_tag):
            return
        child_tag = f"{io_matrix_tag}.child"
        old_pos = dpg.get_item_pos(io_matrix_tag)
        old_width = dpg.get_item_width(io_matrix_tag)
        old_height = dpg.get_item_height(io_matrix_tag)
        old_x_scroll = dpg.get_x_scroll(child_tag)
        old_y_scroll = dpg.get_y_scroll(child_tag)
        old_show = dpg.is_item_shown(io_matrix_tag)
        dpg.delete_item(io_matrix_tag)
        self.create_io_matrix_window(clip, pos=old_pos, show=old_show, width=old_width, height=old_height)
        dpg.set_x_scroll(child_tag, old_x_scroll)
        dpg.set_y_scroll(child_tag, old_y_scroll)


    def toggle_automation_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "manual" if input_channel.mode == "automation" else "automation"
    
    def enable_recording_mode(self, sender, app_data, user_data):
        input_channel = user_data
        input_channel.mode = "armed"

    def create_automation_window(self, clip, input_channel, action):
        parent = get_automation_window_tag(input_channel)
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
            tab_bar_tag = f"{input_channel.id}.tab_bar"

            def select_preset(sender, app_data, user_data):
                input_channel, automation = user_data
                self.execute_wrapper(f"set_active_automation {input_channel.id} {automation.id}")
                self.reset_automation_plot(input_channel)

            with dpg.menu_bar(tag=menu_tag):

                dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_enable_button", label="Disable" if input_channel.mode == "automation" else "Enable", callback=self.toggle_automation_mode, user_data=input_channel)
                dpg.add_menu_item(tag=f"{input_channel.id}.gui.automation_record_button", label="Record", callback=self.enable_recording_mode, user_data=input_channel)


                def default_time(sender, app_data, user_data):
                    user_data.speed = 0
                dpg.add_menu_item(
                    label="1",
                    callback=default_time,
                    user_data=input_channel,
                )

                def double_time(sender, app_data, user_data):
                    user_data.speed += 1
                dpg.add_menu_item(
                    label="x2",
                    callback=double_time,
                    user_data=input_channel,
                )

                def half_time(sender, app_data, user_data):
                    user_data.speed -= 1
                dpg.add_menu_item(
                    label="/2",
                    callback=half_time,
                    user_data=input_channel,
                )

                def update_automation_length(sender, app_data, user_data):
                    if app_data:
                        input_channel = user_data
                        input_channel.active_automation.set_length(app_data)
                        self.reset_automation_plot(input_channel)

                def update_preset_name(sender, app_data, user_data):
                    input_channel = user_data
                    automation = input_channel.active_automation
                    if automation is None:
                        return
                    automation.name = app_data
                    tab_bar_tag = f"{input_channel.id}.tab_bar"
                    dpg.configure_item(f"{tab_bar_tag}.{automation.id}.button", label=app_data)

                prop_x_start = 600
                dpg.add_text("Preset:", pos=(prop_x_start-200, 0))
                dpg.add_input_text(tag=f"{parent}.preset_name", label="", default_value="", pos=(prop_x_start-150, 0), on_enter=True, callback=update_preset_name, user_data=input_channel, width=100)
                
                dpg.add_text("Beats:", pos=(prop_x_start+200, 0))
                dpg.add_input_text(tag=f"{parent}.beats", label="", default_value=input_channel.active_automation.length, pos=(prop_x_start+230, 0), on_enter=True, callback=update_automation_length, user_data=input_channel, width=50)

            def delete_preset(sender, app_data, user_data):
                input_channel, automation = user_data
                success = self.execute_wrapper(f"delete {automation.id}")
                if success:
                    tab_bar_tag = f"{input_channel.id}.tab_bar"
                    tags_to_delete = [
                        f"{tab_bar_tag}.{automation.id}.button",
                        f"{tab_bar_tag}.{automation.id}.button.x", 
                    ]
                    for tag in tags_to_delete:
                        dpg.delete_item(tag)

            def add_preset(sender, app_data, user_data):
                input_channel = user_data
                success, automation = self.execute_wrapper(f"add_automation {input_channel.id}")
                tab_bar_tag = f"{input_channel.id}.tab_bar"
                if success:
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button", parent=tab_bar_tag, label=automation.name, callback=select_preset, user_data=(input_channel, automation))
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button.x", parent=tab_bar_tag, label="X", callback=delete_preset, user_data=(input_channel, automation))
                    self.reset_automation_plot(input_channel)

            tab_bar_tag = f"{input_channel.id}.tab_bar"
            with dpg.tab_bar(tag=tab_bar_tag):
                for automation_i, automation in enumerate(input_channel.automations):
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button", label=automation.name, callback=select_preset, user_data=(input_channel, automation))
                    dpg.add_tab_button(tag=f"{tab_bar_tag}.{automation.id}.button.x", label="X", callback=delete_preset, user_data=(input_channel, automation))
                dpg.add_tab_button(label="+", callback=add_preset, user_data=input_channel, trailing=True)


            with dpg.plot(label=input_channel.active_automation.name, height=-1, width=-1, tag=plot_tag, query=True, callback=self.print_callback, anti_aliased=True, no_menus=True):
                min_value = input_channel.get_parameter("min").value
                max_value = input_channel.get_parameter("max").value
                dpg.add_plot_axis(dpg.mvXAxis, label="x", tag=f"{plot_tag}.x_axis_limits", no_gridlines=True)
                dpg.set_axis_limits(dpg.last_item(), -AXIS_MARGIN, input_channel.active_automation.length+AXIS_MARGIN)

                dpg.add_plot_axis(dpg.mvYAxis, label="y", tag=f"{plot_tag}.y_axis_limits", no_gridlines=True)
                dpg.set_axis_limits(dpg.last_item(), -min_value, max_value*1.01)

                dpg.add_line_series(
                    [],
                    [],
                    tag=series_tag,
                    parent=dpg.last_item(),
                )

                self.reset_automation_plot(input_channel)

                dpg.add_drag_line(
                    label="Playhead",
                    tag=playhead_tag,
                    color=[255, 255, 0, 255],
                    vertical=True,
                    default_value=0,
                )
                dpg.add_drag_line(
                    label="Ext Value",
                    tag=ext_value_tag,
                    color=[255, 255, 255, 50],
                    vertical=False,
                    default_value=0,
                )
                with dpg.popup(plot_tag, mousebutton=1):
                    dpg.add_menu_item(label="Duplicate", callback=self.double_automation)
                    with dpg.menu(label="Set Quantize"):
                        dpg.add_menu_item(label="Off", callback=self.set_quantize, user_data=None)
                        dpg.add_menu_item(label="1 bar", callback=self.set_quantize, user_data=4)
                        dpg.add_menu_item(label="1/2", callback=self.set_quantize, user_data=2)
                        dpg.add_menu_item(label="1/4", callback=self.set_quantize, user_data=1)
                        dpg.add_menu_item(label="1/8", callback=self.set_quantize, user_data=0.5)
                        dpg.add_menu_item(label="1/16", callback=self.set_quantize, user_data=0.25)


    def set_quantize(self, sender, app_data, user_data):
        self._quantize_amount = user_data
        self.reset_automation_plot(self._active_input_channel)

    def double_automation(self):
       if self._active_input_channel is None:
            return
        
       automation = self._active_input_channel.active_automation
       if automation is None:
            return

       success = self.execute_wrapper(f"double_automation {automation.id}")
       if success:
        self.reset_automation_plot(self._active_input_channel)

    def reset_automation_plot(self, input_channel):
        window_tag = get_automation_window_tag(input_channel)
        automation = input_channel.active_automation
        series_tag = f"{input_channel.id}.series"
        plot_tag = get_plot_tag(input_channel)
        x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
        y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
        

        dpg.configure_item(plot_tag, label=input_channel.active_automation.name)
        dpg.set_axis_limits(x_axis_limits_tag, -AXIS_MARGIN, input_channel.active_automation.length+AXIS_MARGIN)

        dpg.set_value(f"{window_tag}.beats", value=automation.length)
        dpg.set_value(f"{window_tag}.preset_name", value=automation.name)

        # Delete existing points
        for item in self.gui_state.point_tags:
            dpg.delete_item(item)

        # Add new points
        point_tags = []
        for i, x in enumerate(automation.values_x):
            if x is None:
                continue
            y = automation.values_y[i]
            point_tag = f"{input_channel.id}.{automation.id}.series.{i}"
            dpg.add_drag_point(
                color=[0, 255, 255, 255],
                default_value=[x, y],
                callback=self.update_automation_point_callback,
                parent=plot_tag,
                tag=point_tag,
                user_data=input_channel,
                thickness=10,
            )
            point_tags.append(point_tag)
        self.gui_state.point_tags = point_tags

        # Add quantization bars
        if self._quantize_amount is not None:
            i = 0
            while True:
                tag = f"gui.quantization_bar.{i}"
                if dpg.does_item_exist(tag):
                    dpg.delete_item(tag)
                else:
                    break
                i += 1

            def fix_position_callback(sender, app_data, user_data):
                dpg.set_value(sender, user_data)

            n_bars = int(input_channel.active_automation.length / self._quantize_amount)
            for i in range(n_bars+1):
                tag = f"gui.quantization_bar.{i}"
                value = i * self._quantize_amount
                dpg.add_drag_line(
                    tag=tag,
                    color=[255, 255, 255, 50],
                    vertical=True,
                    default_value=value,
                    parent=get_plot_tag(input_channel),
                    callback=fix_position_callback,
                    user_data=value
                )

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
            tag="io.gui.window"
        ) as window:
            output_table_tag = f"io.outputs.table"
            input_table_tag = f"io.inputs.table"

            def set_io_type(sender, app_data, user_data):
                index, io_type, input_output, *args = user_data
                table_tag = f"io.{input_output}.table"
                self.gui_state.io_types[input_output][int(index)] = io_type
                dpg.configure_item(f"{table_tag}.{index}.type", label=io_type.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io_type.arg_template if not args else args[0])

            def add_io(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    _, index, input_output = user_data
                    io_type = self.gui_state.io_types[input_output][int(index)]
                    success, io = state.execute(f"create_io {index} {input_output} {io_type.type} {app_data}")
                    
                    if not success:
                        raise RuntimeError("Failed to create IO")

                    self.gui_state.io_args[input_output][index] = app_data
                else: # restore
                    _, index, io = user_data

                table_tag = f"io.{input_output}.table"
                dpg.configure_item(f"{table_tag}.{index}.type", label=io.nice_title)
                dpg.set_value(f"{table_tag}.{index}.arg", value=io.arg_string)

            def connect(sender, app_data, user_data):
                _, index, input_output = user_data
                table_tag = f"io.{input_output}.table"
                add_io(sender, dpg.get_value(f"{table_tag}.{index}.arg"), user_data)

            def hide_midi_menu_and_set_io_type(sender, app_data, user_data):
                dpg.configure_item("midi_devices_window", show=False)
                set_io_type(sender, app_data, user_data)

            def create_and_show_midi_menu(sender, app_data, user_data):
                try:
                    dpg.delete_item("midi_devices_window")
                except:
                    pass

                i, in_out = user_data
                devices = mido.get_input_names() if in_out == "inputs" else mido.get_output_names() 
                with dpg.window(tag="midi_devices_window", no_title_bar=True, max_size=(200, 400), pos=(self.mouse_x, self.mouse_y)):
                    for device_name in devices:
                        dpg.add_menu_item(label=device_name, callback=hide_midi_menu_and_set_io_type, user_data=(i, model.MidiDevice, in_out, device_name))

            with dpg.table(header_row=True, tag=input_table_tag):
                type_column_tag = f"{input_table_tag}.column.type"
                arg_column_tag = f"{input_table_tag}.column.arg"
                connected_column_tag = f"{input_table_tag}.column.connected"
                dpg.add_table_column(label="Input Type", tag=type_column_tag)
                dpg.add_table_column(label="Input", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connect", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        input_type = model.IO_INPUTS[i]
                        type_tag = f"{input_table_tag}.{i}.type"
                        dpg.add_button(label="Select Input Type" if input_type is None else input_type.nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_input_type in model.ALL_INPUT_TYPES:
                                if io_input_type.type == "midi":
                                    dpg.add_menu_item(label=io_input_type.nice_title, callback=create_and_show_midi_menu, user_data=(i, "inputs"))
                                else:
                                    dpg.add_menu_item(label=io_input_type.nice_title, callback=set_io_type, user_data=(i, io_input_type, "inputs"))
                        
                        arg_tag = f"{input_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=add_io, user_data=("create", i, "inputs"))

                        connected_tag = f"{input_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "inputs"))

            with dpg.table(header_row=True, tag=output_table_tag):
                type_column_tag = f"{output_table_tag}.column.type"
                arg_column_tag = f"{output_table_tag}.column.arg"
                connected_column_tag = f"{output_table_tag}.column.connected"
                dpg.add_table_column(label="Output Type", tag=type_column_tag)
                dpg.add_table_column(label="Output", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connect", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        type_tag = f"{output_table_tag}.{i}.type"
                        dpg.add_button(label="Select Output Type" if model.IO_OUTPUTS[i] is None else model.IO_OUTPUTS[i].nice_title, tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_output_type in model.ALL_OUTPUT_TYPES:
                                if io_output_type.type == "midi":
                                    dpg.add_menu_item(label=io_output_type.nice_title, callback=create_and_show_midi_menu, user_data=(i, "outputs"))
                                else:
                                    dpg.add_menu_item(label=io_output_type.nice_title, callback=set_io_type, user_data=(i, io_output_type, "outputs"))

                        arg_tag = f"{output_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=add_io, user_data=("create", i, "outputs"))
                        
                        connected_tag = f"{output_table_tag}.{i}.connected"
                        dpg.add_button(label="Connect", callback=connect, user_data=("create", i, "outputs"))

        ###############
        ### Restore ###
        ###############

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
                dpg.add_table_column(label="Outputs", tag=f"{output_table_tag}.column.dmx_channel")
                dpg.add_table_column(label="Name", tag=f"{output_table_tag}.column.name")
                dpg.add_table_column(tag=f"{output_table_tag}.column.delete", width=10)

        ###############
        ### Restore ###
        ###############
        for output_index, output_channel in enumerate(track.outputs):
            if output_channel.deleted:
                continue
            self.add_track_output(sender=None, app_data=None, user_data=("restore", track, output_channel))

    def add_track_output(self, sender, app_data, user_data):
        action = user_data[0]
        track = user_data[1]
        if action == "create":
            success, output_channel = state.execute(f"create_output {track.id}")
            if not success:
                return
        else: # restore
            output_channel = user_data[2]

        output_table_tag = f"{get_output_configuration_window_tag(track)}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel.id}.gui.row"
        with dpg.table_row(parent=output_table_tag, tag=output_table_row_tag):
            dpg.add_input_int(tag=f"{output_channel.id}.dmx_channel", width=75, default_value=output_channel.dmx_channel, callback=self.update_channel_attr, user_data=(output_channel, "dmx_channel"))
            dpg.add_input_text(tag=f"{output_channel.id}.name", default_value=output_channel.name, callback=self.update_channel_attr, user_data=(output_channel, "name"), width=80)
            dpg.add_button(label="X", callback=self._delete_track_output, user_data=(track, output_channel))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_node(clip, output_channel)

    def add_fixture(self, sender, app_data, user_data):
        track = user_data[0]
        fixture = user_data[1]
        starting_address = fixture.address

        for output_channel in track.outputs:
            starting_address = max(starting_address, output_channel.dmx_channel + 1)

        for ch, name in enumerate(fixture.channels):
            self.add_track_output(None, None, ("create", track))
            output_channel = track.outputs[-1]
            self.update_channel_attr(None, starting_address + ch, (output_channel, "dmx_channel"))
            self.update_channel_attr(None, name, (output_channel, "name"))
            dpg.set_value(f"{output_channel.id}.dmx_channel", starting_address + ch)
            dpg.set_value(f"{output_channel.id}.name", name)

    ###

    def _delete_link(self, link_tag, link_key, clip):
        src_node_attribute_tag, dst_node_attribute_tag = link_key.split(":")
        src_id = src_node_attribute_tag.replace(".node_attribute", "").split(".", 1)[-1]
        dst_id = dst_node_attribute_tag.replace(".node_attribute", "").split(".", 1)[-1]
        success = self.execute_wrapper(f"delete_link {clip.id} {src_id} {dst_id}")
        if success:              
            dpg.delete_item(link_tag)
        else:
            raise RuntimeError(f"Failed to delete: {link_key}")
        self.update_io_matrix_window(clip)

    def _delete_node_gui(self, node_tag, obj_id):
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        obj = self.state.get_obj(obj_id)
        channels_to_delete = []
        if isinstance(obj, model.Channel):
            # Input Nodes (also need to delete automation window)
            channels_to_delete = [obj]
            automation_window_tag = get_automation_window_tag(obj_id, is_id=True)
            if automation_window_tag in all_aliases:
                dpg.delete_item(automation_window_tag)

        # Function Nodes have their own inputs/outputs that we need to delete
        # corresponding links.
        if isinstance(obj, model.FunctionNode):
            channels_to_delete.extend(obj.inputs)
            channels_to_delete.extend(obj.outputs)

        self.delete_associated_links(channels_to_delete)
        
        # Finally, delete the node from the Node Editor
        dpg.delete_item(node_tag)

        # Update the matrix
        self.update_io_matrix_window(self._active_clip)

    def delete_associated_links(self, channels):
        # Delete any links attached to this node
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        ids = [channel.id for channel in channels]
        link_tags = [alias for alias in all_aliases if alias.endswith(".gui.link")]
        for id_ in ids:
            for link_tag in link_tags:
                if id_ in link_tag:
                    self._delete_link(link_tag, link_tag.replace(".gui.link", ""), self._active_clip)

    def _delete_track_output(self, _, __, user_data):
        with self.gui_lock:
            track, output_channel = user_data
            # Delete the entire window, since we will remake it later.
            parent = get_output_configuration_window_tag(track)
            dpg.delete_item(parent)

            success = self.execute_wrapper(f"delete {output_channel.id}")
            if success:
                # Delete each Node from each clip's node editor
                for clip_i, clip in enumerate(track.clips):
                    if clip is None:
                        continue
                    self._delete_node_gui(get_node_tag(clip, output_channel), output_channel.id)

                # Remake the window
                self.create_track_output_configuration_window(track, show=True)
            else:
                RuntimeError(f"Failed to delete: {output_channel.id}")
             
    def delete_selected_nodes(self):
        node_editor_tag = get_node_editor_tag(self._active_clip)

        for item in dpg.get_selected_nodes(node_editor_tag):                    
            alias = dpg.get_item_alias(item)
            node_id = alias.replace(".node", "").rsplit(".", 1)[-1]
            # Deleting outputs from the Node Editor GUI is not allowed.
            if "DmxOutput" in node_id:
                continue
            success = self.execute_wrapper(f"delete {node_id}")
            if success:
                self._delete_node_gui(alias, node_id)
            else:
                RuntimeError(f"Failed to delete: {node_id}")

        for item in dpg.get_selected_links(node_editor_tag):
            alias = dpg.get_item_alias(item)
            link_key = alias.replace(".gui.link", "")
            self._delete_link(alias, link_key, self._active_clip)

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
                if item_id.startswith("DmxOutput"):
                    continue
                new_copy_buffer.append(item_id)
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
            new_objs = {}
            link_ids = []
            for item_id in self.copy_buffer:
                obj = self.state.get_obj(item_id, missing_ok=True)
                if obj is None:
                    link_ids.append(item_id)
                elif isinstance(obj, model.ClipInputChannel):
                    success, new_input_channel = self.execute_wrapper(f"duplicate_input {self._active_clip.id} {item_id}")
                    if success:
                        self.add_input_node(sender=None, app_data=None, user_data=("restore", (self._active_clip, new_input_channel), False))
                        duplicate_map[item_id] = new_input_channel
                        new_objs[new_input_channel.id] = new_input_channel
                    else:
                        raise RuntimeError(f"Failed to duplicate {item_id}")
                elif isinstance(obj, model.FunctionNode):
                    success, new_node, id_to_ptrs = self.execute_wrapper(f"duplicate_node {self._active_clip.id} {item_id}")
                    if success:
                        if isinstance(obj, model.FunctionCustomNode):
                            self.add_custom_function_node(sender=None, app_data=None, user_data=("restore", (self._active_clip, new_node), False))
                        else:
                            self.add_function_node(sender=None, app_data=None, user_data=("restore", (self._active_clip, new_node), False))
                        duplicate_map[item_id] = new_node
                        new_objs[new_node.id] = new_node
                        for i, input_channel in enumerate(obj.inputs):
                            duplicate_map[input_channel.id] = new_node.inputs[i]
                            new_objs[input_channel.id] = input_channel
                        for i, output_channel in enumerate(obj.outputs):
                            duplicate_map[output_channel.id] = new_node.outputs[i]                         
                            new_objs[output_channel.id] = output_channel
                    else:
                       raise RuntimeError("Failed to duplicate_node")
                else:
                        raise RuntimeError(f"Failed to duplicate {item_id}")
            
            # First replace old ids with new ids in selected links
            new_link_ids = []
            for link_id in link_ids:   
                new_link_id = link_id
                for old_id, new_obj in duplicate_map.items():
                    new_link_id = new_link_id.replace(old_id, new_obj.id)
                new_link_ids.append(new_link_id)

            # Create new links
            for link_id in new_link_ids:
                src_tag, dst_tag = link_id.split(":")
                self.add_link_callback(sender=None, app_data=(src_tag, dst_tag), user_data=("create", self._active_clip))

        elif window_tag_alias == "clip.gui.window":
            if self._active_clip_slot is not None:
                self.paste_clip(self._active_clip_slot[0], self._active_clip_slot[1])

    def paste_clip(self, track_i, clip_i):
        if not self.copy_buffer:
            return
        obj = self.copy_buffer[0]
        if not isinstance(obj, model.Clip):
            return

        clip_id = obj.id
        success, new_clip = self.execute_wrapper(f"duplicate_clip {track_i} {clip_i} {clip_id} ")
        if success:
            self.populate_clip_slot(track_i, clip_i)
        else:
            raise RuntimeError(f"Failed to duplicate clip {clip_id}")

        self.save_last_active_clip()
        self._active_track = self.state.tracks[track_i]
        self._active_clip = new_clip
        self.update_clip_window()

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

    def get_all_valid_track_output_channels(self):
        output_channels = []
        for track in self.state.tracks:
            for output_channel in track.outputs:
                if output_channel.deleted:
                    continue
                output_channels.append(output_channel)
        return output_channels

    def get_all_valid_node_src_channels(self, clip):
        src_channels = []
        for input_channel in clip.inputs:
            if input_channel.deleted:
                continue
            src_channels.append(input_channel)
        for node in clip.node_collection.nodes:
            if node.deleted:
                continue
            for output_channel in node.outputs:
                if output_channel.deleted:
                    continue
                src_channels.append(output_channel)
        return src_channels

    def get_all_valid_dst_channels(self, clip):
        if clip is None:
            return []
        dst_channels = []
        for output_channel in clip.outputs:
            if output_channel.deleted:
                continue
            dst_channels.append(output_channel)
        for node in clip.node_collection.nodes:
            if node.deleted:
                continue
            for input_channel in node.inputs:
                if input_channel.deleted:
                    continue
                dst_channels.append(input_channel)
        return dst_channels

    def update_state_from_gui(self):
        pass

    def update_gui_from_state(self):
        dpg.configure_item("play_button", label="[Pause]" if self.state.playing else "[Play]")

        if valid(self._active_clip):
            # This is only setting the GUI value, so we only need to update the active clip.
            for dst_channel in self.get_all_valid_dst_channels(self._active_clip):
                if hasattr(dst_channel, "dmx_channel"):
                    tag = get_output_node_value_tag(self._active_clip, dst_channel)
                else:
                    tag = f"{dst_channel.id}.value"
                dpg.set_value(tag, dst_channel.get())

            # This is only setting the GUI value, so we only need to update the active clip.
            for src_channel in self.get_all_valid_node_src_channels(self._active_clip):
                tag = f"{src_channel.id}.value"
                dpg.set_value(tag, src_channel.get())


        # Update automation points
        if valid(self._active_input_channel) and valid(self._active_input_channel.active_automation):
            automation = self._active_input_channel.active_automation
            values = sorted(
                zip(automation.values_x, automation.values_y), 
                key=lambda t: t[0] if t[0] is not None else 0
            )
            xs = np.arange(0, self._active_input_channel.active_automation.length, 0.01)
            ys = self._active_input_channel.active_automation.f(xs)
            dpg.configure_item(
               f"{self._active_input_channel.id}.series",
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
            if valid(self._active_input_channel.active_automation):
                playhead_tag = f"{self._active_input_channel.id}.gui.playhead"
                ext_value_tag = f"{self._active_input_channel.id}.gui.ext_value"
                playhead_color = {
                    "armed": [255, 100, 100, 255],
                    "recording": [255, 0, 0, 255],
                    "automation": [255, 255, 0, 255],
                    "manual": [200, 200, 200, 255],
                }
                dpg.configure_item(playhead_tag, color=playhead_color[self._active_input_channel.mode])
                dpg.configure_item(f"{self._active_input_channel.id}.gui.automation_enable_button", label="Disable" if self._active_input_channel.mode == "automation" else "Enable")
                dpg.set_value(
                    playhead_tag, 
                    self._active_input_channel.last_beat % self._active_input_channel.active_automation.length if self._active_input_channel.mode in ["automation", "armed", "recording"] else 0
                )
                dpg.set_value(ext_value_tag, self._active_input_channel.ext_get())

        if model.LAST_MIDI_MESSAGE is not None:
            device_name, message = model.LAST_MIDI_MESSAGE
            channel = message.channel
            note_control = message.control if message.is_cc() else message.note
            dpg.set_value("last_midi_message", "" if model.LAST_MIDI_MESSAGE is None else f"{device_name}: {channel}/{note_control}")


    def select_clip_callback(self, sender, app_data, user_data):
        track, clip = user_data

        self.save_last_active_clip()

        self._active_track = track
        self._active_clip = clip
        self.update_clip_window()

        for tag in self.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(clip), show=True)

    def select_clip_slot_clip_callback(self, sender, app_data, user_data):
        track_i = int(user_data[1])
        clip_i = int(user_data[2])
        self._active_clip_slot = (track_i, clip_i)
        self._active_track = self.state.tracks[track_i]
        self.update_clip_window()

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
                window_x, window_y = dpg.get_item_pos(tag)
                window_x2, window_y2 = window_x + dpg.get_item_width(tag), window_y + dpg.get_item_height(tag)
                self.node_editor_window_is_focused = inside((self.mouse_x, self.mouse_y), (window_x, window_x2, window_y+10, window_y2))
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
            if window_tag is not None and window_tag.endswith("automation_window"):
                plot_mouse_pos = dpg.get_plot_mouse_pos()
                automation = self._active_input_channel.active_automation
                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                    y_axis_limits_tag = f"{self._active_input_channel.id}.plot.y_axis_limits"
                    if norm_distance((x,y), plot_mouse_pos, dpg.get_axis_limits(x_axis_limits_tag), dpg.get_axis_limits(y_axis_limits_tag)) <= 0.015:
                        if self.execute_wrapper(f"remove_automation_point {self._active_input_channel.id} {i}"):
                            point_tag = f"{self._active_input_channel.id}.series.{i}"
                            dpg.delete_item(point_tag)
                        return

                point = self._quantize_point(*plot_mouse_pos, self._active_input_channel.dtype, automation.length, quantize_x=False)
                success = self.execute_wrapper(
                    f"add_automation_point {automation.id} {point[0]},{point[1]}"
                )
                if success:
                    self.reset_automation_plot(self._active_input_channel)

    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)
        #print(key_n)
        if key == " ":
            self.state.playing = not self.state.playing
            if self.state.playing:
                self.state.play_time_start = time.time()
        elif key_n in [8, 46] and self.node_editor_window_is_focused and self.ctrl:
            self.delete_selected_nodes()
        elif key_n in [120]:
            if self._active_input_channel is not None:
                self.enable_recording_mode(None, None, self._active_input_channel)
        elif key_n in [9]:
            self.toggle_node_editor_fullscreen()
        elif key in ["C"]:
            if self.ctrl:
                self.copy_selected()
        elif key in ["V"]:
            if self.ctrl:
                with self.gui_lock:
                    self.paste_selected()
        elif key in ["O"]:
            if self.ctrl and self.shift:
                if self._active_track:
                    self.add_track_output(None, None, ("create", self._active_track))
            elif self.ctrl:
                self.open_menu_callback()
        elif key in ["I"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None,( "create", (self._active_clip, "int"), False))
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None, ("create", (self._active_clip, "bool"), False))
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None, ("create", (self._active_clip, "float"), False))
        elif key in ["T"]:
            if self.state.mode == "performance":
                self.tap_tempo()
        elif key in ["R"]:
            if self.state.mode == "performance":
                self.reset_time()
        elif key in ["N"]:
            if self.ctrl:
                for track_i, track in enumerate(self.state.tracks):
                    if track == self._active_track:
                        for clip_i, clip in enumerate(track.clips):
                            if clip is None:
                                self.create_new_clip(None, None, ("create", track_i, clip_i))
                                return
        elif key in ["S"]:
            if self.shift and self.ctrl:
                self.save_as_menu_callback()
            elif self.ctrl:
                self.save_menu_callback()

        elif key_n in [187]:
            if self.ctrl and self.shift:
                if self._last_add_function_node is not None:
                    self.add_function_node(*self._last_add_function_node)


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
        input_channel = user_data
        automation = input_channel.active_automation
        tag = dpg.get_item_alias(sender)
        point_id, point_index = tag.split(".series.")
        point_index = int(point_index)

        x, y, *_ = dpg.get_value(sender)
        max_x_i = automation.values_x.index(max(automation.values_x, key=lambda x: x or 0))
        original_x = automation.values_x[point_index]
        if point_index in [0, max_x_i]:
            dpg.set_value(sender, (original_x, y))
            x = original_x

        quantize_x = True
        delta_x = x - original_x
        if self._quantize_amount is not None and (abs(delta_x) < self._quantize_amount/3):
            x = original_x
            quantize_x = False

        x, y = self._quantize_point(x, y, input_channel.dtype, automation.length, quantize_x=quantize_x)
        dpg.set_value(sender, (x, y))

        success = self.execute_wrapper(f"update_automation_point {automation.id} {point_index} {x},{y}")
        if not success:
            raise RuntimeError("Failed to update automation point")

    def _quantize_point(self, x, y, dtype, length, quantize_x=True):
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
            x2 = min(length - 0.0001, max(0, x2))

        return x2, y2

    def save(self):
        for track_i, track in enumerate(self.state.tracks):
            track_ptr = f"*track[{track_i}]"
            for clip_i, clip in enumerate(track.clips):
                clip_ptr = f"{track_ptr}.clip[{clip_i}]"
                if clip is None:
                    continue
                for input_i, input_channel in enumerate(clip.inputs):
                    if not valid(input_channel):
                        continue
                    ptr = f"{clip_ptr}.in[{input_i}]"
                    self.gui_state.node_positions[ptr] = dpg.get_item_pos(get_node_tag(clip, input_channel))
                    self.gui_state.axis_limits[ptr] = {}
                    self.gui_state.axis_limits[ptr]['x'] = dpg.get_axis_limits(get_plot_tag(input_channel)+".x_axis_limits")
                    self.gui_state.axis_limits[ptr]['y'] = dpg.get_axis_limits(get_plot_tag(input_channel)+".y_axis_limits")
                    for automation_i, automation in enumerate(input_channel.automations):
                        aptr = f"{ptr}.automation[{automation_i}]"
                for output_i, output_channel in enumerate(clip.outputs):
                    if not valid(output_channel):
                        continue
                    ptr = f"{clip_ptr}.out[{output_i}]"
                    self.gui_state.node_positions[ptr] = dpg.get_item_pos(get_node_tag(clip, output_channel))
                for node_i, node in enumerate(clip.node_collection.nodes):
                    if not valid(node):
                        continue
                    node_ptr = f"{clip_ptr}.node[{node_i}]"
                    self.gui_state.node_positions[node_ptr] = dpg.get_item_pos(get_node_tag(clip, node))

        if self.state.project_filepath is not None:
            self.state.project_name = os.path.basename(self.state.project_filepath).replace(f".{PROJECT_EXTENSION}", "")
            with open(self.state.project_filepath, "w") as f:
                self.state.dump_state(f)
            with open(self.state.project_filepath + ".gui", "wb") as f:
                pickle.dump(self.gui_state, f)

            dpg.set_viewport_title(f"NodeDMX [{self.state.project_name}]")


    def restore_gui_state(self):
        for ptr, pos in self.gui_state.node_positions.items():
            clip = self.state.get_clip_from_ptr(ptr)
            obj = self.state.get_obj(ptr)
            if obj.deleted:
                continue
            tag = get_node_tag(clip, obj)
            dpg.set_item_pos(tag, pos)

        for ptr, axis_limits in self.gui_state.axis_limits.items():
            input_channel = self.state.get_obj(ptr)
            if input_channel.deleted:
                continue
            dpg.set_axis_limits(get_plot_tag(input_channel)+".x_axis_limits", axis_limits['x'][0], axis_limits['x'][1])
            dpg.set_axis_limits(get_plot_tag(input_channel)+".y_axis_limits", axis_limits['y'][0], axis_limits['y'][1])

        for i, value in self.gui_state.io_types["inputs"].items():
            dpg.configure_item(f"io.inputs.table.{i}.type", label=value.nice_title)
        for i, value in self.gui_state.io_types["outputs"].items():
            dpg.configure_item(f"io.outputs.table.{i}.type", label=value.nice_title)

        for i, value in self.gui_state.io_args["inputs"].items():
            dpg.set_value(f"io.inputs.table.{i}.arg", value)
        for i, value in self.gui_state.io_args["outputs"].items():
            dpg.set_value(f"io.outputs.table.{i}.arg", value)

    def restore(self, path):
        with open(path, 'r') as f:
            self.new_state = model.ProgramState()
            self.new_state.deserialize(f)
        try:
            with open(path + ".gui", 'rb') as f:
                self.new_gui_state = pickle.load(f)
        except:
            self.new_gui_state = GuiState()

        print("[Stopping]")
        dpg.stop_dearpygui()

    def save_last_active_clip(self):
        if self._active_track is not None and self._active_clip is not None:
            self.gui_state.track_last_active_clip[self._active_track.id] = self._active_clip.id

state = model.ProgramState()
gui_state = GuiState()

with Profile() as profile:
    while state is not None:
        gui = Gui()
        state, gui_state = gui.run(state, gui_state)
        print("[Done]")

Stats(profile).strip_dirs().sort_stats(SortKey.CALLS).print_stats()