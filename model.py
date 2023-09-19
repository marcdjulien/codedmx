from collections import defaultdict
import re
from threading import Lock
import scipy
import time

class Channel:
    def __init__(self, direction="in", value=0):
        self._value = value
        self.direction = direction

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class ChannelLink:
    def __init__(self, src_channel, dst_channel):
        self.src_channel = src_channel
        self.dst_channel = dst_channel

    def update(self):
        self.dst_channel.set(self.src_channel.get())


class Node:
    """Represents a clip that defines a set of inputs, outputs, and transformation between."""

    def __init__(self):
        super().__init__()
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.type = None

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


class AddNode(Node):
    

    def __init__(self):
        super().__init__()
        self.inputs = [Channel("in", 0), Channel("in", 0)]
        self.outputs.append(Channel("out", 0))
        self.type = "add_node"

    def transform(self):
        if self.outputs:
            self.outputs[0].set(sum(inp.get() for inp in self.inputs))


class NodeCollection:
    """Collection of nodes and their the a set of inputs and outputs"""

    def __init__(self):
        self.inputs: Channel = []
        self.nodes: Node = []  # Needs to be a tree
        self.outputs: Channel = []
        self.links = []

    def add_input(self, inp: Channel):
        self.inputs.append(inp)

    def add_output(self, output: Channel):
        self.outputs.append(output)

    def add_node(self, node: Node):
        self.nodes.append(node)

    def add_link(self, src_channel, dst_channel):
        assert src_channel.direction == "out"
        assert dst_channel.direction == "in"
        self.links.append(ChannelLink(src_channel, dst_channel))

    def update(self):
        # Transform each node
        # TODO: BFS tree
        for link in self.links:
            link.update()

        for node in self.nodes:
            node.transform()


class ChannelAutomation:

    def __init__(self, length):
        # TODO: Turn this into its own class that handles interpolation
        self.values_x = [0, length]
        self.values_y = [0, 255]
        self.enabled = True
        self.f = scipy.interpolate.interp1d(self.values_x, self.values_y)
    
    def value(self, index):
        return int(self.f(index))

    def add_point(self, p1):
        self.values_x.append(p1[0])
        self.values_y.append(p1[1])
        self.reinterpolate()

    def remove_point(self, index):
        self.values_x[index] = None
        self.values_y[index] = None

    def update_point(self, index, p1):
        self.values_x[index] = p1[0]
        self.values_y[index] = p1[1]
        self.reinterpolate()

    def length(self):
        return len(self.values_x)

    def reinterpolate(self):
        values_x = [x for x in self.values_x if x is not None]
        values_y = [y for y in self.values_y if y is not None]
        self.f = scipy.interpolate.interp1d(values_x, values_y, assume_sorted=False)

class Clip:
    def __init__(self):
        self.title = "Untitled"

        self.node_collection = NodeCollection()

        # Maps automation to an input or output
        self.automation_map = {}

        # In beats
        self.length = 4

        # Speed to play clip
        self.speed = 0

        self.time = 0

    def create_input(self):
        new_inp = Channel("out")
        new_automation = ChannelAutomation(self.length)
        self.automation_map[new_inp] = new_automation
        self.node_collection.add_input(new_inp)

    def create_output(self):
        new_output = Channel("in")
        self.node_collection.add_output(new_output)

    def update(self, beat):
        self.time = (beat * (2**self.speed)) % self.length
        for channel, automation in self.automation_map.items():
            if automation.enabled:
                value = automation.value(self.time)
                channel.set(value)

        self.node_collection.update()


class Track:
    def __init__(self, title, n_clips=20):
        self.name = title
        self.clips = [None] * n_clips

    def __delitem__(self, key):
        del clips[key]

    def __getitem__(self, key):
        return self.clips[key]

    def __setitem__(self, key, value):
        self.clips[key] = value

    def __len__(self):
        return len(self.clips)

class ProgramState:
    def __init__(self):
        self.project_name = "Untitled"
        self.project_filepath = None
        self.tracks = []
        
        for i in range(10):
            self.tracks.append(Track(f"Track {i}"))

        self._active_clip = None

        self.playing = False
        self.tempo = 120.0
        self.play_time_start = 0
        self.beats_since_start = 0
    def update(self):
        if self.playing:
            time_since_start_s = time.time() - self.play_time_start
            self.beats_since_start = time_since_start_s * (1.0/60.0) * self.tempo

        for track in self.tracks:
            for clip in track.clips:
                if clip is not None:
                    clip.update(self.beats_since_start)

    def get_clip(self, clip_key):
        """clip[i,j] -> Clip"""
        match = re.match(r"clip\[(\d+),(\d+)\]", clip_key)
        if match:
            track_i = int(match.groups()[0])
            clip_i = int(match.groups()[1])
            return self.tracks[track_i][clip_i]

    def get_channel(self, clip, key):
        def channel(src, in_out_key):
            match = re.match(r"(in|out)\[(\d+)]", index_key)
            if match:
                in_out, index = match.groups()
                inout_list = src.inputs if in_out == "in" else src.outputs
                return inout_list[int(index)]

        src_key, index_key = key.split(".")
        if src_key == "clip":
            return channel(clip.node_collection, index_key)
        elif src_key.startswith("node"):
            match = re.match(r"node\[(\d+)\]", src_key)
            if match:
                node_index = int(match.groups()[0])
                return channel(clip.node_collection.nodes[int(node_index)], index_key)

    def execute(self, full_command):
        print(full_command)

        toks = full_command.split()
        cmd = toks[0]
        if cmd == "new_clip":
            track_i, clip_i = toks[1].split(",")
            track_i = int(track_i)
            clip_i = int(clip_i)
            assert track_i < len(self.tracks)
            assert clip_i < len(self.tracks[track_i])
            self.tracks[track_i][clip_i] = Clip()
            return True

        elif cmd == "create_input":
            clip_id = toks[1]
            clip = self.get_clip(clip_id)
            clip.create_input()
            return True

        elif cmd == "create_output":
            clip_id = toks[1]
            clip = self.get_clip(clip_id)
            clip.create_output()
            return True

        elif cmd == "create_link":
            src = toks[1]
            dst = toks[2]

            src_clip_id, src_index_id = src.split(".", 1)
            dst_clip_id, dst_index_id = dst.split(".", 1)
            src_clip = self.get_clip(src_clip_id)
            dst_clip = self.get_clip(dst_clip_id)
            assert src_clip == dst_clip

            clip = src_clip
            if src_clip is not None:
                src_channel = self.get_channel(clip, src_index_id)
                dst_channel = self.get_channel(clip, dst_index_id)
                assert src_clip
                assert dst_channel
            src_clip.node_collection.add_link(src_channel, dst_channel)
            return True

        elif cmd == "create_node":
            clip_id = toks[1]
            type_id = toks[2]

            clip = self.get_clip(clip_id)
            if clip is None:
                return False

            if type_id == "add_node":
                clip.node_collection.add_node(AddNode())

            return True
        elif cmd == "add_automation_point":
            src = toks[1]
            point = toks[2]
            clip_id, index_id = src.split(".", 1)
            clip = self.get_clip(clip_id)
            if clip is None:
                return False
            input_channel = self.get_channel(clip, index_id)
            if input_channel is None:
                return False
            automation = clip.automation_map[input_channel]
            automation.add_point([float(x) for x in point.split(",")])
            return True
        elif cmd == "update_automation_point":
            src = toks[1]
            point_index = toks[2]
            point = toks[3]
            clip_id, index_id = src.split(".", 1)
            clip = self.get_clip(clip_id)
            if clip is None:
                return False
            input_channel = self.get_channel(clip, index_id)
            if input_channel is None:
                return False
            automation = clip.automation_map[input_channel]
            automation.update_point(
                int(point_index), [float(x) for x in point.split(",")]
            )
            return True
        elif cmd == "remove_automation_point":
            src = toks[1]
            point_index = toks[2]
            clip_id, index_id = src.split(".", 1)
            clip = self.get_clip(clip_id)
            if clip is None:
                return False
            input_channel = self.get_channel(clip, index_id)
            if input_channel is None:
                return False
            automation = clip.automation_map[input_channel]
            automation.remove_point(int(point_index))
            return True
