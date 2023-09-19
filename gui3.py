import dearpygui.dearpygui as dpg
import model
import re
from copy import copy
import math
import time
import pickle

SCREEN_WIDTH = 1600

SCREEN_HEIGHT = 1000

FILE_EXTENSION = "ndmx"


def distance(p1, p2):
    return math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)

class GuiState:
    def __init__(self):
        self.node_positions = {}

class Gui:

    def __init__(self):
        self.tags = {}
        
        self.state = None
        self.new_state = None
        
        self.gui_state = None
        self.new_gui_state = None

    def run(self, state, gui_state):
        self.state = state
        self.gui_state = gui_state
        self.tags = {
            "automation_window": [],
            "node_window": [],
            "gui_to_channel": {},
            "channel_to_gui": {},
            "attr_to_channel": {},
            "automation_to_gui": {},
            "playhead_tags": {},
        }
        print("Create Context")
        dpg.create_context()

        #### Create Clip Window ####
        clip_window = dpg.window(label="Clip", width=800, height=600)
        with clip_window as window:
            w = 75
            h = 20
            x_offset = 15
            y_offset = 15
            x_sep = 5

            for track_i in range(len(self.state.tracks)):
                track = self.state.tracks[track_i]
                for clip_i in range(len(track.clips)):
                    clip = track.clips[clip_i]
                    dpg.add_button(
                        label=clip.title if clip is not None else "Empty",
                        pos=((w+x_sep) * track_i + x_offset, (clip_i+1) * h + y_offset),
                        callback=self.clip_button_callback,
                        user_data={
                            "state": self.state, 
                            "track_i": track_i, 
                            "clip_i": clip_i,
                            "action": "create" if clip is None else "restore"
                        },
                        tag=f"clip[{track_i},{clip_i}]",
                    )
                    with dpg.popup(dpg.last_item()):
                        dpg.add_menu_item(label="Save As", callback=self.print_callback)

                    ################
                    #### Restore ###
                    ################
                    if clip is not None:
                        self.clip_button_callback(
                            sender=None,
                            app_data=None,
                            user_data={
                            "state": self.state, 
                            "track_i": track_i, 
                            "clip_i": clip_i,
                            "action": "create" if clip is None else "restore"
                        })    

                dpg.add_text(
                    default_value=self.state.tracks[track_i].name,
                    pos=((w+x_sep) * track_i + x_offset, y_offset),
                )
                with dpg.popup(dpg.last_item()):
                    dpg.add_menu_item(label="Save As", callback=self.print_callback)

        #### Mouse Handlers ####
        with dpg.handler_registry():
            dpg.add_mouse_double_click_handler(
                callback=self.mouse_double_click_callback, user_data=self.state
            )
            dpg.add_key_press_handler(
                callback=self.key_press_callback, user_data=self.state
            )

        # Create Viewport
        dpg.create_viewport(title="NodeDMX", width=SCREEN_WIDTH, height=SCREEN_HEIGHT)

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

                with dpg.menu(label="Settings"):
                    dpg.add_menu_item(label="Setting 1", callback=self.print_callback, check=True)
                    dpg.add_menu_item(label="Setting 2", callback=self.print_callback)

            dpg.add_menu_item(label="Help", callback=self.print_callback)

            
            # Transport 
            transport_start_x = 500

            def tap_tempo():
                pass
            dpg.add_button(label="Tap Tempo", callback=tap_tempo, pos=(transport_start_x, 0))

            def update_tempo(sender, app_data):
                self.state.tempo = float(app_data)
            dpg.add_text("Tempo:", pos=(transport_start_x + 90,0))
            dpg.add_input_text(label="Tempo", default_value=self.state.tempo, pos=(transport_start_x + 130, 0), on_enter=True, decimal=True, callback=update_tempo)

        dpg.setup_dearpygui()
        dpg.show_viewport()

        self.restore_gui_state()

        return self.main_loop()

    def restore_gui_state(self):
        for alias in self.gui_state.node_positions:
            dpg.set_item_pos(alias, self.gui_state.node_positions[alias])

    def main_loop(self):
        print("Running main loop")
        while dpg.is_dearpygui_running():
            self.update_state_from_gui()
            self.state.update()
            self.update_gui_from_state()
            dpg.render_dearpygui_frame()
        
        dpg.destroy_context()

        # TODO: Close old window.
        return self.new_state, self.new_gui_state

    def create_new_node_editor(self, parent, state, track_i, clip_i):
        clip = state.tracks[track_i][clip_i]

        with dpg.window(
            tag=parent,
            label=f"Node Window | {clip.title}",
            width=SCREEN_WIDTH * 9.8 / 10,
            height=340,
            pos=(10, 600),
        ) as window:

            def add_input(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    new_clip = state.tracks[track_i][clip_i]
                    input_index = len(new_clip.node_collection.inputs)
                    state.execute(f"create_input clip[{track_i},{clip_i}]")
                else: # restore
                    _, new_clip, input_index = user_data

                input_channel = new_clip.node_collection.inputs[input_index]
    
                clip_input_id = f"clip[{track_i},{clip_i}].clip.in[{input_index}]"
                node_tag = f"{clip_input_id}.node"
                with dpg.node(label=f"Input {input_index}", tag=node_tag, parent=f"{parent}.node_editor"):
                    attr_tag = f"{clip_input_id}"
                    self.tags["attr_to_channel"][attr_tag] = input_channel

                    with dpg.node_attribute(tag=f"{attr_tag}", attribute_type=dpg.mvNode_Attr_Output):
                        # Input Knob
                        value_tag = f"{attr_tag}.value"
                        dpg.add_knob_float(label="", min_value=0, max_value=255, tag=f"{value_tag}", width=75)
                        self.tags["gui_to_channel"][value_tag] = input_channel

                        # Automation Editor
                        automation_window_tag = f"{clip_input_id}.automation_window"
                        self.create_new_automation_editor(
                            automation_window_tag,
                            state,
                            track_i,
                            clip_i,
                            input_index,
                        )
                        self.tags["automation_window"].append(automation_window_tag)

                    # When user clicks on the node, bring up the automation window.
                    def open_automation_window(sender, app_data, user_data):
                        for tag in self.tags["automation_window"]:
                            dpg.configure_item(tag, show=False)
                        dpg.configure_item(user_data, show=True)

                    handler_registry_tag = f"{node_tag}.item_handler_registry"
                    with dpg.item_handler_registry(tag=handler_registry_tag) as handler:
                        dpg.add_item_clicked_handler(callback=open_automation_window, user_data=automation_window_tag)
                    dpg.bind_item_handler_registry(node_tag, handler_registry_tag)

            def add_output(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    new_clip = state.tracks[track_i][clip_i]
                    output_index = len(new_clip.node_collection.outputs)
                    state.execute(f"create_output clip[{track_i},{clip_i}]")
                else: # restore
                    _, new_clip, output_index = user_data

                output_channel = new_clip.node_collection.outputs[output_index]

                clip_output_id = f"clip[{track_i},{clip_i}].clip.out[{output_index}]"
                node_tag = f"{clip_output_id}.node"
                with dpg.node(label=f"Output {output_index}", tag=node_tag, parent=f"{parent}.node_editor"):
                    attr_tag = f"{clip_output_id}"
                    self.tags["attr_to_channel"][attr_tag] = output_channel
                    with dpg.node_attribute(tag=f"{attr_tag}"):
                        value_tag = f"{attr_tag}.value"
                        dpg.add_input_int(label="In", tag=f"{value_tag}", width=75, readonly=True)
                        self.tags["channel_to_gui"][value_tag] = output_channel

                    with dpg.node_attribute(tag=f"{attr_tag}.dmx_channel", attribute_type=dpg.mvNode_Attr_Static):
                        value_tag = f"{attr_tag}.dmx_channel.value"
                        dpg.add_input_int(label="Ch.", tag=f"{value_tag}", width=75, )
                        #self.tags["channel_to_gui"][value_tag] = output_channel

                # TODO: Add output inspecter

            def add_function_node(sender, app_data, user_data):
                action = user_data[0]
                if action == "create":
                    node_type = user_data[1]
                    new_clip = state.tracks[track_i][clip_i]
                    node_index = len(new_clip.node_collection.nodes)
                    state.execute(f"create_node clip[{track_i},{clip_i}] {node_type}")
                else: # restore
                    _, new_clip, node_index = user_data
                    node_type = new_clip.node_collection.nodes[node_index].type

                input_channels = new_clip.node_collection.nodes[node_index].inputs
                output_channels = new_clip.node_collection.nodes[node_index].outputs

                clip_node_id = f"clip[{track_i},{clip_i}].node[{node_index}]"
                node_tag = f"{clip_node_id}.node"
                with dpg.node(parent=f"{parent}.node_editor", tag=node_tag):
                    if node_type == "add_node":
                        dpg.configure_item(dpg.last_item(), label="Add")

                        for input_index, input_channel in enumerate(input_channels):
                            attr_in_tag = f"{clip_node_id}.in[{input_index}]"
                            self.tags["attr_to_channel"][attr_in_tag] = input_channel
                            with dpg.node_attribute(label="NodeAttr", tag=attr_in_tag):
                                value_tag = f"{attr_in_tag}.value"
                                print(value_tag)
                                dpg.add_input_int(label=f"In{input_index}", tag=f"{value_tag}", width=75)
                                self.tags["channel_to_gui"][value_tag] = input_channel

                        for output_index, output_channel in enumerate(output_channels):
                            attr_out_tag = (
                                f"{clip_node_id}.out[{output_index}]"
                            )
                            self.tags["attr_to_channel"][attr_out_tag] = output_channel
                            with dpg.node_attribute(
                                label="NodeAttr",
                                tag=attr_out_tag,
                                attribute_type=dpg.mvNode_Attr_Output,
                            ):
                                value_tag = f"{attr_out_tag}.value"
                                print(value_tag)
                                dpg.add_input_int(label="Out", tag=f"{value_tag}", width=75)
                                self.tags["channel_to_gui"][value_tag] = output_channel

            def add_link(sender, app_data, user_data):
                action = user_data[0]

                if action == "create":
                    src = dpg.get_item_alias(app_data[0])
                    dst = dpg.get_item_alias(app_data[1])
                    state.execute(f"create_link {src} {dst}")
                else: # restore
                    _, src_channel, dst_channel = user_data
                    src, dst = None, None
                    for tag, channel in self.tags["attr_to_channel"].items():
                        if channel == src_channel:
                            src = tag
                        if channel == dst_channel:
                            dst = tag
                        if dst and src:
                            break
                    print(src, dst)
                    assert src and dst

                link_tag = f"{src}:{dst}"
                dpg.add_node_link(src, dst, parent=f"{parent}.node_editor", tag=link_tag)

            def delete_link(sender, app_data):
                dpg.delete_item(app_data)

            # Node Editor
            dpg.add_node_editor(
                callback=add_link,
                delink_callback=delete_link,
                tag=f"{parent}.node_editor",
                user_data=("create",)
            )

            with dpg.menu_bar():
                dpg.add_menu_item(label="Create Input", callback=add_input, user_data=("create",))

                with dpg.menu(label="Functions"):
                    dpg.add_menu_item(
                        label="Add", user_data=("create", "add_node"), callback=add_function_node
                    )

                dpg.add_menu_item(label="Create Output", callback=add_output, user_data=("create",))

        ###############
        ### Restore ###
        ###############
        for input_index, input_channel in enumerate(clip.node_collection.inputs):
            add_input(sender=None, app_data=None, user_data=("restore", clip, input_index))

        for output_index, output_channel in enumerate(clip.node_collection.outputs):
            add_output(sender=None, app_data=None, user_data=("restore", clip, output_index))

        for node_index, node_channel in enumerate(clip.node_collection.nodes):
            add_function_node(sender=None, app_data=None, user_data=("restore", clip, node_index))

        for link_index, link in enumerate(clip.node_collection.links):
            add_link(sender=None, app_data=None, user_data=("restore", link.src_channel, link.dst_channel))

    def create_new_automation_editor(self, parent, state, track_i, clip_i, input_index):
        clip = state.tracks[track_i][clip_i]
        
        with dpg.window(
            tag=parent,
            label=f"Automation Window | {clip.title} | Input {input_index}",
            width=750,
            height=600,
            pos=(810, 0),
        ) as window:
            channel = clip.node_collection.inputs[input_index]
            automation = clip.automation_map[channel]

            clip_input_id = f"clip[{track_i},{clip_i}].clip.in[{input_index}]"
            series_tag = f"{clip_input_id}.series"
            plot_tag = f"{clip_input_id}.plot"
            playhead_tag = f"{clip_input_id}.playhead"

            def disable_automation(sender, app_data, user_data):
                automation.enabled = not automation.enabled
                dpg.configure_item(playhead_tag, color=[255, 255, 0, 255] if automation.enabled else [200, 200, 200,255])
                dpg.configure_item(sender, label="Disable" if automation.enabled else "Enable")
            dpg.add_button(
                label="Disable",
                callback=disable_automation,
                user_data=automation,
            )



            def default_time(sender, app_data, user_data):
                clip.speed = 0
            dpg.add_button(
                label="1",
                callback=default_time,
                user_data=clip,
                pos=(100, 26)
            )

            def double_time(sender, app_data, user_data):
                clip.speed += 1
            dpg.add_button(
                label="x2",
                callback=double_time,
                user_data=clip,
                pos=(125, 26)
            )

            def half_time(sender, app_data, user_data):
                clip.speed -= 1
            dpg.add_button(
                label="/2",
                callback=half_time,
                user_data=clip,
                pos=(150, 26)
            )

            with dpg.plot(label="Automation", height=-1, width=-1, tag=plot_tag):
                dpg.add_plot_axis(dpg.mvXAxis, label="x")
                dpg.set_axis_limits(dpg.last_item(), 0, clip.length)

                dpg.add_plot_axis(dpg.mvYAxis, label="y")
                dpg.set_axis_limits(dpg.last_item(), 0, 255)
                dpg.add_line_series(
                    [],
                    [],
                    tag=series_tag,
                    user_data=(state, track_i, clip_i, input_index),
                    parent=dpg.last_item(),
                )

                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    point_tag = f"{clip_input_id}.series.{i}"
                    dpg.add_drag_point(
                        color=[0, 255, 255, 255],
                        default_value=[x, y],
                        callback=self.update_automation_point_callback,
                        parent=plot_tag,
                        tag=point_tag,
                        user_data=(state, track_i, clip_i, input_index),
                    )

                dpg.add_drag_line(
                    label="Playhead",
                    tag=playhead_tag,
                    color=[255, 255, 0, 255],
                    vertical=True,
                    default_value=0,
                )

            self.tags["automation_to_gui"][series_tag] = automation
            self.tags["playhead_tags"][playhead_tag] = (state, clip, automation)

    def update_state_from_gui(self):
        # Update Input Channels from GUI value
        for tag, channel in self.tags["gui_to_channel"].items():
            channel.set(dpg.get_value(tag))


    def update_gui_from_state(self):
        # Update nodes from Channel values
        for tag, channel in self.tags["channel_to_gui"].items():
            dpg.set_value(tag, channel.get())

        # Update automation points
        for tag, automation in self.tags["automation_to_gui"].items():
            values = sorted(
                zip(automation.values_x, automation.values_y), 
                key=lambda t: t[0] if t[0] is not None else 0
            )
            dpg.configure_item(
                tag,
                x=[x[0] for x in values if x[0] is not None],
                y=[x[1] for x in values if x[0] is not None],
            )

        # Set the play heads to the correct position
        for playhead_tag, (state, clip, automation) in self.tags["playhead_tags"].items():
            dpg.set_value(playhead_tag, clip.time if automation.enabled else 0)


    def clip_button_callback(self, sender, app_data, user_data):
        action = user_data["action"]
        state = user_data["state"]
        track_i = user_data["track_i"]
        clip_i = user_data["clip_i"]

        node_window_tag = f"node_window[{track_i},{clip_i}]"

        # Window already exists, show it.
        if node_window_tag in self.tags["node_window"]:
            for tag in self.tags["node_window"] + self.tags["automation_window"]:
                dpg.configure_item(tag, show=False)
            dpg.configure_item(f"node_window[{track_i},{clip_i}]", show=True)
            return

        # Creating a new one or restoring.
        if action == "create":
            state.execute(f"new_clip {track_i},{clip_i}")
    
        clip = state.tracks[track_i][clip_i]    

        if action == "create":
            dpg.configure_item(sender, label=clip.title)
        
        self.create_new_node_editor(f"node_window[{track_i},{clip_i}]", state, track_i, clip_i)
        
        self.tags["node_window"].append(node_window_tag)

    def mouse_double_click_callback(self, sender, app_data, user_data):
        state = user_data
        window_tag = dpg.get_item_alias(dpg.get_item_parent(dpg.get_active_window()))
        mouse_pos = dpg.get_mouse_pos()
        plot_mouse_pos = dpg.get_plot_mouse_pos()
        if window_tag is not None and window_tag.endswith("automation_window"):
            match = re.match(r"clip\[(\d+),(\d+)\]\.clip\.in\[(\d+)\]\.automation_window", window_tag)
            if match:
                track_i, clip_i, input_index = match.groups()
                channel = state.tracks[int(track_i)][int(clip_i)].node_collection.inputs[
                    int(input_index)
                ]
                automation = state.tracks[int(track_i)][int(clip_i)].automation_map[
                    channel
                ]

                clip_input_id = f"clip[{track_i},{clip_i}].clip.in[{input_index}]"

                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    if distance((x,y), plot_mouse_pos) <= 5:
                        if state.execute(f"remove_automation_point {clip_input_id} {i}"):
                            point_tag = f"{clip_input_id}.series.{i}"
                            dpg.delete_item(point_tag)
                            return

                if state.execute(
                    f"add_automation_point {clip_input_id} {plot_mouse_pos[0]},{plot_mouse_pos[1]}"
                ):
                    plot_tag = f"{clip_input_id}.plot"
                    series_tag = f"{clip_input_id}.series"
                    point_tag = f"{clip_input_id}.series.{automation.length() - 1}"

                    dpg.add_drag_point(
                        color=[0, 255, 255, 255],
                        default_value=plot_mouse_pos,
                        callback=self.update_automation_point_callback,
                        parent=plot_tag,
                        tag=point_tag,
                        user_data=(state, track_i, clip_i, input_index),
                    )

    def key_press_callback(self, sender, app_data, user_data):
        key_n = app_data
        key = chr(key_n)

        if key == " ":
            self.state.playing = not self.state.playing
            if self.state.playing:
                self.state.play_time_start = time.time()


    def print_callback(self, sender, app_data, user_data):
        print(sender)
        print(app_data)
        print(user_data)

    def update_automation_point_callback(self, sender, app_data, user_data):
        """Callback when a draggable point it moved."""
        state, track_i, clip_i, input_index = user_data
        x, y, *_ = dpg.get_value(sender)
        tag = dpg.get_item_alias(sender)
        point_id, point_index = tag.split(".series.")

        if point_index in ["0", "1"]:
            channel = state.tracks[track_i][clip_i].node_collection.inputs[input_index]
            automation = state.tracks[track_i][clip_i].automation_map[channel]
            original_x = automation.values_x[int(point_index)]
            dpg.set_value(sender, (original_x, y))
            x = original_x

        if not state.execute(f"update_automation_point {point_id} {point_index} {x},{y}"):
            raise RuntimeError("Failed to update automation point")


    def save(self):
        for i in dpg.get_all_items():
            alias = dpg.get_item_alias(i)
            if alias and alias.endswith(".node"):
                self.gui_state.node_positions[alias] = dpg.get_item_pos(i)

        if self.state.project_filepath is not None:
            with open(self.state.project_filepath, "wb") as f:
                pickle.dump((self.state, self.gui_state), f, pickle.HIGHEST_PROTOCOL)


    def restore(self, path):
        with open(path, 'rb') as f:
            self.new_state, self.new_gui_state = pickle.load(f)

        print("Stopping")
        dpg.stop_dearpygui()


state = model.ProgramState()
gui_state = GuiState()

while state is not None:
    gui = Gui()
    state, gui_state = gui.run(state, gui_state)
    print("Done")

# Create one method to generate gui items
# Serialize state