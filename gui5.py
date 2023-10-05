import dearpygui.dearpygui as dpg
import model
import fixtures
import re
from copy import copy
import math
import time
import pickle
from threading import Lock

SCREEN_WIDTH = 1940

SCREEN_HEIGHT = 1150

FILE_EXTENSION = "ndmx"


def norm_distance(p1, p2, x_limit, y_limit):
    np1 = p1[0]/x_limit[1], p1[1]/y_limit[1],
    np2 = p2[0]/x_limit[1], p2[1]/y_limit[1],
    return math.sqrt((np2[0] - np1[0]) ** 2 + (np2[1] - np1[1]) ** 2)

def inside(p1, rect):
    x = rect[0] <= p1[0] <= rect[1]
    y = rect[2] <= p1[1] <= rect[3]
    return x and y

def valid(obj):
    return obj is not None and not getattr(obj, "deleted", False)


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


def get_node_editor_tag(clip):
    return f"{clip.id}.gui.node_window.node_editor"

def get_output_configuration_window_tag(track):
    return f"{track.id}.gui.output_configuration_window"

def get_io_matrix_window_tag(clip):
    return f"{clip.id}.gui.io_matrix_window"

def get_automation_window_tag(input_channel, is_id=False):
    return f"{input_channel if is_id else input_channel.id}.gui.automation_window"

def get_plot_tag(input_channel):
    return f"{input_channel.id}.plot"

def get_node_tag(clip, obj):
    return f"{get_node_editor_tag(clip)}.{obj.id}.node"

def get_node_window_tag(clip):
    return f"{clip.id}.gui.node_window"

def get_output_node_attribute_tag(clip, output_channel):
    return f"{clip.id}.{output_channel.id}.output.gui.node"

def get_output_node_value_tag(clip, output_channel):
    return f"{clip.id}.{output_channel.id}.output.value"

def show_callback(sender, app_data, user_data):
    dpg.configure_item(user_data, show=True)

class Gui:

    def __init__(self):
        self.tags = {}
        
        self.state = None
        self.new_state = None
        
        self.gui_state = None
        self.new_gui_state = None

        self._active_output_channel = None
        self._active_input_channel = None
        self._active_track = None
        self._inspecter_x = list(range(500))

        self._active_clip = None
        self._last_add_function_node = None

        self._tap_tempo_buffer = [0, 0, 0, 0, 0, 0]

        self.ctrl = False
        self.shift = False

        self.gui_lock = Lock()

    def run(self, state, gui_state):
        self.state = state
        self.gui_state = gui_state
        self.tags = {
            "hide_on_clip_selection": [],
            "node_window": [],
        }
        dpg.create_context()
        dpg.show_metrics()
        dpg.show_item_registry()

        #### Create Clip Window ####
        clip_window = dpg.window(label="Clip", pos=(0,18), width=800, height=520, no_move=True, no_title_bar=True, no_resize=True)
        with clip_window as window:
            table_tag = f"clip_window.table"
            with dpg.table(header_row=False, tag=table_tag,
                   borders_innerH=True, borders_outerH=True, borders_innerV=True,
                   borders_outerV=True, policy=dpg.mvTable_SizingStretchProp, resizable=True):

                for track_i in range(len(self.state.tracks)):
                    dpg.add_table_column()

                with dpg.table_row():
                    for track_i, track in enumerate(self.state.tracks):


                        # When user clicks on the track title, bring up the output configuration window.
                        def open_output_configuration_window(sender, app_data, user_data):
                            for track in self.state.tracks:
                                output_configuration_window_tag = get_output_configuration_window_tag(track)
                                dpg.configure_item(tag, show=False)
                            dpg.configure_item(get_output_configuration_window_tag(user_data), show=True)
                            self._active_track = user_data

                        track_title_button_tag = f"{track.id}.gui.button"
                        dpg.add_button(label=track.name, tag=track_title_button_tag, callback=open_output_configuration_window, user_data=track, width=75)


                clips_per_track = len(self.state.tracks[0].clips)
                for clip_i in range(clips_per_track):
                    # Row
                    with dpg.table_row(height=10):
                        for track_i, track in enumerate(self.state.tracks):
                            # Col
                            clip = track.clips[clip_i]
                            with dpg.table_cell() as cell_tag:
                                with dpg.group(horizontal=True, horizontal_spacing=5) as group_tag:
                                    def play_clip_callback(sender, app_data, user_data):
                                        track, clip = user_data
                                        if self.state.execute(f"toggle_clip {track.id} {clip.id}"):
                                            self.update_clip_status()

                                    def add_clip_elements(action, track, clip, group_tag):
                                        dpg.add_button(arrow=True, direction=dpg.mvDir_Right, tag=f"{clip.id}.gui.play_button", callback=play_clip_callback, user_data=(track,clip), parent=group_tag)                        
                                        dpg.add_text(
                                            label=clip.title,
                                            tag=f"{clip.id}.title",
                                            parent=group_tag,
                                            default_value=clip.title,
                                        )
                                        self.register_clicked_handler(group_tag, self.select_clip_callback, user_data=(track, clip))
                                        self._active_track = track
                                        self._active_clip = clip
                                        if action == "create":
                                            self.update_clip_status()
    
                                    def add_clip_callback(sender, app_data, user_data):
                                        action, group_tag, track = user_data[:3]
                                        if action == "create":
                                            clip_i = user_data[3]
                                            success, clip = self.state.execute(f"new_clip {track.id},{clip_i}")
                                            if success:
                                                dpg.delete_item(sender)
                                            else:
                                                raise RuntimeError("Failed to create clip")
                                        else: # restore
                                            clip = user_data[3]
                                        add_clip_elements(action, track, clip, group_tag)
                                        with self.gui_lock:
                                            self.create_node_editor_window(clip)

                                    if clip is None:
                                        dpg.add_button(
                                            label="         ",
                                            callback=add_clip_callback,
                                            user_data=("create", group_tag, track, clip_i)
                                        )
                                    else:
                                        add_clip_callback(None, None, ("restore", group_tag, track, clip))

                self.update_clip_status()

        #### Mouse Handlers ####
        with dpg.handler_registry():
            dpg.add_mouse_double_click_handler(
                callback=self.mouse_double_click_callback, user_data=self.state
            )
            dpg.add_key_press_handler(
                callback=self.key_press_callback, user_data=self.state
            )
            dpg.add_key_down_handler(
                callback=self.key_down_callback, user_data=self.state
            )
            dpg.add_key_release_handler(
                callback=self.key_release_callback, user_data=self.state
            )

        # Create Viewport
        dpg.create_viewport(title=f"NodeDMX [{self.state.project_name}]", width=SCREEN_WIDTH, height=SCREEN_HEIGHT, x_pos=50, y_pos=0)

        # File Dialogs
        def open_menu_callback(self):
            dpg.configure_item("open_file_dialog", show=True)

        def save_menu_callback():
            if self.state.project_filepath is None:
                dpg.configure_item("save_file_dialog", show=True)
            self.save()


        def save_as_menu_callback():
            dpg.configure_item("save_file_dialog", show=True)

        def save_callback(sender, app_data):
            self.state.project_filepath = app_data["file_path_name"]
            if not self.state.project_filepath.endswith(f".{FILE_EXTENSION}"):
                self.state.project_filepath += f".{FILE_EXTENSION}"
            self.save()

        def restore_callback(sender, app_data):
            self.restore(app_data["file_path_name"])

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

            for tag in ["open_file_dialog", "save_file_dialog"]:
                dpg.add_file_extension(f".{FILE_EXTENSION}", color=[255, 255, 0, 255], parent=tag)

            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open", callback=open_menu_callback)
                dpg.add_menu_item(label="Save", callback=save_menu_callback)
                dpg.add_menu_item(label="Save As", callback=save_as_menu_callback)

            with dpg.menu(label="View"):
                dpg.add_menu_item(label="I/O", callback=lambda: dpg.configure_item("io_window", show=True))
                dpg.add_menu_item(label="Inspector", callback=lambda: dpg.configure_item("inspector_window", show=True))

            dpg.add_menu_item(label="Help", callback=self.print_callback)

            
            # Transport 
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

    def register_clicked_handler(self, tag, function, user_data):
        handler_registry_tag = f"{tag}.item_handler_registry"
        with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
            dpg.add_item_clicked_handler(callback=function, user_data=user_data)
        dpg.bind_item_handler_registry(tag, handler_registry_tag)

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

    def update_clip_status(self):
        for track_i, track in enumerate(self.state.tracks):
            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue

                # In edit mode the active clip should always play.
                if self.state.mode == "edit":
                    if self._active_clip == clip:
                        if not clip.playing:
                            clip.start()
                    else:
                        clip.stop()

                value = "|>" if clip.playing else "[]"
                button_tag = f"{clip.id}.gui.play_button"
                dpg.configure_item(button_tag, label=value)

                if clip == self._active_clip:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 100,155,255])                    
                elif clip.playing:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 155,100,255])
                else:
                    dpg.highlight_table_cell("clip_window.table", clip_i + 1, track_i, color=[0, 50, 100,255])

    def main_loop(self):
        print("Running main loop")
        try:
            while dpg.is_dearpygui_running():
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
        with dpg.window(
            tag=window_tag,
            label=f"Node Window | {clip.title}",
            width=SCREEN_WIDTH * 9.9 / 10,
            height=570,
            pos=(0, 540),
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
            
            with dpg.menu_bar():
                with dpg.menu(label="New Input"):
                    dpg.add_menu_item(label="Bool", callback=self.add_input_node, user_data=("create", (clip, "bool")))
                    dpg.add_menu_item(label="Integer", callback=self.add_input_node, user_data=("create", (clip, "int")))
                    dpg.add_menu_item(label="Float", callback=self.add_input_node, user_data=("create", (clip, "float")))

                with dpg.menu(label="Functions"):
                    dpg.add_menu_item(
                        label="Binary Operator", user_data=("create", "binary_operator", ",", clip), callback=self.add_function_node
                    )
                    with dpg.menu(label="Demux"):
                        for i in range(1, 16):
                            dpg.add_menu_item(  
                                label=i, user_data=("create", "demux", i, clip), callback=self.add_function_node
                            )
                    with dpg.menu(label="Multiplexer"):
                        for i in range(1, 16):
                            dpg.add_menu_item(  
                                label=i, user_data=("create", "multiplexer", i, clip), callback=self.add_function_node
                            )
                    dpg.add_menu_item(
                        label="Random", user_data=("create", "random", ",", clip), callback=self.add_function_node
                    )                
                    dpg.add_menu_item(
                        label="Sample", user_data=("create", "sample", ",", clip), callback=self.add_function_node
                    ) 
                    dpg.add_menu_item(
                        label="Buffer", user_data=("create", "buffer", ",", clip), callback=self.add_function_node
                    )                

                def show_io_matrix(sender, app_Data, user_data):
                    clip = user_data
                    self.update_io_matrix_window(clip)
                    dpg.configure_item(get_io_matrix_window_tag(clip), show=True)


                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="I/O Matrix", callback=show_io_matrix, user_data=clip)

                dpg.add_menu_item(label="[Delete Selected]", callback=self.delete_selected_nodes)

                dpg.add_text(default_value="Clip Name:")
                dpg.add_input_text(source=f"{clip.id}.title", width=75)

        ###############
        ### Restore ###
        ###############
        self.create_io_matrix_window(clip, show=False)

        for input_index, input_channel in enumerate(clip.inputs):
            if input_channel.deleted:
                continue
            self.add_input_node(sender=None, app_data=None, user_data=("restore", (clip, input_channel)))

        for output_index, output_channel in enumerate(clip.outputs):
            if output_channel.deleted:
                continue
            self.add_output_node(clip, output_channel)

        for node_index, node in enumerate(clip.node_collection.nodes):
            if node.deleted:
                continue
            self.add_function_node(sender=None, app_data=None, user_data=("restore", clip, node))

        for link_index, link in enumerate(clip.node_collection.links):
            if link.deleted:
                continue
            self.add_link_callback(sender=None, app_data=None, user_data=("restore", clip, link.src_channel, link.dst_channel))

    def add_input_node(self, sender, app_data, user_data):
        action = user_data[0]
        args = user_data[1]
        if action == "create":
            clip, dtype = args
            success, input_channel = self.state.execute(f"create_input {clip.id} {dtype}")
            if not success:  
                raise RuntimeError("Failed to create input")
            success = self.state.execute(f"add_automation {input_channel.id}")
        else: # restore
            clip, input_channel = args

        node_editor_tag = get_node_editor_tag(clip)
        dtype = input_channel.dtype

        node_tag = get_node_tag(clip, input_channel)
        with dpg.node(label=input_channel.name, tag=node_tag, parent=node_editor_tag):
            with dpg.node_attribute(tag=f"{input_channel.id}.gui.node", attribute_type=dpg.mvNode_Attr_Output):
                # Input Knob
                dpg.add_knob_float(label="", min_value=0, max_value=model.MAX_VALUES[dtype], tag=f"{input_channel.id}.value", width=75)

            # Create Automation Editor
            self.create_new_automation_editor(
                clip,
                input_channel,
                action
            )

            # When user clicks on the node, bring up the automation window.
            def input_selected_callback(sender, app_data, user_data):
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

            if action == "create":
                self.update_io_matrix_window(clip)

    def add_function_node(self, sender, app_data, user_data):
        action = user_data[0]
        if action == "create":
            node_type = user_data[1]
            node_args = user_data[2]
            clip = user_data[3]
            success, node = state.execute(f"create_node {clip.id} {node_type} {node_args}")
            if not success:
                return
            self._last_add_function_node = (sender, app_data, user_data)
        else: # restore
            _, clip, node = user_data

        parent = get_node_editor_tag(clip)
        parameters = node.parameters
        input_channels = node.inputs
        output_channels = node.outputs

        node_tag = get_node_tag(clip, node)
        with dpg.node(parent=get_node_editor_tag(clip), tag=node_tag, label=node.name):

            def update_parameter(sender, app_data, user_data):
                if app_data:
                    node, parameter_index = user_data
                    success = self.state.execute(f"update_parameter {node.id} {parameter_index} {app_data}")
                    if not success:
                        raise RuntimeError("Failed to update parameter")

            for parameter_index, parameter in enumerate(parameters):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                    dpg.add_input_text(
                        label=parameter.name, 
                        tag=f"{parameter.id}.value",
                        callback=update_parameter, 
                        user_data=(node, parameter_index), 
                        width=70,
                        default_value=parameter.value if parameter.value is not None else "",
                        on_enter=True,
                    )

            # If an input isn't connected to a node, the user can set it 
            def update_input_channel_value(sender, app_data, user_data):
                if app_data:
                    input_channel = user_data
                    success = self.state.execute(f"update_channel_value {input_channel.id} {app_data}")
                    if not success:
                        raise RuntimeError(f"Failed to update channel value {input_channel.id}")

            for input_index, input_channel in enumerate(input_channels):
                with dpg.node_attribute(tag=f"{input_channel.id}.gui.node"):
                    add_func = dpg.add_input_float if input_channel.dtype == "float" else dpg.add_input_int
                    add_func(
                        label=input_channel.name, 
                        tag=f"{input_channel.id}.value", 
                        width=90, 
                        on_enter=True,
                        default_value=input_channel.get(),
                        callback=update_input_channel_value,
                        user_data=input_channel
                    )

            for output_index, output_channel in enumerate(output_channels):
                with dpg.node_attribute(tag=f"{output_channel.id}.gui.node", attribute_type=dpg.mvNode_Attr_Output):
                    dpg.add_input_int(label=output_channel.name, tag=f"{output_channel.id}.value", width=90)

        self.update_io_matrix_window(clip)
    
    def add_output_node(self, clip, output_channel):
        # This is the id used when adding links.
        attr_tag = get_output_node_attribute_tag(clip, output_channel)

        if dpg.does_item_exist(attr_tag):
            return

        node_tag = get_node_tag(clip, output_channel)
        with dpg.node(label="Output", tag=node_tag, parent=get_node_editor_tag(clip)):
            with dpg.node_attribute(tag=attr_tag):
                dpg.add_input_int(label="In", tag=get_output_node_value_tag(clip, output_channel), width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_int(label="Ch.", source=f"{output_channel.id}.dmx_channel", width=50, readonly=True, step=0)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text(source=f"{output_channel.id}.name", default_value=output_channel.name)

            # When user clicks on the output node it will populate the inspector.
            def set_inspector(sender, app_data, user_data):
                self._active_output_channel = user_data

            handler_registry_tag = f"{node_tag}.item_handler_registry"
            with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                dpg.add_item_clicked_handler(callback=set_inspector, user_data=output_channel)
            dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

        self.update_io_matrix_window(clip)

    def add_link_callback(self, sender, app_data, user_data):
        action = user_data[0]

        if action == "create":
            track_output = False
            _, clip = user_data
            src = dpg.get_item_alias(app_data[0]) or app_data[0]
            dst = dpg.get_item_alias(app_data[1]) or app_data[1]
            if dst.endswith(".output.gui.node"):
                track_output = True
                dst = dst.replace(".output", "").split('.', 1)[-1]
            src = src.replace(".gui.node", "")
            dst = dst.replace(".gui.node", "")
            success = state.execute(f"create_link {clip.id} {src} {dst}")
            if success:
                link_tag = f"{src}:{dst}.gui.link"
                node_editor_tag = get_node_editor_tag(clip)
                if track_output:
                    dst = f"{clip.id}.{dst}.output"
                dpg.add_node_link(f"{src}.gui.node", f"{dst}.gui.node", parent=node_editor_tag, tag=link_tag)
            else:
                raise RuntimeError("Failed to add link")
        else: # restore
            _, clip, src_channel, dst_channel = user_data
            src = src_channel.id
            if hasattr(dst_channel, "dmx_channel"):
                dst = get_output_node_attribute_tag(clip, dst_channel)
            else:
                dst = dst_channel.id
            link_tag = f"{src}:{dst}.gui.link"

            node_editor_tag =  get_node_editor_tag(clip)
            dpg.add_node_link(f"{src}.gui.node", f"{dst}.gui.node", parent=node_editor_tag, tag=link_tag)

        self.update_io_matrix_window(clip)

    def delete_link_callback(self, sender, app_data, user_data):
        alias = dpg.get_item_alias(app_data) or app_data
        clip = user_data[1]
        self._delete_link(alias, alias.rstrip(".gui.link"), clip)

    def create_io_matrix_window(self, clip, pos=(810, 0), show=True, width=800, height=800):
        io_matrix_tag = get_io_matrix_window_tag(clip)

        # Create the I/O Matrix windows after restoring links
        with dpg.window(
            tag=io_matrix_tag,
            label=f"I/O Matrix | {clip.title} ",
            width=width,
            height=height,
            pos=pos, 
            show=show,
            horizontal_scrollbar=True,
        ) as window:
            w = 50
            h = 25

            table_tag = f"{io_matrix_tag}.table"
            with dpg.child_window(horizontal_scrollbar=True,track_offset=1.0, tracked=True, tag=f"{io_matrix_tag}.child"):
                def io_matrix_checkbox(sender, app_data, user_data):
                    link_tag = sender.replace(".io_matrix_checkbox", ".gui.link")
                    if app_data:
                        src, dst = sender.replace(".io_matrix_checkbox", "").split(":")
                        self.add_link_callback(None, (src, dst), ("create", clip))
                    else:
                        self.delete_link_callback(None, link_tag, (None, clip))

                srcs = []
                dsts = []

                for channel in clip.inputs:
                    if channel.deleted:
                        continue
                    srcs.append(("In", channel))
                for node in clip.node_collection.nodes:
                    if node.deleted:
                        continue
                    for channel in node.outputs:
                        srcs.append((node.name, channel))

                for channel in clip.outputs:
                    if channel.deleted:
                        continue
                    dsts.append(("Out", channel))
                for node in clip.node_collection.nodes:
                    if node.deleted:
                        continue
                    for channel in node.inputs:
                        dsts.append((node.name, channel))

                y_start = 25
                x_start = 10
                xsum = 10
                all_xpos = []
                for dst_index, (name, dst_channel) in enumerate(dsts):
                    text = f"{name}.{dst_channel.name}"
                    xpos = x_start + xsum*8
                    if hasattr(dst_channel, "dmx_channel"):
                        # Should match the link naming scheme
                        tag = get_output_node_attribute_tag(clip, dst_channel).replace(".gui.node", ".io_matrix_text")
                    else: # a track output
                        tag = f"{dst_channel.id}.io_matrix_text"
                    all_xpos.append(xpos)
                    dpg.add_button(label=text, pos=(xpos, y_start), tag=tag)
                    xsum += len(text)

                for src_index, (name, src_channel) in enumerate(srcs):
                    ypos = h * (src_index + 1)
                    text = f"{name}.{src_channel.name}"
                    tag = f"{src_channel.id}.io_matrix_text"
                    dpg.add_button(label=text, pos=(x_start, y_start + ypos), tag=tag)
                    for dst_index, (name, dst_channel) in enumerate(dsts):
                        link = f"{src_channel.id}:{dst_channel.id}"
                        dpg.add_checkbox(
                                tag=f"{link}.io_matrix_checkbox", 
                                default_value=clip.node_collection.link_exists(src_channel, dst_channel),
                                callback=io_matrix_checkbox,
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

    def create_new_automation_editor(self, clip, input_channel, action):
        parent = get_automation_window_tag(input_channel)
        with dpg.window(
            tag=parent,
            label=f"Automation Window",
            width=750,
            height=520,
            pos=(799, 18),
            show=False,
            no_move=True,
            no_resize=True,
            no_title_bar=True,

        ) as window:
            self.tags["hide_on_clip_selection"].append(parent)

            automation = input_channel.active_automation

            series_tag = f"{input_channel.id}.series"
            plot_tag = get_plot_tag(input_channel)
            playhead_tag = f"{input_channel.id}.gui.playhead"
            menu_tag = f"{input_channel.id}.menu"

            with dpg.menu_bar(tag=menu_tag):
                with dpg.menu(label="Preset", tag=f"{menu_tag}.preset"):

                    def select_preset(sender, app_data, user_data):
                        input_channel, automation = user_data
                        self.state.execute(f"set_active_automation {input_channel.id} {automation.id}")
                        self.reset_points(input_channel)

                    def add_preset(sender, app_data, user_data):
                        input_channel = user_data
                        success, automation = self.state.execute(f"add_automation {input_channel.id}")
                        if success:
                            dpg.add_menu_item(parent=f"{menu_tag}.preset", label=automation.name, callback=select_preset, user_data=input_channel)
                            self.reset_points(input_channel)

                    dpg.add_menu_item(label="Add Preset", callback=add_preset, user_data=input_channel)
                    for auto_i, automation in enumerate(input_channel.automations):
                        dpg.add_menu_item(label=automation.name, callback=select_preset, user_data=(input_channel, automation))

                def disable_automation(sender, app_data, user_data):
                    input_channel = user_data
                    input_channel.automation_enabled = not input_channel.automation_enabled
                    dpg.configure_item(playhead_tag, color=[255, 255, 0, 255] if input_channel.automation_enabled else [200, 200, 200,255])
                    dpg.configure_item(sender, label="Disable" if input_channel.automation_enabled else "Enable")
                dpg.add_menu_item(label="Disable", callback=disable_automation, user_data=input_channel)


                def default_time(sender, app_data, user_data):
                    clip.speed = 0
                dpg.add_menu_item(
                    label="1",
                    callback=default_time,
                    user_data=clip,
                )

                def double_time(sender, app_data, user_data):
                    clip.speed += 1
                dpg.add_menu_item(
                    label="x2",
                    callback=double_time,
                    user_data=clip,
                )

                def half_time(sender, app_data, user_data):
                    clip.speed -= 1
                dpg.add_menu_item(
                    label="/2",
                    callback=half_time,
                    user_data=clip,
                )

                def update_min_max_value(sender, app_data, user_data):
                    y_axis_limits_tag = f"{plot_tag}.y_axis_limits"
                    min_y, max_y = dpg.get_axis_limits(y_axis_limits_tag)
                    if user_data == "min":
                        min_y = float(app_data)
                    if user_data == "max":
                        max_y = float(app_data)
                    dpg.set_axis_limits(y_axis_limits_tag, min_y, max_y)

                def update_automation_length(sender, app_data, user_data):
                    x_axis_limits_tag = f"{plot_tag}.x_axis_limits"
                    if app_data:
                        input_channel = user_data
                        input_channel.active_automation.set_length(app_data)
                        dpg.set_axis_limits(x_axis_limits_tag, 0, input_channel.active_automation.length)
                        self.reset_points(input_channel)

                dpg.add_text("Min:", pos=(400, 0))
                dpg.add_input_text(label="", default_value=0, pos=(430, 0), on_enter=True, decimal=input_channel.dtype=="float", callback=update_min_max_value, user_data="min", width=50)
                dpg.add_text("Max:", pos=(500, 0))
                dpg.add_input_text(label="", default_value=model.MAX_VALUES[input_channel.dtype], pos=(530, 0), on_enter=True, decimal=input_channel.dtype=="float", callback=update_min_max_value, user_data="max", width=50)
                dpg.add_text("Beats:", pos=(600, 0))
                dpg.add_input_text(label="", default_value=input_channel.active_automation.length, pos=(630, 0), on_enter=True, callback=update_automation_length, user_data=input_channel, width=50)

            with dpg.plot(label=input_channel.active_automation.name, height=-1, width=-1, tag=plot_tag, query=True, callback=self.print_callback):
                dpg.add_plot_axis(dpg.mvXAxis, label="x", tag=f"{plot_tag}.x_axis_limits")
                dpg.set_axis_limits(dpg.last_item(), 0, input_channel.active_automation.length)

                dpg.add_plot_axis(dpg.mvYAxis, label="y", tag=f"{plot_tag}.y_axis_limits")
                dpg.set_axis_limits(dpg.last_item(), 0, model.MAX_VALUES[input_channel.dtype])
                dpg.add_line_series(
                    [],
                    [],
                    tag=series_tag,
                    parent=dpg.last_item(),
                )

                self.reset_points(input_channel)

                dpg.add_drag_line(
                    label="Playhead",
                    tag=playhead_tag,
                    color=[255, 255, 0, 255],
                    vertical=True,
                    default_value=0,
                )

    def reset_points(self, input_channel):
        automation = input_channel.active_automation
        series_tag = f"{input_channel.id}.series"
        plot_tag = get_plot_tag(input_channel)

        dpg.configure_item(plot_tag, label=input_channel.active_automation.name)

        # Delete existing points
        for item in dpg.get_all_items():
            alias = dpg.get_item_alias(item)
            if alias.startswith(f"{input_channel.id}.series."):
                dpg.delete_item(item)

        for i, x in enumerate(automation.values_x):
            if x is None:
                continue
            y = automation.values_y[i]
            point_tag = f"{input_channel.id}.series.{i}"
            dpg.add_drag_point(
                color=[0, 255, 255, 255],
                default_value=[x, y],
                callback=self.update_automation_point_callback,
                parent=plot_tag,
                tag=point_tag,
                user_data=input_channel,
                thickness=50,
            )

    def create_inspector_window(self):
        with dpg.window(
            label=f"Inspector",
            width=750,
            height=600,
            pos=(810, 0),
            show=False,
            tag="inspector_window"
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
            tag="io_window"
        ) as window:
            output_table_tag = f"io.outputs.table"
            input_table_tag = f"io.inputs.table"

            def set_output_type(sender, app_data, user_data):
                index, io_output_type = user_data
                self.gui_state.io_types["outputs"][int(index)] = (sender, io_output_type)
                arg_string = {
                    "node_dmx_client": "host:port"
                }[io_output_type]
                dpg.configure_item(f"{output_table_tag}.{index}.type", label=model.ALL_OUTPUT_TYPES[io_output_type])
                dpg.set_value(f"{output_table_tag}.{index}.arg", value=arg_string)

            def add_output(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    _, index = user_data
                    io_output_type = self.gui_state.io_types["outputs"][int(index)][1]
                    state.execute(f"create_io_output {index} {io_output_type} {app_data}")
                    io_output = model.IO_OUTPUTS[index]
                    self.gui_state.io_args["outputs"][index] = (sender, app_data)
                else: # restore
                    _, index, io_output = user_data

                dpg.configure_item(f"{output_table_tag}.{index}.type", label=model.ALL_OUTPUT_TYPES[io_output.type])
                dpg.set_value(f"{output_table_tag}.{index}.arg", value=io_output.arg_string)

            def add_input(sender, app_data, user_data):
                pass


            with dpg.table(header_row=True, tag=output_table_tag):
                type_column_tag = f"{output_table_tag}.column.type"
                arg_column_tag = f"{output_table_tag}.column.arg"
                connected_column_tag = f"{output_table_tag}.column.connected"
                dpg.add_table_column(label="Input Type", tag=type_column_tag)
                dpg.add_table_column(label="Input", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connected", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        dpg.add_button(label="Select Input Type", callback=self.print_callback)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for input_type, nice_title in model.ALL_INPUT_TYPES.items():

                                dpg.add_menu_item(label=nice_title, callback=add_input, user_data=("create", i, input_type))

                        dpg.add_input_text(default_value="")
                        
                        dpg.add_text(default_value="-")

            with dpg.table(header_row=True, tag=input_table_tag):
                type_column_tag = f"{input_table_tag}.column.type"
                arg_column_tag = f"{input_table_tag}.column.arg"
                connected_column_tag = f"{input_table_tag}.column.connected"
                dpg.add_table_column(label="Output Type", tag=type_column_tag)
                dpg.add_table_column(label="Output", tag=arg_column_tag, width=15)
                dpg.add_table_column(label="Connected", tag=connected_column_tag)
                for i in range(5):
                    with dpg.table_row():
                        type_tag = f"{output_table_tag}.{i}.type"
                        dpg.add_button(label="Select Output Type", tag=type_tag)
                        with dpg.popup(dpg.last_item(), mousebutton=0):
                            for io_output_type, nice_title in model.ALL_OUTPUT_TYPES.items():
                                dpg.add_menu_item(label=nice_title, callback=set_output_type, user_data=(i, io_output_type))

                        arg_tag = f"{output_table_tag}.{i}.arg"
                        dpg.add_input_text(default_value="", tag=arg_tag, on_enter=True, callback=add_output, user_data=("create", i))
                        
                        connected_tag = f"{output_table_tag}.{i}.connected"
                        dpg.add_text(default_value="-", tag=connected_tag)

        ###############
        ### Restore ###
        ###############

    def create_track_output_configuration_window(self, track, show=False):
        window_tag = get_output_configuration_window_tag(track)
        with dpg.window(
            tag=window_tag,
            label=f"Output Configuration",
            width=250,
            height=520,
            pos=(799,18),
            no_title_bar=True,
            no_move=True,
            show=show
        ) as window:
            output_table_tag = f"{window_tag}.output_table"

            with dpg.group(horizontal=True):
                def set_track_title_button_text(sender, app_data, user_data):
                    if self.state.mode == "edit":
                        track.name = app_data
                        dpg.configure_item(user_data, label=track.name)
                track_title_tag = f"{track.id}.gui.button"
                dpg.add_input_text(tag=f"{track.id}.title", default_value=track.name, user_data=track_title_tag, callback=set_track_title_button_text, width=75)

                dpg.add_button(
                    label="Add Output",
                    callback=self.add_track_output,
                    user_data=("create", track)
                )
                dpg.add_button(label="Add Fixture")    
                with dpg.popup(dpg.last_item(), mousebutton=0):
                    for Fixture in fixtures.FIXTURES:
                        dpg.add_menu_item(label=Fixture.name, callback=self.add_fixture, user_data=(track, Fixture))

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
        with dpg.table_row(parent=output_table_tag):
            dpg.add_input_int(tag=f"{output_channel.id}.dmx_channel", width=75, default_value=output_channel.dmx_channel)
            dpg.add_input_text(tag=f"{output_channel.id}.name", default_value=output_channel.name, width=80)
            dpg.add_button(label="X", callback=self._delete_track_output, user_data=(track, output_channel))

        # Add a Node to each clip's node editor
        for clip in track.clips:
            if clip is None:
                continue
            self.add_output_node(clip, output_channel)

    def add_fixture(self, sender, app_data, user_data):
        track = user_data[0]
        Fixture = user_data[1]
        
        starting_address = 1
        for output_channel in track.outputs:
            starting_address = max(starting_address, output_channel.dmx_channel + 1)

        for ch, name in enumerate(Fixture.channels):
            self.add_track_output(None, None, ("create", track))
            output_channel = track.outputs[-1]
            dpg.set_value(f"{output_channel.id}.dmx_channel", starting_address + ch)
            dpg.set_value(f"{output_channel.id}.name", name)

    ###

    def _delete_link(self, link_tag, link_key, clip):
        success = self.state.execute(f"delete_link {clip.id} {link_key}")
        if success:              
            dpg.delete_item(link_tag)
        else:
            raise RuntimeError(f"Failed to delete: {link_key}")
        self.update_io_matrix_window(clip)

    def _delete_node(self, node_tag, obj_id):
        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        obj = self.state.get_obj(obj_id)
        success = self.state.execute(f"delete {obj_id}")
        if success:
            channels_to_delete = []
            if isinstance(obj, model.Channel):
                # Input Nodes (also need to delete automation window)
                channels_to_delete = [obj]
                automation_window_tag = get_automation_window_tag(obj_id, is_id=True)
                if automation_window_tag in all_aliases:
                    dpg.delete_item(automation_window_tag)

            # Function Nodes have their own inputs/outputs that we need to delete
            # corresponding links.
            if isinstance(obj, model.Node):
                channels_to_delete.extend(obj.inputs)
                channels_to_delete.extend(obj.outputs)

            # Delete any links attached to this node
            ids = [channel.id for channel in channels_to_delete]
            link_tags = [alias for alias in all_aliases if alias.endswith(".gui.link")]
            for id_ in ids:
                for link_tag in link_tags:
                    if id_ in link_tag:
                        self._delete_link(link_tag, link_tag.rstrip(".gui.link"), self._active_clip)
            
            # Finally, delete the node from the Node Editor
            dpg.delete_item(node_tag)

            # Update the matrix
            self.update_io_matrix_window(self._active_clip)

    def _delete_track_output(self, _, __, user_data):
        track, output_channel = user_data
        parent = get_output_configuration_window_tag(track)
        output_table_tag = f"{parent}.output_table"
        output_table_row_tag = f"{output_table_tag}.{output_channel.id}.gui.row"
        success = self.state.execute(f"delete {output_channel.id}")
        if success:
            dpg.delete_item(parent)
            self.create_track_output_configuration_window(track, pos=old_pos, show=True)

            # Delete each Node from each clip's node editor
            for clip_i, clip in enumerate(self.state.tracks[track_i].clips):
                if clip is None:
                    continue
                track_output_id = f"track[{track_i}].out[{output_index}]-{clip_i}"
                node_tag = f"{track_output_id}.{clip_i}.node"
                dpg.delete_item(node_tag)
             
    ###

    def delete_selected_nodes(self):
        window_tag_alias = dpg.get_item_alias(dpg.get_active_window())
        
        if window_tag_alias is not None and window_tag_alias.endswith("node_window"):
            node_editor_tag = get_node_editor_tag(self._active_clip)

            for item in dpg.get_selected_nodes(node_editor_tag):                    
                alias = dpg.get_item_alias(item)
                node_id = alias.rstrip(".node").rsplit(".", 1)[-1]
                if "DmxOutput" in node_id:
                    continue
                self._delete_node(alias, node_id)

            for item in dpg.get_selected_links(node_editor_tag):
                alias = dpg.get_item_alias(item)
                link_key = alias.rstrip(".gui.link")
                self._delete_link(alias, link_key, self._active_clip)

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
        for track in self.state.tracks:
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
        for src_channel in self.get_all_valid_clip_input_channels():
            src_channel.set(dpg.get_value(f"{src_channel.id}.value"))

        for output_channel in self.get_all_valid_track_output_channels():
            output_channel.dmx_channel = dpg.get_value(f"{output_channel.id}.dmx_channel")
            output_channel.name = dpg.get_value(f"{output_channel.id}.name")


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
            dpg.configure_item(
               f"{self._active_input_channel.id}.series",
                x=[x[0] for x in values if x[0] is not None],
                y=[x[1] for x in values if x[0] is not None],
            )

        # Update Inspector
        if valid(self._active_output_channel):
            dpg.configure_item(
                    "inspector.series",
                    x=self._inspecter_x,
                    y=self._active_output_channel.history[-1 - len(self._inspecter_x):-1],
                )

        # Set the play heads to the correct position
        if valid(self._active_input_channel):
            dpg.set_value(f"{self._active_input_channel.id}.gui.playhead", self._active_clip.time % self._active_input_channel.active_automation.length if self._active_input_channel.automation_enabled else 0)

    def select_clip_callback(self, sender, app_data, user_data):
        track, clip = user_data
        self._active_track = track
        self._active_clip = clip
        self.update_clip_status()

        all_aliases = [dpg.get_item_alias(item) for item in dpg.get_all_items()]
        for tag in self.tags["hide_on_clip_selection"]:
            if tag in all_aliases:
                dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(clip), show=True)
        
    def mouse_double_click_callback(self, sender, app_data, user_data):
        state = user_data
        window_tag = dpg.get_item_alias(dpg.get_item_parent(dpg.get_active_window()))
        mouse_pos = dpg.get_mouse_pos()
        plot_mouse_pos = dpg.get_plot_mouse_pos()
        if window_tag is not None and window_tag.endswith("automation_window"):
            automation = self._active_input_channel.active_automation
            for i, x in enumerate(automation.values_x):
                if x is None:
                    continue
                y = automation.values_y[i]
                x_axis_limits_tag = f"{self._active_input_channel.id}.plot.x_axis_limits"
                y_axis_limits_tag = f"{self._active_input_channel.id}.plot.y_axis_limits"
                if norm_distance((x,y), plot_mouse_pos, dpg.get_axis_limits(x_axis_limits_tag), dpg.get_axis_limits(y_axis_limits_tag)) <= 0.015:
                    if state.execute(f"remove_automation_point {self._active_input_channel.id} {i}"):
                        point_tag = f"{self._active_input_channel.id}.series.{i}"
                        dpg.delete_item(point_tag)
                    return

            if self._active_input_channel.dtype == "bool":
                plot_mouse_pos[1] = int(plot_mouse_pos[1] > 0.5)
            elif self._active_input_channel.dtype == "int":
                plot_mouse_pos[1] = int(plot_mouse_pos[1])

            success = state.execute(
                f"add_automation_point {automation.id} {plot_mouse_pos[0]},{plot_mouse_pos[1]}"
            )
            if success:
                self.reset_points(self._active_input_channel)

    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)
        #print(key_n)
        #print(key)

        if key == " ":
            self.state.playing = not self.state.playing
            if self.state.playing:
                self.state.play_time_start = time.time()
        elif key_n in [8, 46] and self.ctrl:
            self.delete_selected_nodes()
        elif key in ["O"]:
            if self.ctrl and self.shift:
                if self._active_track:
                    self.add_track_output(None, None, ("create", self._active_track))
            elif self.ctrl:
                dpg.configure_item("open_file_dialog", show=True)
        elif key in ["I"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None,( "create", (self._active_clip, "int")))
        elif key in ["B"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None, ("create", (self._active_clip, "bool")))
        elif key in ["F"]:
            if self.ctrl and self.shift:
                if self._active_clip:
                    self.add_input_node(None, None, ("create", (self._active_clip, "float")))
        elif key in ["T"]:
            self.tap_tempo()
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
        x, y, *_ = dpg.get_value(sender)
        tag = dpg.get_item_alias(sender)
        point_id, point_index = tag.split(".series.")
        point_index = int(point_index)

        max_x_i = automation.values_x.index(max(automation.values_x, key=lambda x: x or 0))
        if point_index in [0, max_x_i]:
            original_x = automation.values_x[point_index]
            dpg.set_value(sender, (original_x, y))
            x = original_x

        x, y, *_ = dpg.get_value(sender)
        if input_channel.dtype == "bool":
            y = int(y > 0.5)
        elif input_channel.dtype == "int":
            y = int(y)
        dpg.set_value(sender, (x, y))

        success = self.state.execute(f"update_automation_point {automation.id} {point_index} {x},{y}")
        if not success:
            raise RuntimeError("Failed to update automation point")

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
            with open(self.state.project_filepath, "w") as f:
                self.state.dump_state(f)
            with open(self.state.project_filepath + ".gui", "wb") as f:
                pickle.dump(self.gui_state, f)

    def restore_gui_state(self):
        for ptr, pos in self.gui_state.node_positions.items():
            clip = self.state.get_clip_from_ptr(ptr)
            obj = self.state.get_obj(ptr)
            tag = get_node_tag(clip, obj)
            dpg.set_item_pos(tag, pos)

        for ptr, axis_limits in self.gui_state.axis_limits.items():
            input_channel = self.state.get_obj(ptr)
            dpg.set_axis_limits(get_plot_tag(input_channel)+".x_axis_limits", axis_limits['x'][0], axis_limits['x'][1])
            dpg.set_axis_limits(get_plot_tag(input_channel)+".y_axis_limits", axis_limits['y'][0], axis_limits['y'][1])

        for _, (tag, value) in self.gui_state.io_args["inputs"].items():
            dpg.set_value(tag, value)
        for _, (tag, value) in self.gui_state.io_args["outputs"].items():
            dpg.set_value(tag, value)

    def restore(self, path):
        with open(path, 'r') as f:
            self.new_state = model.ProgramState()
            self.new_state.read_state(f)
        with open(path + ".gui", 'rb') as f:
            self.new_gui_state = pickle.load(f)

        print("[Stopping]")
        dpg.stop_dearpygui()


state = model.ProgramState()
gui_state = GuiState()

while state is not None:
    gui = Gui()
    state, gui_state = gui.run(state, gui_state)
    print("[Done]")

# hold
# create script reader for 
# Serialize state
