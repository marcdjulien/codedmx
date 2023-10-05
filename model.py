from collections import defaultdict
import re
from threading import Lock
import scipy
import time
import operator
import random
import dmxio
import uuid 

ALL_OUTPUT_TYPES = {
    "node_dmx_client": "Node DMX Client"
}
ALL_INPUT_TYPES = {}


UUID_DATABASE = {}
id_count = 0


def clear_database():
    global UUID_DATABASE
    global id_count
    id_count = 0
    UUID_DATABASE = {}

class Identifier:
    def __init__(self):
        global UUID_DATABASE
        global id_count
        self.id = f"{self.__class__.__name__}[{id_count}]"
        id_count += 1
        UUID_DATABASE[self.id] = self
        self.deleted = False

    def delete(self):
        self.deleted = True


class Channel(Identifier):
    def __init__(self, direction="in", value=0, dtype="float", name=None):
        super().__init__()
        self.value = value
        self.direction = direction
        self.dtype = dtype
        self.name = name

        self.automations = []
        self.active_automation = None
        self.active_automation_i = None
        self.automation_enabled = False

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def set_active_automation(self, automation):
        assert automation in self.automations
        self.active_automation = automation
        self.active_automation_i = self.automations.index(automation)
        self.automation_enabled = True
        return True

    def add_automation(self, clear=False):
        n = len(self.automations)
        new_automation = ChannelAutomation(self.dtype, f"Preset #{n}", clear=clear)
        self.automations.append(new_automation)
        self.set_active_automation(new_automation)
        return new_automation

    def remove_automation(self, index):
        self.automations[index].delete = True
        return True

    def serialize(self, clip_ptr, i, id_to_ptr=None):
        if i is None:
            input_ptr = f"{clip_ptr}.in[{{input_i}}]"
        else:
            input_ptr = f"{clip_ptr}.in[{i}]"
            if id_to_ptr is not None:
                id_to_ptr[self.id] = input_ptr

        data = []
        dtype = "none" if self.deleted else self.dtype
        data.append(f"execute create_input {clip_ptr} {dtype}")

        if self.deleted:
            data.append(f"execute delete {input_ptr}")
        else:
            data.append(f"execute update_channel_value {input_ptr} {self.get()}")
            data.append(f"{input_ptr}.name:{repr(self.name)}")
            data.append(f"{input_ptr}.automation_enabled:{repr(self.automation_enabled)}")
            for auto_i, automation in enumerate(self.automations):
                if automation.deleted:
                    continue
                data.extend(automation.serialize(input_ptr, auto_i))

        return data


class DmxOutput(Channel):
    def __init__(self, dmx_channel):
        super().__init__(direction="in", dtype="int", name=f"DMX CH. {dmx_channel}")
        self.dmx_channel = 1
        self.history = [0] * 500

    def record(self):
        self.history.pop(0)
        self.history.append(self.value)

    def serialize(self, track_ptr, i, id_to_ptr):
        if i is None:
            output_ptr = f"{track_ptr}.out[{{output_i}}]"
        else:
            output_ptr = f"{track_ptr}.out[{i}]"
            if id_to_ptr is not None:
                id_to_ptr[self.id] = output_ptr

        data = []

        data.append(f"execute create_output {track_ptr}")
        if self.deleted:
            data.append(f"execute delete {output_ptr}")
        else:
            data.append(f"{output_ptr}.name:{repr(self.name)}")
            data.append(f"{output_ptr}.dmx_channel:{repr(self.dmx_channel)}")

        return data


class ChannelLink(Identifier):
    def __init__(self, src_channel, dst_channel):
        super().__init__()
        self.src_channel = src_channel
        self.dst_channel = dst_channel

    def update(self):
        self.dst_channel.set(self.src_channel.get())


class Parameter(Identifier):
    def __init__(self, name, value=None):
        super().__init__()
        self.name = name
        self.value = value

class Node(Identifier):
    """Represents a clip that defines a set of inputs, outputs, and transformation between."""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.parameters: Parameter = []
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.type = None
        self.args = None

    def add_input(self, inp: Channel):
        self.inputs.append(inp)

    def set_input(self, inp: Channel, index: int):
        self.inputs[index] = inp

    def add_output(self, output: Channel):
        self.outputs.append(output)

    def transform(self):
        raise NotImplemented

    def outputs(self):
        return self.outputs

    def update_parameter(self, index, value):
        raise NotImplemented


class BinaryOperator(Node):

    def __init__(self, name="Op"):
        super().__init__(name)
        self.parameters = [Parameter("op")]
        self.inputs = [
            Channel("in", 0, name=f"x"), 
            Channel("in", 0, name=f"y"), 
        ]
        self.outputs.append(
            Channel("out", 0, name=f"z")
        )
        self.type = "binary_operator"
        self.f = None

    def transform(self):
        # TODO: Handle division by zero
        if self.f is not None:
            if self.parameters[0].value == "/" and self.inputs[1].get() == 0:
                return
            self.outputs[0].set(self.f(self.inputs[0].get(), self.inputs[1].get()))

    def update_parameter(self, index, value):
        if index == 0 and value in ["+", "-", "/", "*"]:
            self.parameters[index].value = value

            # TODO: Add other operators
            self.f = {
                "+": operator.add,
                "-": operator.sub,
                "/": operator.truediv,
                "*": operator.mul,
            }[value]


class Demux(Node):

    def __init__(self, n, name="Demux"):
        super().__init__(name)
        self.n = n
        self.parameters = []
        self.inputs = [
            Channel("in", 0, dtype="int", name=f"sel"),
            Channel("in", 1, dtype="float", name=f"val")
        ]
        for i in range(n):
            self.outputs.append(Channel("out", 0, dtype="float", name=f"{i+1}"))

        self.type = "demux"
        self.args = str(n)

    def transform(self):
        for output in self.outputs:
            output.set(0)
        selected = int(self.inputs[0].get())
        if selected in range(self.n+1):
            if selected != 0:
                self.outputs[selected-1].set(self.inputs[1].get())


class Multiplexer(Node):

    def __init__(self, n, name="Multiplexer"):
        super().__init__(name)
        self.n = n
        self.parameters = []
        self.inputs = [
            Channel("in", 1, dtype="int", name=f"sel")
        ]
        for i in range(n):
            self.inputs.append(Channel("in", 0, dtype="float", name=f"{i+1}"))

        self.outputs.append(Channel("out", 0, dtype="float", name=f"out"))
        self.type = "multiplexer"
        self.args = str(n)

    def transform(self):
        selected = int(self.inputs[0].get())
        if selected in range(1, self.n+1):
            self.outputs[0].set(self.inputs[selected].get())


class Random(Node):

    def __init__(self, name="Random"):
        super().__init__(name)
        self.parameters = []
        self.inputs = [
            Channel("in", 0, dtype="int", name=f"a"),
            Channel("in", 1, dtype="int", name=f"b")
        ]
        self.outputs.append(
            Channel("out", 0, name=f"z")
        )
        self.type = "random"

    def transform(self):
        a = int(self.inputs[0].get())
        b = int(self.inputs[1].get())
        if a > b:
            return
        self.outputs[0].set(random.randint(a, b))


class Sample(Node):

    def __init__(self, name="Sample"):
        super().__init__(name)
        self.parameters = []
        self.inputs = [
            Channel("in", 1, name=f"rate", dtype="float"),
            Channel("in", 0, name=f"in", dtype="float")
        ]
        self.outputs.append(
            Channel("out", 0, name=f"out", dtype="float")
        )
        self.type = "sample"

        self._cycles_held = 0

    def transform(self):
        self._cycles_held += 1 
        s_held = self._cycles_held/60.0

        rate = self.inputs[0].get()
        if rate <= 0:
            return
        if s_held <= rate:
            return
        else:
            self._cycles_held = 0
            self.outputs[0].set(self.inputs[1].get())


class Buffer(Node):

    def __init__(self, name="Buffer"):
        super().__init__(name)
        self.parameters = [Parameter("n", value=60)]
        self.inputs = [
            Channel("in", 0, name=f"in", dtype="float")
        ]
        self.outputs.append(
            Channel("out", 0, name=f"out", dtype="float")
        )
        self.type = "buffer"

        self._buffer = []

    def transform(self):
        self._buffer.insert(0, self.inputs[0].get())
        self.outputs[0].set(self._buffer.pop())

    def update_parameter(self, index, value):
        try:
            value = int(value)
        except:
            return
        self._buffer = [0] * value

class NodeCollection:
    """Collection of nodes and their the a set of inputs and outputs"""

    def __init__(self):
        self.nodes: Node = []  # Needs to be a tree
        self.links = []


    def add_node(self, cls, arg):
        if cls is None:
            self.nodes.append(None)
            return

        n = len(self.nodes)
        if arg is not None:
            node = cls(arg)
        else:
            node = cls()

        self.nodes.append(node)
        return node

    def add_link(self, src_channel, dst_channel):
        assert src_channel.direction == "out"
        assert dst_channel.direction == "in"
        for link in self.links:
            if link.deleted:
                continue
            if link.dst_channel == dst_channel:
                return False
        link = ChannelLink(src_channel, dst_channel)
        self.links.append(link)
        return link

    def del_link(self, src_channel, dst_channel):
        found = False
        for i, link in enumerate(self.links):
            if link.deleted:
                continue
            if src_channel == link.src_channel and dst_channel == link.dst_channel:
                found = True
                break
        
        if found:
            self.links[i].deleted = True
        
        return found

    def link_exists(self, src_channel, dst_channel):
        for link in self.links:
            if link.deleted:
                continue
            if src_channel == link.src_channel and dst_channel == link.dst_channel:
                return True
        return False

    def update(self):
        # Transform each node
        # TODO: BFS tree
        for link in self.links:
            if link.deleted:
                continue
            link.update()

        for node in self.nodes:
            if node.deleted:
                continue
            node.transform()

    def serialize(self, clip_ptr, id_to_ptr):
        data = []
        for node_i, node in enumerate(self.nodes):
            node_type = "none" if node.deleted else node.type
            args = "," if node.deleted else (node.args or ",")
            data.append(f"execute create_node {clip_ptr} {node_type} {args}")
            if node.deleted:
                continue
            node_ptr = f"{clip_ptr}.node[{node_i}]"
            data.append(f"{node_ptr}.name:{repr(node.name)}")
            for param_i, parameter in enumerate(node.parameters):
                data.append(f"execute update_parameter {node_ptr} {param_i} {parameter.value}")
            for input_i, input_channel in enumerate(node.inputs):
                input_ptr = f"{node_ptr}.in[{input_i}]"
                id_to_ptr[input_channel.id] = input_ptr
                data.append(f"execute update_channel_value {input_ptr} {input_channel.get()}")
            for output_i, output_channel in enumerate(node.outputs):
                output_ptr = f"{node_ptr}.out[{output_i}]"
                id_to_ptr[output_channel.id] = output_ptr

        for link in self.links:
            if link.deleted:
                continue
            data.append(f"execute create_link {clip_ptr} {id_to_ptr[link.src_channel.id]} {id_to_ptr[link.dst_channel.id]}")

        return data

MAX_VALUES = {
    "bool": 1,
    "int": 255,
    "float": 100.0,
}


class ChannelAutomation(Identifier):

    def __init__(self, dtype, name, clear=False):
        super().__init__()
        self.length = 4 # beats
        self.values_x = [] if clear else [0, self.length]
        self.values_y = [] if clear else [0, MAX_VALUES[dtype]]
        self.f = None if clear else scipy.interpolate.interp1d(self.values_x, self.values_y)
        self.dtype = dtype
        self.name = name

    def value(self, beat_time):
        v = self.f(beat_time % self.length)
        if self.dtype == "bool":
            return int(v > 0.5)
        elif self.dtype == "int":
            return int(v)
        else:
            return float(v)

    def n_points(self):
        return len(self.values_x)

    def add_point(self, p1):
        self.values_x.append(p1[0])
        self.values_y.append(p1[1])
        self.reinterpolate()

    def remove_point(self, index, force=False):
        if index in [0, self.max_x_index()] and not force:
            return False
        else:
            self.values_x[index] = None
            self.values_y[index] = None
            self.reinterpolate()
            return True

    def max_x_index(self):
        return self.values_x.index(max(self.values_x, key=lambda x: x or 0))

    def update_point(self, index, p1):
        self.values_x[index] = p1[0]
        self.values_y[index] = p1[1]
        self.reinterpolate()

    def reinterpolate(self):
        values_x = [x for x in self.values_x if x is not None]
        values_y = [y for y in self.values_y if y is not None]
        self.f = scipy.interpolate.interp1d(values_x, values_y, assume_sorted=False)

    def set_length(self, new_length):
        new_length = float(new_length)
        self.length = new_length
        if new_length > self.length:
            self.add_point((new_length, self.values_y[self.max_x_index()]))
        else:
            for i, x in enumerate(self.values_x):
                if x is None:
                    continue
                if x > new_length:
                    self.remove_point(i, force=True)
            self.add_point((new_length, self.values_y[self.max_x_index()]))

    def serialize(self, input_ptr, i=None):
        if i is None:
            auto_ptr = f"{input_ptr}.automation[{{auto_i}}]"
        else:
            auto_ptr = f"{input_ptr}.automation[{i}]"

        data = []
        data.append(f"execute add_automation {input_ptr} clear")
        data.append(f"{auto_ptr}.length:{repr(self.length)}")
        data.append(f"{auto_ptr}.name:{repr(self.name)}")
        for point_i in range(len(self.values_x)):
            x, y = self.values_x[point_i], self.values_y[point_i]
            if x is None:
                continue
            data.append(f"execute add_automation_point {auto_ptr} {x},{y}")

        return data


class Clip(Identifier):
    def __init__(self, outputs):
        super().__init__()

        self.title = " "*7

        self.inputs = []
        self.outputs = outputs
        self.node_collection = NodeCollection()

        # Speed to play clip
        self.speed = 0

        self.time = 0

        self.playing = False

    def create_input(self, dtype):
        n = len(self.inputs)
        new_inp = Channel("out", dtype=dtype, name=f"In.{n}")
        self.inputs.append(new_inp)
        return new_inp
        
    def update(self, beat):
        if self.playing:
            self.time = (beat * (2**self.speed))
            for channel in self.inputs:
                if channel.deleted:
                    continue
                if channel.automation_enabled:
                    value = channel.active_automation.value(self.time)
                    channel.set(value)

            self.node_collection.update()

    def start(self):
        self.time = 0
        self.playing = True

    def stop(self):
        self.playing = False

    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.start()

    def serialize(self, track_ptr, i=None, id_to_ptr=None):
        if i is None:
            clip_ptr = f"{track_ptr}.clip[{{clip_i}}]"
        else:
            clip_ptr = f"{track_ptr}.clip[{i}]"

        data = []
        data.append(f"execute new_clip {track_ptr},{i}")
        data.append(f"{clip_ptr}.title:{repr(self.title)}")
        data.append(f"{clip_ptr}.speed:{repr(self.speed)}")
        data.append(f"{clip_ptr}.playing:{repr(self.playing)}")

        for input_i, input_channel in enumerate(self.inputs):
            data.extend(input_channel.serialize(clip_ptr, input_i, id_to_ptr))

        data.extend(self.node_collection.serialize(clip_ptr, id_to_ptr))

        return data


class Track(Identifier):
    def __init__(self, title, n_clips=20):
        super().__init__()
        self.name = title
        self.clips = [None] * n_clips
        self.outputs = []

    def update(self, beat):
        for clip in self.clips:
            if clip is not None:
                clip.update(beat)

        for output in self.outputs:
            if output.deleted:
                continue
            output.record()

    def create_output(self):
        n = len(self.outputs)
        new_output = DmxOutput(0)
        self.outputs.append(new_output)
        return new_output

    def __delitem__(self, key):
        clips[key] = None

    def __getitem__(self, key):
        return self.clips[key]

    def __setitem__(self, key, value):
        self.clips[key] = value

    def __len__(self):
        return len(self.clips)

    def serialize(self, i=None):
        if i is None:
            track_ptr = "*track[{track_i}]"
        else:
            track_ptr = f"*track[{i}]"

        id_to_ptr = {}
        data = []
        data.append(f"{track_ptr}.name:{repr(self.name)}")
        for output_i, output_channel in enumerate(self.outputs):
            data.extend(output_channel.serialize(track_ptr, output_i, id_to_ptr))
        for clip_i, clip in enumerate(self.clips):
            if clip is not None:
                data.extend(clip.serialize(track_ptr, clip_i, id_to_ptr))
        return data


class IOOutput:

    def __init__(self, type, arg_string):
        self.type = type
        self.arg_string = arg_string

    def update(self, outputs):
        raise NotImplemented


class NodeDmxClientOutput(IOOutput):

    def __init__(self, host_port):
        super().__init__("node_dmx_client", host_port)
        self.host, self.port = host_port.split(":")
        self.port = int(self.port)
        self.node_dmx_client = dmxio.NodeDmxClient((self.host, self.port), dmx_address=1, n_channels=512)
        self.dmx_frame = [0] * 512

    def update(self, outputs):
        for output_channel in outputs:
            if output_channel.deleted:
                continue
            self.dmx_frame[output_channel.dmx_channel-1] = min(255, max(0, int(round(output_channel.get()))))

        try:
            self.node_dmx_client.set_channels(1, self.dmx_frame)
            self.node_dmx_client.send_frame()
        except Exception as e:
            raise e

    def __str__(self):
        return f"NodeDmxClient({self.host}:{self.port})"

IO_OUTPUTS = [None] * 5
IO_INPUTS = [None] * 5

class ProgramState(Identifier):
    _attrs_to_dump = [
        "project_name",
        "project_filepath",
        "tempo",
    ]
    
    def __init__(self):
        super().__init__()
        self.mode = "edit"
        self.project_name = "Untitled"
        self.project_filepath = None
        self.tracks = []
        self.restoring = False

        for i in range(10):
            self.tracks.append(Track(f"Track {i}"))

        self._active_clip = None

        self.playing = False
        self.tempo = 120.0
        self.play_time_start = 0
        self.beats_since_start = 0

        self.all_track_outputs = []

    def update(self):
        global IO_OUTPUTS
        global IO_INPUTS
        # Update timing
        if self.playing:
            time_since_start_s = time.time() - self.play_time_start
            self.beats_since_start = time_since_start_s * (1.0/60.0) * self.tempo

            # Update values
            for track in self.tracks:
                track.update(self.beats_since_start)

            # Update DMX outputs
            for io_output in IO_OUTPUTS:
                if io_output is not None:
                    io_output.update(self.all_track_outputs)

    def stop_io(self):
        pass

    def get_channel(self, key):
        """
        track[a].clip[b]          in[c]
        track[a]                  out[c]
        track[a].clip[b].node[c]  in[d]
        track[a].clip[b].node[c]  in[d]
        """
        def channel(obj, in_out_key):
            match = re.match(r"(in|out)\[(\d+)]", in_out_key)
            if match:
                in_out, index = match.groups()
                inout_list = obj.inputs if in_out == "in" else obj.outputs
                return inout_list[int(index)]

        src_key, in_out_key = key.rsplit(".", 1)
        
        src_toks = src_key.split(".")
        if len(src_toks) == 1:
            match = re.match(r"track\[(\d+)\]", src_key)
            if match:
                track_i = match.groups()[0]
                return channel(self.tracks[int(track_i)], in_out_key)
        elif len(src_toks) == 2:
            match = re.match(r"track\[(\d+)\]\.clip\[(\d+)\]", src_key)
            if match:
                track_i, clip_i = match.groups()
                return channel(self.tracks[int(track_i)].clips[int(clip_i)], in_out_key)
        elif len(src_toks) == 3:
            match = re.match(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\]", src_key)
            if match:
                track_i, clip_i, node_i = match.groups()
                return channel(self.tracks[int(track_i)].clips[int(clip_i)].node_collection.nodes[int(node_i)], in_out_key)

    def execute(self, full_command):
        global IO_OUTPUTS
        global IO_INPUTS
        print(full_command)

        toks = full_command.split()
        cmd = toks[0]
        
        if self.mode == "performance":
            if cmd == "toggle_clip":
                track_id = toks[1]
                clip_id = toks[2]
                track = self.get_obj(track_id)
                clip = self.get_obj(clip_id)
                for other_clip in track.clips:
                    if other_clip is None or clip == other_clip:
                        continue
                    other_clip.stop()
                clip.toggle()
                if clip.playing:
                    self.playing = True
                return True


        if cmd == "new_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            print(track.outputs)
            track[clip_i] = Clip(track.outputs)
            return True, track[clip_i]

        elif cmd == "create_input":
            clip_id = toks[1]
            dtype = toks[2]
            clip = self.get_obj(clip_id)
            new_input_channel = clip.create_input(dtype)
            return True, new_input_channel

        elif cmd == "create_output":
            track_id = toks[1]
            track = self.get_obj(track_id)
            new_output_channel = track.create_output()
            self.all_track_outputs.append(new_output_channel)
            return True, new_output_channel

        elif cmd == "create_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]
            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return True, clip.node_collection.add_link(src_channel, dst_channel)

        elif cmd == "delete_link":
            clip_id = toks[1]
            src_id, dst_id = toks[2].split(":")

            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return clip.node_collection.del_link(src_channel, dst_channel)

        elif cmd == "create_node":
            clip_id = toks[1]
            type_id = toks[2]
            args = toks[3]

            clip = self.get_obj(clip_id)

            if type_id == "none":
                node = clip.node_collection.add_node(None, None)
            elif type_id == "binary_operator":
                node = clip.node_collection.add_node(BinaryOperator, None)
            elif type_id == "demux":
                n = int(args)
                node = clip.node_collection.add_node(Demux, n)
            elif type_id == "multiplexer":
                n = int(args)
                node = clip.node_collection.add_node(Multiplexer, n)
            elif type_id == "random":
                node = clip.node_collection.add_node(Random, None)
            elif type_id == "sample":
                node = clip.node_collection.add_node(Sample, None)
            elif type_id == "buffer":
                node = clip.node_collection.add_node(Buffer, None)
            return True, node

        elif cmd == "delete":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            obj.deleted = True
            return True

        elif cmd == "set_active_automation":
            input_id = toks[1]
            automation_id = toks[2]
            input_channel = self.get_obj(input_id)
            automation = self.get_obj(automation_id)
            input_channel.set_active_automation(automation)
            return True 

        elif cmd == "add_automation":
            input_id = toks[1]
            clear = True if len(toks) == 3 else False
            input_channel = self.get_obj(input_id)
            return True, input_channel.add_automation(clear=clear)

        elif cmd == "add_automation_point":
            automation_id = toks[1]
            point = toks[2]
            automation = self.get_obj(automation_id)
            automation.add_point([float(x) for x in point.split(",")])
            return True

        elif cmd == "update_automation_point":
            automation_id = toks[1]
            point_index = toks[2]
            point = toks[3]
            automation = self.get_obj(automation_id)
            automation.update_point(
                int(point_index), [float(x) for x in point.split(",")]
            )
            return True

        elif cmd == "update_parameter":
            node_id = toks[1]
            param_i = toks[2]
            value = toks[3]
            node = self.get_obj(node_id)
            node.update_parameter(int(param_i), value)
            return True

        elif cmd == "update_channel_value":
            input_id = toks[1]
            value = toks[2]
            input_channel = self.get_obj(input_id)
            input_channel.set(float(value))
            return True

        elif cmd == "remove_automation_point":
            src = toks[1]
            point_index = toks[2]
            input_channel = self.get_obj(src)
            automation = input_channel.active_automation
            return automation.remove_point(int(point_index))

        elif cmd == "create_io_output":
            index = int(toks[1])
            io_type = toks[2]
            args = toks[3]
            if io_type == "node_dmx_client":
                IO_OUTPUTS[index] = NodeDmxClientOutput(args)
                return True
        print("Previous command failed")

    def get_obj(self, id_):
        if id_.startswith("*"):
            return self.get_obj_ptr(id_[1::])
        else:
            try:
                return UUID_DATABASE[id_]
            except:
                print(UUID_DATABASE)
                raise

    def get_obj_ptr(self, item_key):
        if item_key.startswith("state"):
            return self

        # Track
        match = re.fullmatch(r"track\[(\d+)\]", item_key)
        if match:
            ti = match.groups()[0]
            return self.tracks[int(ti)]

        # Clip
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]", item_key)
        if match:
            ti, ci = match.groups()
            return self.tracks[int(ti)].clips[int(ci)]

        # Output
        match = re.fullmatch(r"track\[(\d+)\]\.out\[(\d+)\]", item_key)
        if match:
            ti, oi = match.groups()
            return self.tracks[int(ti)].outputs[int(oi)]

        # Clip Input
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.in\[(\d+)\]", item_key)
        if match:
            ti, ci, ii = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].inputs[int(ii)]

        # Clip Output
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.out\[(\d+)\]", item_key)
        if match:
            ti, ci, oi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].outputs[int(oi)]

        # Node Input
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\].in\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, ii = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].inputs[int(ii)]

        # Node Output
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\].out\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, oi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].outputs[int(oi)]

        # Automation
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.in\[(\d+)\]\.automation\[(\d+)\]", item_key)
        if match:
            ti, ci, ii, ai = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].inputs[int(ii)].automations[int(ai)]

        # Node
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\]", item_key)
        if match:
            ti, ci, ni = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)]

        # Parameter
        match = re.fullmatch(r"track\[(\d+)\]\.clip\[(\d+)\]\.node\[(\d+)\]\.parameter\[(\d+)\]", item_key)
        if match:
            ti, ci, ni, pi = match.groups()
            return self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].parameters[int(pi)]

        raise Exception(f"Failed to find {item_key}")

    def get_clip_from_ptr(self, clip_key):
        """*track[i].clip[j].+ -> Clip"""
        match = re.match(r"track\[(\d+)\]\.clip\[(\d+)\]", clip_key[1::])
        if match:
            track_i = int(match.groups()[0])
            clip_i = int(match.groups()[1])
            return self.tracks[track_i][clip_i]
        raise RuntimeError(f"Failed to find clip for {clip_key}")

    def read_state(self, f):
        for line in f:
            line = line.strip()
            if not line:
                continue
            toks = line.strip().split()
            if toks[0] == "execute":
                self.execute(" ".join(toks[1::]))
            else:
                toks = line.split(":", 1)
                obj_id, attr_name = toks[0].rsplit(".", 1)
                value = toks[1]
                obj = self.get_obj(obj_id)
                setattr(obj, attr_name, eval(value))

    def dump_state(self, f):
        print("# Serialize")
        for track_i, track in enumerate(self.tracks):
            print(track.serialize(i=track_i))
        print("# End Serialize")
        
        def save(line):
            print(line)
            f.write(line + "\n")

        for attr in self._attrs_to_dump:
            save(f"*state.{attr}:{repr(getattr(self, attr))}")

        # Clips
        for track_i, track in enumerate(self.tracks):
            # Keep track of ID's to pointers
            id_to_ptr = {}
            track_ptr = f"*track[{track_i}]"
            save(f"{track_ptr}.name:{repr(track.name)}")

            for output_i, output_channel in enumerate(track.outputs):
                output_ptr = f"{track_ptr}.out[{output_i}]"
                id_to_ptr[output_channel.id] = output_ptr
                save(f"execute create_output {track_ptr}")
                if output_channel.deleted:
                    save(f"execute delete {output_ptr}")
                else:
                    save(f"{output_ptr}.name:{repr(output_channel.name)}")
                    save(f"{output_ptr}.dmx_channel:{repr(output_channel.dmx_channel)}")

            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue
                clip_ptr = f"{track_ptr}.clip[{clip_i}]"
                save(f"execute new_clip {track_ptr},{clip_i}")
                save(f"{clip_ptr}.title:{repr(clip.title)}")
                save(f"{clip_ptr}.speed:{repr(clip.speed)}")
                save(f"{clip_ptr}.playing:{repr(clip.playing)}")

                for input_i, input_channel in enumerate(clip.inputs):
                    dtype = "none" if input_channel.deleted else input_channel.dtype
                    save(f"execute create_input {clip_ptr} {dtype}")
                    input_ptr = f"{clip_ptr}.in[{input_i}]"

                    if input_channel.deleted:
                        save(f"execute delete {input_ptr}")
                    else:
                        id_to_ptr[input_channel.id] = input_ptr
                        save(f"execute update_channel_value {input_ptr} {input_channel.get()}")
                        save(f"{input_ptr}.name:{repr(input_channel.name)}")
                        save(f"{input_ptr}.automation_enabled:{repr(input_channel.automation_enabled)}")
                        for auto_i, automation in enumerate(input_channel.automations):
                            if automation.deleted:
                                continue
                            auto_ptr = f"{input_ptr}.automation[{auto_i}]"
                            save(f"execute add_automation {input_ptr} clear")
                            save(f"{auto_ptr}.length:{repr(automation.length)}")
                            save(f"{auto_ptr}.name:{repr(automation.name)}")
                            for point_i in range(len(automation.values_x)):
                                x, y = automation.values_x[point_i], automation.values_y[point_i]
                                if x is None:
                                    continue
                                save(f"execute add_automation_point {auto_ptr} {x},{y}")

                for node_i, node in enumerate(clip.node_collection.nodes):
                    node_type = "none" if node.deleted else node.type
                    args = "," if node.deleted else (node.args or ",")
                    save(f"execute create_node {clip_ptr} {node_type} {args}")
                    if node.deleted:
                        continue
                    node_ptr = f"{clip_ptr}.node[{node_i}]"
                    save(f"{node_ptr}.name:{repr(node.name)}")
                    for param_i, parameter in enumerate(node.parameters):
                        save(f"execute update_parameter {node_ptr} {param_i} {parameter.value}")
                    for input_i, input_channel in enumerate(node.inputs):
                        input_ptr = f"{node_ptr}.in[{input_i}]"
                        id_to_ptr[input_channel.id] = input_ptr
                        save(f"execute update_channel_value {input_ptr} {input_channel.get()}")
                    for output_i, output_channel in enumerate(node.outputs):
                        output_ptr = f"{node_ptr}.out[{output_i}]"
                        id_to_ptr[output_channel.id] = output_ptr

                for link in clip.node_collection.links:
                    if link.deleted:
                        continue
                    save(f"execute create_link {clip_ptr} {id_to_ptr[link.src_channel.id]} {id_to_ptr[link.dst_channel.id]}")
                    
