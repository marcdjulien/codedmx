from inspect import getmembers, isfunction

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
CLIP_WINDOW_PERCENT = [0.41, 0.45]
NODE_WINDOW_PERCENT = [0.41, 0.45]
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


def set_app(app):
    global APP
    APP = app


def action_callback(sender, app_data, user_data):
    APP.action_callback(sender, app_data, user_data)


def valid(*objs):
    return all([obj is not None and not getattr(obj, "deleted", False) for obj in objs])


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

        self.app.save_last_active_clip()
        self.last_track = self.app._active_track
        self.last_clip = self.app._active_clip

        self.app._active_track = track
        self.app._active_clip = clip
        self.app._active_clip_slot = None

        for tag in self.app.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)

        if self.app.code_view == CLIP_VIEW:
            dpg.configure_item(get_code_window_tag(clip), show=True)
        elif self.app.code_view == TRACK_VIEW:
            dpg.configure_item(get_code_window_tag(track), show=True)
        else:
            dpg.configure_item(get_code_window_tag(self.state), show=True)

        self.app.clip_automation_presets_window.reset(clip)
        self.app.help_window.reset()
        self.app.clip_params_window.reset()

    def undo(self):
        self.app.save_last_active_clip()
        self.app._active_track = self.last_track
        self.app._active_clip = self.last_clip

        for tag in self.app.tags["hide_on_clip_selection"]:
            dpg.configure_item(tag, show=False)
        dpg.configure_item(get_node_window_tag(self.last_clip), show=True)


class SelectInputNode(GuiAction):
    def execute(self):
        clip = self.params["clip"]
        input_channel = self.params["channel"]
        for other_input_channel in clip.inputs:
            if other_input_channel.deleted:
                continue
            dpg.configure_item(
                get_source_node_window_tag(other_input_channel), show=False
            )
        dpg.configure_item(get_source_node_window_tag(input_channel), show=True)
        APP._active_input_channel = input_channel


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

        # Delete the double_click handler to create clips
        dpg.delete_item(
            get_clip_slot_group_tag(track_i, clip_i) + ".clip.item_handler_registry"
        )

        clip = track.clips[clip_i]

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

        self.last_track = self.app._active_track
        self.last_clip = self.app._active_clip
        self.app.save_last_active_clip()
        self.app._active_track = track
        self.app._active_clip = clip

        # Create the properties window
        self.create_clip_properties_window(clip)

        # Add the associated code editor
        self.app.create_code_editor_window(clip)
        #self.app.resize_windows_callback(None, None, None)

    def create_clip_properties_window(self, clip):
        window_tag = get_properties_window_tag(clip)

        with dpg.window(
            tag=window_tag,
            label=f"Properties",
            width=500,
            height=700,
            pos=(SCREEN_WIDTH / 3, SCREEN_HEIGHT / 3),
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


def get_io_matrix_window_tag(clip):
    return f"{clip.id}.gui.io_matrix_window"


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
):
    dpg.add_text(parent=group_tag, default_value=text, tag=text_tag)
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

    def create(self):
        """Build the window."""
        raise NotImplementedError

    def _create(self):
        """Create and build the window."""
        self.window = dpg.window(*self.args, **self.kwargs)
        self.create()

    @property
    def shown(self):
        return dpg.is_item_shown(self.tag)

    def hide(self):
        dpg.configure_item(self.tag, show=False)


class FixedWindow(Window):
    def __init__(self, state, *args, **kwargs):
        super().__init__(state, *args, no_title_bar=True, no_move=True, **kwargs)


class ResettableWindow(Window):
    """A Window that can be recreated.

    Helpful for windows that depend on state from other items that frequently
    change.
    """

    def __init__(self, state, *args, **kwargs):
        self.position = kwargs.get("pos")
        self.width = kwargs.get("width")
        self.height = kwargs.get("height")
        super().__init__(state, *args, **kwargs)

    def _create(self, show=None):
        """Create and build the window."""
        self.kwargs["pos"] = self.position
        self.kwargs["width"] = self.width
        self.kwargs["height"] = self.height

        if show is not None:
            self.kwargs["show"] = show

        super()._create()

    def reset(self, show=None):
        self.position = dpg.get_item_pos(self.tag)
        self.width = dpg.get_item_width(self.tag)
        self.height = dpg.get_item_height(self.tag)

        try:
            show = self.shown
        except:
            logger.warning("Failed to get show status")
            show = None

        dpg.delete_item(self.tag)
        self._create(show=show)
        dpg.focus_item(self.tag)

    def reset_callback(self, sender, app_data, user_data):
        self.reset(show=True)


class TrackPropertiesWindow(ResettableWindow):
    def __init__(self, state, track):
        self.track = track
        super().__init__(
            state,
            tag=get_output_configuration_window_tag(track),
            label=f"Output Configuration",
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


class RenameWindow(FixedWindow):
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
            label=f"Inspector",
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
            label=f"Global Storage Debug",
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
            label=f"Remap MIDI Device",
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
            label=f"I/O",
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
            output_table_tag = f"io.outputs.table"
            input_table_tag = f"io.inputs.table"

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

                        connected_tag = f"{input_table_tag}.{i}.connected"
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

                        connected_tag = f"{output_table_tag}.{i}.connected"
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
            label=f"Global Storage Debug",
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
            label=f"Help",
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


class PerformancePresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label=f"Clip Presets",
            tag="performance_preset.gui.window",
            show=False,
        )

    def create(self):
        def play_clip_and_preset(sender, app_data, user_data):
            track, clip, preset = user_data
            APP.play_clip_callback(None, None, (track, clip))
            APP.play_clip_preset_callback(None, None, preset)
            SelectClip({"track": track, "clip": clip}).execute()

        with self.window:
            dpg.add_button(label="Refresh", callback=self.reset_callback)
            with dpg.table(tag=f"{self.tag}.table"):
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
                                    with dpg.group(
                                        tag=f"{clip.id}.performance_preset_window.group"
                                    ):
                                        if not clip.presets:
                                            continue
                                        dpg.add_text(source=f"{clip.id}.name")
                                        for preset in clip.presets:
                                            dpg.add_button(
                                                tag=get_preset_button_tag(preset),
                                                label=preset.name,
                                                callback=play_clip_and_preset,
                                                user_data=(track, clip, preset),
                                            )
                                            dpg.bind_item_theme(
                                                dpg.last_item(),
                                                get_channel_preset_theme(preset),
                                            )


class GlobalPerformancePresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=800,
            height=800,
            label=f"Global Clip Presets",
            tag="global_preset_window.gui.window",
            show=False,
        )

    def create(self):
        def play_global_clip_preset(sender, app_data, user_data):
            global_preset = user_data
            global_preset.execute()

        with self.window:
            with dpg.menu_bar():
                dpg.add_menu_item(
                    label="New Global Preset",
                    callback=action_callback,
                    user_data=ShowWindow(APP.save_new_global_performance_preset_window),
                )

            with dpg.table(tag="global_performance_preset.table"):
                dpg.add_table_column(label="Preset")
                for global_preset in self.state.global_presets:
                    with dpg.table_row():
                        dpg.add_button(
                            label=global_preset.name,
                            callback=play_global_clip_preset,
                            user_data=global_preset,
                        )


class SaveNewGlobalPerformancePresetWindow(ResettableWindow):
    def __init__(self, state):
        super().__init__(
            state,
            pos=(200, 100),
            width=500,
            height=500,
            label=f"Save New Global Clip Preset",
            tag="save_new_global_preset_window.gui.window",
            show=False,
            modal=True,
        )
        self._global_presets_buffer = {}

    def create(self):
        def cancel():
            dpg.delete_item(self.tag)

        def save():
            global_presets = []
            for i, track in enumerate(self.state.tracks):
                include = dpg.get_value(f"global_preset.{i}")
                if include:
                    track, clip, preset = self._global_presets_buffer[i]
                    global_presets.append(":".join([track.id, clip.id, preset.id]))

            if global_presets:
                name = dpg.get_value("global_preset.name")
                result = APP.execute_wrapper(
                    f"add_global_preset {','.join(global_presets)} {name}"
                )
                if result.success:
                    dpg.configure_item(item=self.tag, show=False)
                    APP.global_performance_preset_window.reset()
                    self._global_presets_buffer.clear()
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
                            self._global_presets_buffer[int(i)] = (track, clip, preset)
                            dpg.configure_item(
                                item=f"{self.tag}.menu_bar.{i}.title",
                                label=title,
                            )

                        with dpg.menu(
                            tag=f"{self.tag}.menu_bar.{i}.title",
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
            label=f"Triggers",
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
                    if not util.valid(trigger):
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
            label=f"New Trigger",
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

                with dpg.table_row(tag=f"new_trigger.table.save_cancel_row"):
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
            pos=(200, 100),
            width=600,
            height=400,
            label=f"Clip Automation Presets",
            tag="clip_automation.gui.window",
            show=False,
        )

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

        with self.window:
            dpg.add_button(
                label="Refresh",
                callback=super().reset_callback,
            )
            with dpg.table(tag="all_automation.table"):
                for channel in self.clip.inputs:
                    if not isinstance(channel, model.AutomatableSourceNode):
                        continue
                    dpg.add_table_column(label=channel.name)

                with dpg.table_row():
                    for channel in self.clip.inputs:
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

    def reset(self, clip, show=None):
        self.clip = clip
        super().reset(show=show)


class ClipWindow(FixedWindow):
    def __init__(self, state):
        super().__init__(
            state,
            tag="clip.gui.window",
            label="Clip",
            pos=(0, 18),
            width=800,
            height=520,
            no_resize=True,
        )

    def create(self):
        with self.window:
            table_tag = f"clip_window.table"
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


class ConsoleWindow(FixedWindow):
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
        )

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


class CodeWindow(FixedWindow):
    def __init__(self, state, obj, *args, **kwargs):
        self.obj = obj
        super().__init__(
            state,
            tag=get_code_window_tag(obj),
            label="Code Editor",
            pos=WINDOW_INFO["code_pos"],
            width=WINDOW_INFO["code_size"][0],
            height=WINDOW_INFO["code_size"][1],
            no_resize=True,
        )

    def create(self):
        with self.window:

            def save():
                APP.save_menu_callback()
                APP._active_clip.code.reload()
                APP._active_track.code.reload()
                self.state.code.reload()

            def hide_code_windows():
                for tag in APP.tags["hide_on_clip_selection"]:
                    if "code_editor" in self.tag:
                        dpg.configure_item(self.tag, show=False)

            def show_global(sender, app_data, user_data):
                APP.code_view = GLOBAL_VIEW
                hide_code_windows()
                dpg.configure_item(get_code_window_tag(self.state), show=True)

            def show_track(sender, app_data, user_data):
                APP.code_view = TRACK_VIEW
                hide_code_windows()
                if valid(APP._active_track):
                    dpg.configure_item(
                        get_code_window_tag(APP._active_track), show=True
                    )

            def show_clip(sender, app_data, user_data):
                APP.code_view = CLIP_VIEW
                hide_code_windows()
                if valid(APP._active_clip):
                    dpg.configure_item(get_code_window_tag(APP._active_clip), show=True)

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

            dpg.add_input_text(
                tag=f"{self.tag}.text",
                default_value=self.obj.code.read(),
                multiline=True,
                readonly=False,
                tab_input=True,
                width=SCREEN_WIDTH - 800 - 35,
                height=SCREEN_HEIGHT - 520 - 70,
                on_enter=False,
            )

        dpg.bind_item_theme(self.tag + ".button.track", "code_editor.track.theme")
        dpg.bind_item_theme(self.tag + ".button.clip", "code_editor.clip.theme")
        dpg.bind_item_theme(self.tag + ".button.global", "code_editor.global.theme")
        dpg.bind_item_theme(
            self.tag + ".button.separator", "code_editor.separator.theme"
        )

        if isinstance(self.obj, model.Track):
            dpg.bind_item_theme(self.tag + ".text", "code_editor.track.theme")
        elif isinstance(self.obj, model.Clip):
            dpg.bind_item_theme(self.tag + ".text", "code_editor.clip.theme")
        else:  # State
            dpg.bind_item_theme(self.tag + ".text", "code_editor.global.theme")

# TODO: Not being used
class AutomationWindow(FixedWindow):
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

                    preset_menu_tag = f"{input_channel.id}.preset_menu"
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


class ClipParametersWindow(ResettableWindow, FixedWindow):
    def __init__(self, state):
        super().__init__(
            state,
            tag="clip_parameters.gui.window",
            width=WINDOW_INFO["clip_parameters_size"][0],
            height=WINDOW_INFO["clip_parameters_size"][1],
            pos=WINDOW_INFO["clip_parameters_pos"],
            show=True,
        )

    def create(self):
        clip = APP._active_clip
        with self.window:
            if clip is None:
                return

            dpg.configure_item(self.tag, label=f"Clip Parameters | {clip.name}")

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
                    # TODO: Fix this, preset themes should only be created if new
                    for preset in clip.presets:
                        APP.add_clip_preset_to_menu(clip, preset)

                dpg.add_menu_item(
                    label="Show All Automation",
                    callback=action_callback,
                    user_data=ShowWindow(APP.clip_automation_presets_window),
                )

            def set_window_percent(sender, app_data, user_data):
                # TODO: try/except here is s hack. Figure out how this
                # causes an exception when selecting Presets in Clip Preset Window
                try:
                    width = dpg.get_item_width(dpg.get_item_alias(app_data))
                    percent = width / SCREEN_WIDTH
                    NODE_WINDOW_PERCENT[0] = percent
                    APP.resize_windows_callback(None, None, None)
                except Exception as e:
                    logger.exception(e)

            with dpg.item_handler_registry() as handler:
                dpg.add_item_resize_handler(callback=set_window_percent)
            dpg.bind_item_handler_registry(self.tag, handler)

            with dpg.group(horizontal=True):
                width = 2*WINDOW_INFO["clip_parameters_size"][0]//3
                with dpg.child_window(
                    width=width,
                    no_scrollbar=True,
                ):
                    with dpg.group(tag="input_child_window_group", width=width, height=1000):
                        with dpg.table(
                            header_row=True,
                            tag="clip_parameters.input.table.gui",
                            policy=dpg.mvTable_SizingFixedFit,
                        ):
                            dpg.add_table_column(
                                label="Input",
                                tag=f"clip_parameters.input.table.column.input",
                                width_stretch=True,
                                init_width_or_weight=0.2,
                            )
                            dpg.add_table_column(
                                label="Type",
                                tag=f"clip_parameters.input.table.column.type",
                                width_stretch=True,
                                init_width_or_weight=0.1,
                            )
                            dpg.add_table_column(
                                label="Value",
                                tag=f"clip_parameters.input.table.column.output",
                                width_stretch=True,
                                init_width_or_weight=0.2,
                            )
                            dpg.add_table_column(
                                label="Parameters",
                                tag=f"clip_parameters.input.table.column.parameters",
                                width_stretch=True,
                                init_width_or_weight=0.4,                        )
                            dpg.add_table_column(
                                tag=f"clip_parameters.input.table.column.edit",
                                width_stretch=True,
                                init_width_or_weight=0.1,                        )

                            for input_index, input_channel in enumerate(clip.inputs):
                                if input_channel.deleted:
                                    continue

                                with dpg.table_row() as row:
                                    # When user clicks on the node, bring up the automation window.
                                    def input_selected_callback(sender, app_data, user_data):
                                        clip, ic = user_data
                                        APP._active_input_channel = ic
                                        for other_channel in clip.inputs:
                                            if other_channel.deleted or ic == other_channel:
                                                continue
                                            dpg.configure_item(
                                                get_source_node_window_tag(other_channel), show=False
                                            )
                                            if other_channel.input_type == "color":
                                                dpg.bind_item_theme(f"{other_channel.id}.name_button", get_node_tag(other_channel) + ".theme")
                                            else:
                                                dpg.bind_item_theme(f"{other_channel.id}.name_button", "not_selected_preset.theme")

                                        dpg.configure_item(
                                            get_source_node_window_tag(ic),
                                            show=True,
                                        )
                                        dpg.bind_item_theme(f"{ic.id}.name_button", "selected_preset.theme")
                                        APP.reset_automation_plot(ic)

                                    if input_channel.input_type in ["int", "float", "bool", "midi", "osc_input_int", "osc_input_float"]:
                                        dpg.add_button(
                                            tag=f"{input_channel.id}.name_button",
                                            label=input_channel.name,
                                            callback=input_selected_callback,
                                            user_data=(clip, input_channel)
                                        )

                                        dpg.add_text(default_value=input_channel.input_type)

                                        with dpg.group():
                                            if input_channel.mode == "automation":
                                                dpg.add_simple_plot(min_scale=-1.0, max_scale=1.0, height=20, width=75, tag=f"{input_channel.id}.mini_plot")
                                            else:
                                                add_func = (
                                                    dpg.add_drag_float
                                                    if input_channel.dtype == "float"
                                                    else dpg.add_drag_int
                                                )
                                                add_func(
                                                    min_value=input_channel.get_parameter("min").value,
                                                    max_value=input_channel.get_parameter("max").value,
                                                    source=f"{input_channel.id}.value",
                                                    width=75,
                                                    callback=APP.update_input_channel_ext_value_callback,
                                                    user_data=input_channel,
                                                )

                                    elif input_channel.input_type == "color":
                                        dpg.add_button(
                                            tag=f"{input_channel.id}.name_button",
                                            label=input_channel.name,
                                            callback=input_selected_callback,
                                            user_data=(clip, input_channel)
                                        )
                                        dpg.bind_item_theme(f"{input_channel.id}.name_button", get_node_tag(input_channel) + ".theme")

                                        dpg.add_text(default_value="color")

                                        def update_color(sender, app_data, user_data):
                                            # Color picker returns values between 0 and 1. Convert
                                            # to 255 int value.
                                            rgb = [int(util.clamp(v * 255, 0, 255)) for v in app_data]
                                            APP.update_channel_value_callback(sender, app_data, user_data)

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
                                            callback=input_selected_callback,
                                            user_data=(clip, input_channel)
                                        )
                                        dpg.add_text(default_value="button")

                                        def update_button(sender, app_data, user_data):
                                            APP.update_channel_value_callback(
                                                sender,
                                                int(app_data),
                                                user_data
                                            )

                                        dpg.add_checkbox(
                                            callback=update_button,
                                            user_data=input_channel,
                                            default_value=bool(input_channel.get()),
                                        )

                                    param_strs = []
                                    for parameter_index, parameter in enumerate(input_channel.parameters):
                                        if str(parameter.value):
                                            param_strs.append(f"{parameter.name}: {parameter.value}")
                                    dpg.add_text(default_value=", ".join(param_strs))

                                    # Edit
                                    def show_properties_window(sender, app_data, user_data):
                                        APP._properties_buffer.clear()
                                        dpg.configure_item(get_properties_window_tag(user_data), show=True)
                                    dpg.add_button(label="Configure", callback=show_properties_window, user_data=input_channel)

                                    # Right click for input channel name button
                                    with dpg.popup(f"{input_channel.id}.name_button", mousebutton=1):
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
                            label="Output", tag=f"clip_parameters.output.table.column.input"
                        )
                        dpg.add_table_column(
                            label="Value", tag=f"clip_parameters.output.table.column.type"
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

