from collections import defaultdict
import re
from threading import Lock
import scipy
import numpy as np
import time
import operator
import random
import dmxio
import uuid 
import math
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server
import threading 
import mido
import json
import logging

logger = logging.getLogger(__name__)

# For Custom Fuction Nodes
import colorsys
from math import *

def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)

MAX_VALUES = {
    "bool": 1,
    "int": 255,
    "float": 100.,
}

NEAR_THRESHOLD = 0.01

TYPES = ["bool", "int", "float", "any"]

UUID_DATABASE = {}

ID_COUNT = 0

def update_name(name, other_names):
    def toks(obj_name):
        match = re.fullmatch(r"(\D*)(\d*)$", obj_name)
        if match:
            prefix, number = match.groups()
            if not number:
                number = 0
            else:
                number = int(number)
            return prefix, number
        else:
            return None, None

    my_prefix, my_number = toks(name)
    if my_prefix is None:
        return f"{name}-1"

    for other_name in other_names:
        other_prefix, other_number = toks(other_name)
        if other_prefix is None:
            continue
        if my_prefix == other_prefix:
            my_number = max(my_number, other_number)
    return f"{my_prefix}{my_number+1}"

# Outputs cannot be copied.
NOT_COPYABLE = [
    "DmxOutput",
    "DmxOutputGroup",
]

def new_ids(data):
    uuid_pattern = r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
    string_data = json.dumps(data)
    obj_ids = re.findall(rf"(\w+\[{uuid_pattern}\])", string_data)
    if not obj_ids:
        return json.loads(string_data)
    obj_ids = set(obj_ids)
    for old_id in obj_ids:
        match2 = re.match(rf"(\w+)\[{uuid_pattern}\]", old_id)
        if not match2:
            raise Exception("Failed to replace ids")
        class_name = match2.groups()[0]
        if class_name in NOT_COPYABLE:
            continue
        
        new_id = f"{class_name}[{uuid.uuid4()}]"
        string_data = string_data.replace(old_id, new_id)

    return json.loads(string_data)


def clear_database():
    global UUID_DATABASE
    global ID_COUNT
    ID_COUNT = 0
    UUID_DATABASE = {}

class Identifier:
    def __init__(self):
        global UUID_DATABASE
        global ID_COUNT
        self.id = f"{self.__class__.__name__}[{uuid.uuid4()}]"
        ID_COUNT += 1
        UUID_DATABASE[self.id] = self
        self.deleted = False

    def delete(self):
        self.deleted = True

    def serialize(self):
        return {"id": self.id}

    def deserialize(self, data):
        global UUID_DATABASE
        self.id = data["id"]
        UUID_DATABASE[self.id] = self


cast = {
    "bool": int,
    "int": int,
    "float": float,
    "any": lambda x: x
}


class Channel(Identifier):
    def __init__(self, **kwargs):
        super().__init__()
        self.value = kwargs.get("value")
        self.size = kwargs.get("size", 1)

        if self.value is None:
            self.value = 0 if self.size == 1 else [0]*self.size

        self.direction = kwargs.get("direction", "in")
        self.dtype = kwargs.get("dtype", "float")
        self.name = kwargs.get("name", "Channel")

    def get(self):
        try:
            return cast[self.dtype](self.value)
        except:
            return 0

    def set(self, value):
        self.value = value

    def serialize(self):
        data = super().serialize()
        data.update({
            "value": self.value,
            "size": self.size,
            "direction": self.direction,
            "dtype": self.dtype,
            "name": self.name,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.direction = data["direction"]
        self.value = data["value"]
        self.dtype = data["dtype"]
        self.name = data["name"]
        self.size = data["size"]


class Parameter(Identifier):
    def __init__(self, name="", value=None, dtype="any"):
        super().__init__()
        self.name = name
        self.value = value
        self.dtype = dtype

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "value": self.value,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        self.value = data["value"]



class Parameterized(Identifier):
    def __init__(self):
        super().__init__()
        self.parameters = []

    def update_parameter(self, index, value):
        if 0 <= index < len(self.parameters):
            return True
        else:
            return False

    def add_parameter(self, parameter):
        assert self.get_parameter(parameter.name) is None
        n = len(self.parameters)
        self.parameters.append(parameter)
        return n

    def get_parameter(self, parameter_name):
        for parameter in self.parameters:
            if parameter_name == parameter.name:
                return parameter
        return None

    def get_parameter_id(self, parameter_name):
        parameter = self.get_parameter(parameter_name)
        if parameter is not None:
            return parameter.id

    def serialize(self):
        data = super().serialize()
        data.update({"parameters": []})
        for parameter in self.parameters:
            data["parameters"].append(parameter.serialize())
        return data

    def deserialize(self, data):
        super().deserialize(data)
        parameters_data = data["parameters"]
        for i, parameter_data in enumerate(parameters_data):
            self.parameters[i].deserialize(parameter_data)
            self.update_parameter(i, str(self.parameters[i].value))


class SourceNode(Parameterized):
    def __init__(self, **kwargs):
        super().__init__()
        kwargs.setdefault("direction", "out")
        self.name = kwargs.get("name", "")
        self.channel = Channel(**kwargs)
        self.input_type = None

    def update(self, clip_beat):
        pass

    @property
    def dtype(self):
        return self.channel.dtype 

    @property
    def direction(self):
        return self.channel.direction 

    @property
    def value(self):
        return self.channel.value 

    @property
    def size(self):
        return self.channel.size 

    def set(self, value):
        self.channel.set(value)

    def get(self):
        return self.channel.get()

    def serialize(self):
        data = super().serialize()

        data.update({
            "name": self.name,
            "channel": self.channel.serialize(),
            "input_type": self.input_type,
        })

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.channel.deserialize(data["channel"])
        self.input_type = data["input_type"]


class AutomatableSourceNode(SourceNode):
    nice_title = "Input"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.input_type = kwargs.get("dtype", "float")
        self.ext_channel = Channel(**kwargs)

        self.mode = "automation"
        self.min_parameter = Parameter("min", 0)
        self.max_parameter = Parameter("max",  MAX_VALUES[self.channel.dtype])
        self.add_parameter(self.min_parameter)
        self.add_parameter(self.max_parameter)


        self.automations = []
        self.active_automation = None
        self.speed = 0
        self.last_beat = 0

    def update(self, clip_beat):
        beat = (clip_beat * (2**self.speed))
        current_beat = beat % self.active_automation.length
        restarted = current_beat < self.last_beat

        if self.mode == "armed":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.channel.set(value)
            if restarted:
                self.mode = "recording"
                self.active_automation.clear()
        elif self.mode == "recording":
            if restarted:
                self.mode = "automation"
            point = Point(current_beat, self.ext_channel.get())
            self.active_automation.add_point(point, replace_near=True)
            self.channel.set(self.ext_channel.get())
        elif self.mode == "automation":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.channel.set(value)
        else: # manual
            self.channel.set(self.ext_channel.get())

        self.last_beat = current_beat

    def ext_get(self):
        return self.ext_channel.get()

    def ext_set(self, value):
        value = max(self.min_parameter.value, value)
        value = min(self.max_parameter.value, value)
        self.ext_channel.set(value)

    def set(self, value):
        value = max(self.min_parameter.value, value)
        value = min(self.max_parameter.value, value)
        super().set(value)

    def set_active_automation(self, automation):
        assert automation in self.automations
        self.active_automation = automation
        return True

    def add_automation(self, automation=None):
        n = len(self.automations)
        new_automation = automation or ChannelAutomation(
            self.channel.dtype, 
            f"Preset #{n}", 
            min_value=self.get_parameter("min").value, 
            max_value=self.get_parameter("max").value, 
        )
        self.automations.append(new_automation)
        self.set_active_automation(new_automation)
        return new_automation

    def update_parameter(self, index, value):
        if self.parameters[index] in [self.min_parameter, self.max_parameter]:
            self.parameters[index].value = cast[self.channel.dtype](value)
            min_value = self.min_parameter.value
            max_value = self.max_parameter.value
            for automation in self.automations:
                if automation.deleted:
                    continue
                for point in automation.points:
                    if point.deleted:
                        continue
                    point.y = clamp(point.y, min_value, max_value)
            return True
        else:
            return super().update_parameter(index, value)

    def serialize(self):
        data = super().serialize()
        
        data.update({
            "ext_channel": self.ext_channel.serialize(),
            "mode": self.mode,
            "active_automation": self.active_automation.id if self.active_automation else None,
            "automations": [automation.serialize() for automation in self.automations if not automation.deleted],
            "speed": self.speed,
        })
        
        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.ext_channel.deserialize(data["ext_channel"])
        self.mode = data["mode"]
        self.speed = data["speed"]

        for automation_data in data["automations"]:
            automation = ChannelAutomation()
            automation.deserialize(automation_data)
            self.automations.append(automation)
        self.set_active_automation(UUID_DATABASE[data["active_automation"]])
        

class DmxOutput(Channel):
    def __init__(self, dmx_address=1, name=""):
        super().__init__(direction="in", dtype="int", name=name or f"DMX CH. {dmx_address}")
        self.dmx_address = dmx_address
        self.history = [0] * 500

    def record(self):
        self.history.pop(0)
        self.history.append(self.value)

    def serialize(self):
        data = super().serialize()
        data.update({
            "dmx_address": self.dmx_address,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.dmx_address = data["dmx_address"]


class DmxOutputGroup(Identifier):

    def __init__(self, channel_names=[], dmx_address=1, name="Group"):
        super().__init__()
        self.name = name
        self.dmx_address = dmx_address
        self.outputs: DmxOutput = []
        self.channel_names = channel_names
        for i, channel_name in enumerate(channel_names):
            output_channel = DmxOutput()
            self.outputs.append(output_channel)
        self.update_starting_address(dmx_address)
        self.update_name(name)

    def record(self):
        for output in self.outputs:
            output.record()

    def update_starting_address(self, address):
        for i, output_channel in enumerate(self.outputs):
            output_channel.dmx_address = i + address

    def update_name(self, name):
        for i, output_channel in enumerate(self.outputs):
            output_channel.name = f"{name}.{self.channel_names[i]}"

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "dmx_address": self.dmx_address,
            "channel_names": self.channel_names,
            "outputs": [],
        })

        for output_channel in self.outputs:
            data["outputs"].append(output_channel.serialize())
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        self.dmx_address = data["dmx_address"]
        self.channel_names = data["channel_names"]
        for i, output_data in enumerate(data["outputs"]):
            self.outputs[i].deserialize(output_data)


class ColorNode(SourceNode):
    nice_title = "Color"

    def __init__(self, **kwargs):
        kwargs.setdefault("dtype", "any")
        kwargs.setdefault("size", 3)
        super().__init__(**kwargs) 
        self.input_type = "color"
        self.int_out_parameter = Parameter("Int", value=False, dtype="bool")
        self.add_parameter(self.int_out_parameter)

    def set(self, value):
        if self.int_out_parameter.value:
            value = [int(255*v) for v in value]
        self.channel.set(value)

    def update_parameter(self, index, value):
        if self.parameters[index] == self.int_out_parameter:
            self.parameters[index].value = value.lower() == "true"
            return True
        else:
            return super().update_parameter(index, value)


class ButtonNode(SourceNode):
    nice_title = "Button"

    def __init__(self, **kwargs):
        kwargs.setdefault("dtype", "bool")
        kwargs.setdefault("size", 1)
        super().__init__(**kwargs) 
        self.input_type = "button"
        self.value_parameter = Parameter("Value", value=False, dtype="bool")
        self.add_parameter(self.value_parameter)

    def update(self, clip_beat):
        self.channel.set(int(self.value_parameter.value))

    def update_parameter(self, index, value):
        if self.parameters[index] == self.value_parameter:
            self.parameters[index].value = value.lower() == "true"
            return True
        else:
            return super().update_parameter(index, value)


class OscInput(AutomatableSourceNode):
    def __init__(self, **kwargs):
        kwargs.setdefault("name", f"OSC")
        kwargs.setdefault("direction", "out")
        kwargs.setdefault("dtype", "int")
        super().__init__(**kwargs)
        self.endpoint_parameter = Parameter("endpoint", value="/")
        self.add_parameter(self.endpoint_parameter)
        self.input_type = "osc_input_" + self.dtype

    def update_parameter(self, index, value):
        if self.parameters[index] == self.endpoint_parameter:
            if value.startswith("/"):
                self.parameters[index].value = value
                global_osc_server().map_channel(value, self)
            return True
        else:
            return super().update_parameter(index, value)


class MidiInput(AutomatableSourceNode):
    
    def __init__(self, **kwargs):
        kwargs.setdefault("name", "MIDI")
        kwargs.setdefault("direction", "out")
        kwargs.setdefault("dtype", "int")
        super().__init__(**kwargs)
        self.device_parameter = Parameter("device", value="")
        self.id_parameter = Parameter("id", value="/")
        self.add_parameter(self.device_parameter)
        self.add_parameter(self.id_parameter)
        self.input_type = "midi"

    def update_parameter(self, index, value):
        if self.parameters[index] == self.device_parameter:
            if not value:
                return False
            self.parameters[index].value = value
            return True
        elif self.parameters[index] == self.id_parameter:
            if self.device_parameter.value is None:
                return False
            result = value.split("/")
            if not len(result) == 2 and result[0] and result[1]:
                return False
            self.parameters[index].value = value
            return True
        else:
            return super().update_parameter(index, value)


class ChannelLink(Identifier):
    def __init__(self, src_channel=None, dst_channel=None):
        super().__init__()
        self.src_channel = src_channel
        self.dst_channel = dst_channel

    def update(self):
        self.dst_channel.set(self.src_channel.get())

    def contains(self, channel):
        return self.src_channel == channel or self.dst_channel == channel

    def serialize(self):
        data = super().serialize()
        data.update({
            "src_channel": self.src_channel.id,
            "dst_channel": self.dst_channel.id,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.src_channel = UUID_DATABASE[data["src_channel"]]
        self.dst_channel = UUID_DATABASE[data["dst_channel"]]


class FunctionNode(Parameterized):

    def __init__(self, args="", name=""):
        super().__init__()
        self.name = name
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.args = args
        self.type = None

    def transform(self):
        raise NotImplemented

    def outputs(self):
        return self.outputs

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "type": self.type,
            "args": self.args,
            "inputs": [channel.serialize() for channel in self.inputs],
            "outputs": [channel.serialize() for channel in self.outputs],
        })

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.type = data["type"]
        self.args = data["args"]

        # Some nodes create their inputs/outputs dynamically
        # Ensure the size is correct before deerializing.
        self.inputs = [None] * len(data["inputs"])
        self.outputs = [None] * len(data["outputs"])
        for i, input_data in enumerate(data["inputs"]):
            channel = Channel()
            channel.deserialize(input_data)
            self.inputs[i] = channel
        for i, output_data in enumerate(data["outputs"]):
            channel = Channel()
            channel.deserialize(output_data)
            self.outputs[i] = channel


class FunctionCustomNode(FunctionNode):
    nice_title = "Custom"

    def __init__(self, args="", name="Custom"):
        super().__init__(args, name)
        self.name = name
        self.n_in_parameter = Parameter("n_inputs", 0)
        self.n_out_parameter = Parameter("n_outputs", 0)
        self.code_parameter = Parameter("code", "")
        self.add_parameter(self.n_in_parameter)
        self.add_parameter(self.n_out_parameter)
        self.add_parameter(self.code_parameter)
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.type = "custom"
        self._vars = defaultdict(float)

        arg_v = args.split(",", 2)
        if len(arg_v) == 3:
            n_inputs = int(arg_v[0])
            n_outputs = int(arg_v[1])
            code = arg_v[2]
            self.update_parameter(0, n_inputs)
            self.update_parameter(1, n_outputs)
            self.update_parameter(2, code)

    def transform(self):
        code = self.parameters[2].value
        if code is None:
            return

        code = code.replace("[NEWLINE]", "\n")

        i = dict()
        o = dict()
        v = self._vars
        for input_i, input_channel in enumerate(self.inputs):
            i[input_i] = input_channel.get()
        try:
            exec(code)
        except BaseException as e:
            print(F"Error: {e}")
            return

        for output_i, output_channel in enumerate(self.outputs):
            output_channel.set(o.get(output_i, 0))

    def outputs(self):
        return self.outputs

    def update_parameter(self, index, value):
        channels = []
        if self.parameters[index] == self.n_in_parameter:
            n = int(value)
            if n < 0:
                return False

            delta = n - len(self.inputs)
            if n < len(self.inputs):
                for i in range(n, len(self.inputs)):
                    channels.append(self.inputs.pop(-1))
            elif n > len(self.inputs):
                for _ in range(delta):
                    i = len(self.inputs)
                    new_channel = Channel(direction="in", dtype="any", name=f"i[{i}]")
                    self.inputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[0].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.n_out_parameter:
            n = int(value)
            if n < 0:
                return False
                
            delta = n - len(self.outputs)
            if n < len(self.outputs):
                for i in range(n, len(self.outputs)):
                    channels.append(self.outputs.pop(-1))
            elif n > len(self.outputs):
                for _ in range(delta):
                    i = len(self.outputs)
                    new_channel = Channel(direction="out", dtype="any", name=f"o[{i}]")
                    self.outputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[1].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.code_parameter:
            self.parameters[2].value = value.replace("\n", "[NEWLINE]")
            return True
        else:
            return super().update_parameter(index, value)


class FunctionBinaryOperator(FunctionNode):
    nice_title = "Binary Operator"

    def __init__(self, args="", name="Operator"):
        super().__init__(args, name)
        self.op_parameter = Parameter("op")
        self.add_parameter(self.op_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"x"), 
            Channel(direction="in", value=0, name=f"y"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"z")
        ]
        self.type = "binary_operator"
        self.f = None

    def transform(self):
        # TODO: Handle division by zero
        if self.f is not None:
            if self.parameters[0].value == "/" and self.inputs[1].get() == 0:
                return
            self.outputs[0].set(self.f(self.inputs[0].get(), self.inputs[1].get()))

    def update_parameter(self, index, value):
        if self.parameters[index] == self.op_parameter and value in ["+", "-", "/", "*", "%", "=="]:
            self.parameters[index].value = value

            # TODO: Add other operators
            self.f = {
                "+": operator.add,
                "-": operator.sub,
                "/": operator.truediv,
                "*": operator.mul,
                "%": operator.mod,
                "==": operator.eq,
            }[value]
            return True
        else:
            return super().update_parameter(index, value)


class FunctionSequencer(FunctionNode):
    nice_title = "Sequencer"

    def __init__(self, args="", name="Sequencer"):
        super().__init__(args, name)
        self.steps_parameter = Parameter("Steps", 4)
        self.step_length_parameter = Parameter("Step Legnth", 1)
        self.add_parameter(self.steps_parameter)
        self.add_parameter(self.step_length_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"beat"), 
            Channel(direction="in", dtype="any", size=4, name=f"seq"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"on")
        ]
        self.type = "sequencer"

    def transform(self):
        beat = self.inputs[0].get()
        seq = self.inputs[1].get()
        steps = self.steps_parameter.value
        step_length = self.step_length_parameter.value * 4

        step_n = int(((beat // step_length) - 1) % steps)

        if step_n <= len(seq):
            self.outputs[0].set(seq[step_n])

    def update_parameter(self, index, value):
        if self.parameters[index] == self.steps_parameter:
            if value.isnumeric():
                self.parameters[index].value = int(value)
            else:
                return False
            return True
        elif self.parameters[index] == self.step_length_parameter:
            if value.isnumeric():
                value = int(value)
            else:
                if "/" in value:
                    try:
                        numerator, denom = value.split("/")
                        value = float(numerator)/float(denom)
                    except Exception as e:
                        return False
                else:
                    return False
            self.parameters[index].value = value
            return True
        else:
            return super().update_parameter(index, value)

class FunctionScale(FunctionNode):
    nice_title = "Scale"

    def __init__(self, args="", name="Scale"):
        super().__init__(name)
        self.in_min_parameter = Parameter("in.min", 0)
        self.in_max_parameter = Parameter("in.max", 255)
        self.out_min_parameter = Parameter("out.min", 0)
        self.out_max_parameter = Parameter("out.max", 1)
        self.add_parameter(self.in_min_parameter)
        self.add_parameter(self.in_max_parameter)
        self.add_parameter(self.out_min_parameter)
        self.add_parameter(self.out_max_parameter)
        self.inputs = [
            Channel(direction="in", value=0, name=f"x"), 
        ]
        self.outputs = [
            Channel(direction="out", value=0, name=f"y")
        ]
        self.type = "scale"

    def transform(self):
        in_min = self.in_min_parameter.value
        in_max = self.in_max_parameter.value
        out_min = self.out_min_parameter.value
        out_max = self.out_max_parameter.value
        x = self.inputs[0].get()
        y = (((x - in_min)/(in_max - in_min))*(out_max-out_min)) + out_min
        self.outputs[0].set(y)

    def update_parameter(self, index, value):
        if self.parameters[index] in [self.in_min_parameter, self.in_max_parameter, self.out_min_parameter, self.out_max_parameter]:
            self.parameters[index].value = float(value)
            return True
        else:
            return super().update_parameter(index, value)


class FunctionDemux(FunctionNode):
    nice_title = "Demux"

    def __init__(self, args="0", name="Demux"):
        super().__init__(args, name)
        self.n = int(args)
        self.inputs = [
            Channel(direction="in", value=0, dtype="int", name=f"sel"),
            Channel(direction="in", dtype="any", name=f"val")
        ]
        for i in range(self.n):
            self.outputs.append(Channel(direction="out", dtype="any", name=f"{i+1}"))

        self.type = "demux"

    def transform(self):
        value = self.inputs[1].get()
        if isinstance(value, list):
            reset_value = [0] * len(value)
        else:
            reset_value = 0

        for output in self.outputs:
            output.set(reset_value)

        selected = int(self.inputs[0].get())
        if selected in range(self.n+1):
            if selected != 0:
                self.outputs[selected-1].set(value)


class FunctionMultiplexer(FunctionNode):
    nice_title = "Multiplexer"

    def __init__(self, args="0", name="Multiplexer"):
        super().__init__(args, name)
        self.n = int(args)
        self.inputs = [
            Channel(direction="in", value=1, dtype="int", name=f"sel")
        ]
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="any", name=f"out"))
        self.type = "multiplexer"

    def transform(self):
        selected = int(self.inputs[0].get())
        if selected in range(1, self.n+1):
            self.outputs[0].set(self.inputs[selected].get())


class FunctionPassthrough(FunctionNode):
    nice_title = "Passthrough"

    def __init__(self, args="", name="Passthrough"):
        super().__init__(args, name)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="any", name=f"out"))
        self.type = "passthrough"

    def transform(self):
        self.outputs[0].set(self.inputs[0].get())


class FunctionGlobalReceiver(FunctionNode):
    nice_title = "Global Receiver"

    def __init__(self, args="", name="Global Receiver"):
        super().__init__(args, name)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.var_parameter = Parameter("var", "name")
        self.add_parameter(self.var_parameter)
        self.type = "global_receiver"

    def transform(self):
        _STATE.global_vars[self.var_parameter.value] = self.inputs[0].get()

    def update_parameter(self, index, value):
        if self.parameters[index] == self.var_parameter:
            self.parameters[index].value = value
            return True
        else:
            return super().update_parameter(index, value)


class FunctionGlobalSender(FunctionNode):
    nice_title = "Global Sender"

    def __init__(self, args="", name="Global Sender"):
        super().__init__(args, name)
        self.outputs.append(Channel(direction="out", dtype="any", name=f"out"))
        self.var_parameter = Parameter("var", "name")
        self.add_parameter(self.var_parameter)
        self.type = "global_sender"

    def transform(self):
        self.outputs[0].set(_STATE.global_vars.get(self.var_parameter.value, 0))

    def update_parameter(self, index, value):
        if self.parameters[index] == self.var_parameter:
            self.parameters[index].value = value
            return True
        else:
            return super().update_parameter(index, value)


class FunctionTimeSeconds(FunctionNode):
    nice_title = "Seconds"

    def __init__(self, args="", name="Seconds"):
        super().__init__(args, name)
        self.outputs.append(Channel(direction="out", dtype="float", name="s"))
        self.type = "time_s"

    def transform(self):
        global _STATE
        self.outputs[0].set(_STATE.time_since_start_s)


class FunctionTimeBeats(FunctionNode):
    nice_title = "Beats"

    def __init__(self, args="", name="Beats"):
        super().__init__(args, name)
        self.outputs.append(Channel(direction="out", dtype="float", name="beat"))
        self.type = "time_beat"

    def transform(self):
        global _STATE
        self.outputs[0].set(_STATE.time_since_start_beat + 1)


class FunctionChanging(FunctionNode):
    nice_title = "Changing"

    def __init__(self, args="", name="Changing"):
        super().__init__(args, name)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="bool", name=f"out"))
        self.type = "changing"
        self._last_value = None

    def transform(self):
        changing = False
        new_value = self.inputs[0].get()
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            self._last_value = new_value

        self.outputs[0].set(int(changing))


class FunctionToggleOnChange(FunctionNode):
    nice_title = "Toggle On Change"

    def __init__(self, args="", name="ToggleOnChange"):
        super().__init__(args, name)
        self.rising_only_parameter = Parameter("Rising", False, dtype="bool")
        self.add_parameter(self.rising_only_parameter)
        self.inputs.append(Channel(direction="in", dtype="any", name=f"in"))
        self.outputs.append(Channel(direction="out", dtype="bool", name=f"out"))
        self.type = "toggle_on_change"
        self._last_value = None
        self._toggle_value = 0

    def transform(self):
        changing = False
        rising_only = self.rising_only_parameter.value
        new_value = self.inputs[0].get()
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            if changing and rising_only:
                changing = new_value
            self._last_value = new_value

        if changing:
            self._toggle_value = int(not self._toggle_value)
        self.outputs[0].set(self._toggle_value)
        
    def update_parameter(self, index, value):
        if self.parameters[index] == self.rising_only_parameter:
            self.parameters[index].value = value.lower() == "true"
            return True
        else:
            return super().update_parameter(index, value)


class FunctionLastChanged(FunctionNode):
    nice_title = "Last Changed"

    def __init__(self, args="0", name="LastChanged"):
        super().__init__(args, name)
        self.n = int(args)
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", value=0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="int", name=f"out{self.n}"))
        self.type = "last_changed"
        self._last_values = [None] * self.n
        self._last_changed_index = 0

    def transform(self):
        for i, last_value in enumerate(self._last_values):
            changing = False
            new_value = self.inputs[i].get()
            if isinstance(new_value, (list)):
                changing = tuple(new_value) == last_value
                self._last_values[i] = tuple(new_value)
            else:
                changing = last_value != new_value
                self._last_values[i] = new_value

            if changing:
                self._last_changed_index = i

        self.outputs[0].set(self._last_changed_index)


class FunctionAggregator(FunctionNode):
    nice_title = "Aggregator"

    def __init__(self, args="0", name="Aggregator"):
        super().__init__(args, name)
        self.n = int(args)
        for i in range(self.n):
            self.inputs.append(Channel(direction="in", value=0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel(direction="out", dtype="any", size=self.n, name=f"out{self.n}"))
        self.type = "aggregator"

    def transform(self):
        value = [channel.get() for channel in self.inputs]
        self.outputs[0].set(value)


class FunctionSeparator(FunctionNode):
    nice_title = "Separator"

    def __init__(self, args="0", name="Separator"):
        super().__init__(args, name)
        self.n = int(args)
        self.inputs.append(Channel(direction="in", dtype="any", size=self.n, name=f"in{self.n}"))

        for i in range(self.n):
            self.outputs.append(Channel(direction="out", dtype="any", name=f"out{self.n}"))
        self.type = "separator"

    def transform(self):
        values = self.inputs[0].get()
        for i, value in enumerate(values):
            if i < len(self.outputs):
                self.outputs[i].set(value)


class FunctionRandom(FunctionNode):
    nice_title = "Random"

    def __init__(self, args="", name="Random"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, dtype="int", name=f"a"),
            Channel(direction="in", value=1, dtype="int", name=f"b")
        ]
        self.outputs.append(
            Channel(direction="out", value=0, name=f"z")
        )
        self.type = "random"

    def transform(self):
        a = int(self.inputs[0].get())
        b = int(self.inputs[1].get())
        if a > b:
            return
        self.outputs[0].set(random.randint(a, b))


class FunctionSample(FunctionNode):
    nice_title = "Sample"

    def __init__(self, args="", name="Sample"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=1, name=f"rate", dtype="float"),
            Channel(direction="in", value=0, name=f"in", dtype="float")
        ]
        self.outputs.append(
            Channel(direction="out", value=0, name=f"out", dtype="float")
        )
        self.type = "sample"

        self._last_sample_time = 0

    def transform(self):

        rate = self.inputs[0].get()
        if rate <= 0:
            return
        if (time.time() - self._last_sample_time) < rate:
            return
        else:
            self._last_sample_time = time.time()
            self.outputs[0].set(self.inputs[1].get())


class FunctionSampleTrigger(FunctionNode):
    nice_title = "Sample Trigger"

    def __init__(self, args="", name="Sample Trigger"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=1, name=f"trigger", dtype="bool"),
            Channel(direction="in", value=0, name=f"in", dtype="float")
        ]
        self.outputs.append(
            Channel(direction="out", value=0, name=f"out", dtype="float")
        )
        self.type = "sample_trigger"
        self._last_value = 0

    def transform(self):
        trigger = self.inputs[0].get()
        if trigger != self._last_value and trigger:
            self.outputs[0].set(self.inputs[1].get())
        self._last_value = trigger


class FunctionTransition(FunctionNode):
    nice_title = "Transition"

    def __init__(self, args="", name="Transition"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", dtype="any", name=f"a"),
            Channel(direction="in", dtype="any", name=f"b"),
            Channel(direction="in", dtype="float", name=f"mix"),
        ]
        self.outputs.append(
            Channel(direction="out", dtype="any", name=f"z")
        )
        self.type = "transition"

    def transform(self):
        a = self.inputs[1].get()
        b = self.inputs[0].get()
        mix = clamp(self.inputs[2].get(), 0.0, 1.0)

        result = None
        if isinstance(a, (float, int)) and isinstance(b, (float, int)):
            result = (a * mix) + (b * (1.0 - mix))
        else:
            result = []
            for i, x in enumerate(a):
                r = (x * mix) + (b[i] * (1.0 - mix))
                result.append(r)

        self.outputs[0].set(result)


class FunctionDelay(FunctionNode):
    nice_title = "Delay"

    def __init__(self, args="", name="Delay"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", name=f"time", dtype="float"),
            Channel(direction="in", name=f"in", dtype="any")
        ]
        self.outputs = [
            Channel(direction="out", name=f"out", dtype="any")
        ]
        self.type = "delay"

        self._buffer = []

    def transform(self):
        n = self.get_n()
        cur_value = self.inputs[1].get()
        n_buf = len(self._buffer)
        
        if n <= 0:
            self.outputs[0].set(cur_value)
            return
        elif n_buf != n:
            if isinstance(cur_value, list):
                reset_value = [0] * len(cur_value)
            else:
                reset_value = 0

            if n_buf < n:
                self._buffer.extend([reset_value] * (n - n_buf))
            else:
                self._buffer = self._buffer[0:n]

        self._buffer.insert(0, cur_value)
        self.outputs[0].set(self._buffer.pop())

    def get_n(self):
        return int(self.inputs[0].get()*60)


class FunctionDelayBeats(FunctionDelay):
    nice_title = "Delay Beats"

    def __init__(self, args="", name="Delay (Beats)"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", name=f"beats", dtype="float"),
            Channel(direction="in", name=f"in", dtype="any")
        ]
        self.outputs = [
            Channel(direction="out", name=f"out", dtype="any")
        ]
        self.type = "delay_beats"

        self._buffer = []

    def get_n(self):
        beats = self.inputs[0].get()
        time_s = (beats / _STATE.tempo) * 60.0
        return int(time_s * 60)


class FunctionCanvas1x8(FunctionNode):
    nice_title = "Canvas 1x8"

    def __init__(self, args="", name="Canvas1x8"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, name="start", dtype="int"),
            Channel(direction="in", value=0, name="size", dtype="int"),
            Channel(direction="in", value=0, name="r", dtype="int"),
            Channel(direction="in", value=0, name="g", dtype="int"),
            Channel(direction="in", value=0, name="b", dtype="int"),
            Channel(direction="in", value=0, name="clear", dtype="bool"),
        ]
        self.outputs.extend([
            Channel(direction="out", value=0, name=f"{rgb}{n}", dtype="float")
            for n in range(8)
            for rgb in "rgb"
        ])
        self.type = "canvas1x8"

    def transform(self):
        start = self.inputs[0].get()
        size = self.inputs[1].get()
        r = self.inputs[2].get()
        g = self.inputs[3].get()
        b = self.inputs[4].get()
        clear = self.inputs[5].get()
        color = [r, g, b]        
        if clear:
            for output_channel in self.outputs:
                output_channel.set(0)

        if 0 <= start < 8 and 0 <= start+size <= 8:
            for i in range(start, start+size):
                for j in range(3):
                    self.outputs[(i*3) + (j)].set(color[j])

class FunctionPixelMover1(FunctionNode):
    nice_title = "Pixel Mover"

    def __init__(self, args="", name="PixelMover1"):
        super().__init__(args, name)
        self.inputs = [
            Channel(direction="in", value=0, name="i1", dtype="int"),
            Channel(direction="in", value=0, name="r1", dtype="int"),
            Channel(direction="in", value=0, name="g1", dtype="int"),
            Channel(direction="in", value=0, name="b1", dtype="int"),
            Channel(direction="in", value=0, name="i2", dtype="int"),
            Channel(direction="in", value=0, name="r2", dtype="int"),
            Channel(direction="in", value=0, name="g2", dtype="int"),
            Channel(direction="in", value=0, name="b2", dtype="int"),
        ]
        self.outputs.extend([
            Channel(direction="out", value=0, name=f"{rgb}{n}", dtype="float")
            for n in range(8)
            for rgb in "rgb"
        ])
        self.type = "pixelmover1"

        self._canvas = [0] * 24

    def transform(self):
        i1 = self.inputs[0].get()
        r1 = self.inputs[1].get()
        g1 = self.inputs[2].get()
        b1 = self.inputs[3].get()
        i2 = self.inputs[4].get()
        r2 = self.inputs[5].get()
        g2 = self.inputs[6].get()
        b2 = self.inputs[7].get()

        for output_channel in self.outputs:
            output_channel.set(0)
        canvas = [0] * 24
        
        if 0 <= i1 <= 7:
            canvas[i1*3 + 0] += r1 
            canvas[i1*3 + 1] += g1 
            canvas[i1*3 + 2] += b1
        if 0 <= i2 <= 7:
            canvas[i2*3 + 0] += r2 
            canvas[i2*3 + 1] += g2 
            canvas[i2*3 + 2] += b2

        for i, value in enumerate(canvas):
            self.outputs[i].set(value)


FUNCTION_TYPES = {
    "aggregator": FunctionAggregator,
    "binary_operator": FunctionBinaryOperator,
    "delay": FunctionDelay,
    "delay_beats": FunctionDelayBeats,
    "changing": FunctionChanging,
    "custom": FunctionCustomNode,
    "demux": FunctionDemux,
    "global_receiver": FunctionGlobalReceiver,
    "global_sender": FunctionGlobalSender,
    "last_changed": FunctionLastChanged,
    "multiplexer": FunctionMultiplexer,
    "passthrough": FunctionPassthrough,
    "random": FunctionRandom,
    "scale": FunctionScale,
    "sequencer": FunctionSequencer,
    "separator": FunctionSeparator,
    "sample": FunctionSample,
    "sample_trigger": FunctionSampleTrigger,
    "time_beat": FunctionTimeBeats,
    "time_s": FunctionTimeSeconds,
    "transition": FunctionTransition,
    "toggle_on_change": FunctionToggleOnChange,
    "canvas1x8": FunctionCanvas1x8,
    "pixelmover1": FunctionPixelMover1,
}


class NodeCollection:
    """Collection of nodes and their the a set of inputs and outputs"""

    def __init__(self):
        self.nodes: Node = []  # Needs to be a tree
        self.links = []


    def add_node(self, cls, arg):
        n = len(self.nodes)
        node = cls(arg)

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
        return True

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
        for link in self.links:
            if link.deleted:
                continue
            link.update()

        for node in self.nodes:
            if node.deleted:
                continue
            try:
                node.transform()
            except Exception as e:
                print(f"{e} in {node.name}")

    def serialize(self):
        data = {
            "nodes": [node.serialize() for node in self.nodes if not node.deleted],
            "links": [link.serialize() for link in self.links if not link.deleted],
        }
        return data

    def deserialize(self, data):
        for node_data in data["nodes"]:
            cls = FUNCTION_TYPES[node_data["type"]]
            node = cls(args=node_data["args"], name=node_data["name"])
            node.deserialize(node_data)
            self.nodes.append(node)

        for link_data in data["links"]:
            link = ChannelLink()
            link.deserialize(link_data)
            self.links.append(link)
        

class Point(Identifier):
    def __init__(self, x=None, y=None):
        super().__init__()
        self.x = x
        self.y = y

    def serialize(self):
        data = super().serialize()
        data.update({
            "x": self.x,
            "y": self.y,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.x = data["x"]
        self.y = data["y"]


class ChannelAutomation(Identifier):
    default_interpolation_type = {
        "bool": "previous",
        "int": "linear",
        "float": "linear",
    }
    TIME_RESOLUTION = 1/60.0
    def __init__(self, dtype="int", name="", min_value=0, max_value=1):

        super().__init__()
        self.dtype = dtype
        self.name = name
        self.length = 4 # beats
        self.points = [Point(0, min_value), Point(self.length, max_value)]
        self.interpolation = self.default_interpolation_type[self.dtype]
        self.reinterpolate()

    @property
    def values_x(self):
        return [p.x for p in self.points if not p.deleted]

    @property
    def values_y(self):
        return [p.y for p in self.points if not p.deleted]

    def value(self, beat_time):
        if self.f is None:
            v = 0
        else:
            try:
                v = self.f(beat_time % self.length)
            except Exception as e:
                logger.warning(e)
                v = 0

        if np.isnan(v):
            v = 0

        if self.dtype == "bool":
            return int(v > 0.5)
        elif self.dtype == "int":
            return int(v)
        else:
            return float(v)

    def n_points(self):
        return len(self.points)

    def add_point(self, point, replace_near=False):
        # Hack to make sure the first/lest point is never a deleted point
        for p in self.points:
            if p.deleted:
                p.x = 1e-6

        self.points.append(point)
        self.points.sort(key=lambda p: p.x)
        self.reinterpolate()

    def shift_points(self, amount):
        # TODO: Not working
        x1 = 0 - amount
        x2 = self.length - amount
        new_first_point = Point(x1, self.value(x1))
        new_last_point = Point(x2, self.value(x2))
        
        self.add_point(new_first_point)
        self.add_point(new_last_point)

        for point in self.points:
            point.x += amount

        p = self.points[0]
        while p.x < 0:
            self.points.pop(0)
            self.points.append(p)
            p.x = p.x % self.length
            p = self.points[0]

        p = self.points[-1]
        while p.x > self.length:
            self.points.pop(-1)
            self.points.insert(0, p)
            p.x = p.x % self.length
            p = self.points[-1]

        self.reinterpolate()

    def set_interpolation(self, kind):
        self.interpolation = kind
        self.reinterpolate()

    def reinterpolate(self):
        self.f = scipy.interpolate.interp1d(
            self.values_x, 
            self.values_y, 
            kind=self.interpolation, 
            assume_sorted=False,
            bounds_error=False,
        )

    def set_length(self, new_length):
        if new_length > self.length:
            self.add_point(Point(new_length, self.points[-1].y))
        else:
            for point in self.points:
                if point.deleted:
                    continue
                if point.x > new_length:
                    point.deleted = True
            self.add_point(Point(new_length, self.value(new_length)))
        self.length = new_length
        self.reinterpolate()

    def clear(self):
        # TODO: This is dangerous since some of the code assume there are always at least two points
        self.points.clear()

    def serialize(self):
        data = super().serialize()
        data.update({
            "length": self.length,
            "points": [point.serialize() for point in self.points if not point.deleted],
            "dtype": self.dtype,
            "name": self.name,
            "interpolation": self.interpolation,
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.dtype = data["dtype"]
        self.points = []
        for point_data in data["points"]:
            point = Point()
            point.deserialize(point_data)
            self.points.append(point)
        self.length = data["length"]
        self.name = data["name"]
        self.set_interpolation(data["interpolation"])


class ClipPreset(Identifier):
    def __init__(self, name=None, presets=None):
        super().__init__()
        self.name = name
        self.presets = presets or []
        self.speeds = []
        for channel, automation in self.presets:
            self.speeds.append(channel.speed)

    def execute(self):
        for i, preset in enumerate(self.presets):
            channel, automation = preset
            channel.set_active_automation(automation)
            channel.speed = self.speeds[i]

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "presets": [f"{channel.id}:{automation.id}" for channel, automation in self.presets],
            "speeds": self.speeds
        })
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        for preset_ids in data["presets"]:
            channel_id, automation_id = preset_ids.split(":")
            self.presets.append((UUID_DATABASE[channel_id], UUID_DATABASE[automation_id]))
        self.speeds = data["speeds"]

class Clip(Identifier):
    def __init__(self, outputs=[]):
        super().__init__()
        self.name = ""

        self.inputs = []
        self.outputs = outputs
        self.node_collection = NodeCollection()
        self.presets = []

        # Speed to play clip
        self.speed = 0

        self.time = 0

        self.playing = False


    def create_source(self, input_type):
        if input_type.startswith("osc_input"):
            input_type = input_type.replace("osc_input_", "")
            name = update_name("OSC", [obj.name for obj in self.inputs])
            new_source = OscInput(name=name, dtype=input_type)
        elif input_type == "midi":
            name = update_name("MIDI", [obj.name for obj in self.inputs])
            new_source = MidiInput(name=name)
        elif input_type == "color":
            name = update_name("Color", [obj.name for obj in self.inputs])
            new_source = ColorNode(name=name)
        elif input_type == "button":
            name = update_name("Button", [obj.name for obj in self.inputs])
            new_source = ButtonNode(name=name)
        else:
            name = update_name("Input", [obj.name for obj in self.inputs])
            new_source = AutomatableSourceNode(dtype=input_type, name=name)
        self.inputs.append(new_source)
        return new_source

    def update(self, beat):
        if self.playing:
            self.time = (beat * (2**self.speed))
            for channel in self.inputs:
                if channel.deleted:
                    continue
                channel.update(self.time)

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

    def add_preset(self, preset_name, presets):
        clip_preset = ClipPreset(preset_name, presets)
        self.presets.append(clip_preset)
        return clip_preset

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "speed": self.speed,
            "inputs": [channel.serialize() for channel in self.inputs if not channel.deleted],
            "outputs": [channel.serialize() for channel in self.outputs if not channel.deleted],
            "node_collection": self.node_collection.serialize(),
            "presets": [preset.serialize() for preset in self.presets if not preset.deleted]
        })

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.speed = data["speed"]
        self.outputs = [UUID_DATABASE[output_data["id"]] for output_data in data["outputs"]]

        for input_data in data["inputs"]:
            if input_data["input_type"] in cast.keys():
                channel = AutomatableSourceNode()
            elif input_data["input_type"].startswith("osc_input_"):
                channel = OscInput()
            elif input_data["input_type"] == "midi":
                channel = MidiInput()
            elif input_data["input_type"] == "color":
                channel = ColorNode()
            elif input_data["input_type"] == "button":
                channel = ButtonNode()
            else:
                raise RuntimeError("Failed to find node type")
            channel.deserialize(input_data)
            self.inputs.append(channel)

        self.node_collection.deserialize(data["node_collection"])

        for preset_data in data["presets"]:
            clip_preset = ClipPreset()
            clip_preset.deserialize(preset_data)
            self.presets.append(clip_preset)


class Track(Identifier):
    def __init__(self, name="", n_clips=20):
        super().__init__()
        self.name = name
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

    def create_output(self, address):
        new_output = DmxOutput(address)
        self.outputs.append(new_output)
        for clip in self.clips:
            if clip is not None:
                clip.outputs = self.outputs
        return new_output

    def create_output_group(self, address, channel_names, group_name):
        new_output_group = DmxOutputGroup(channel_names, address, name=group_name)
        self.outputs.append(new_output_group)
        for clip in self.clips:
            if clip is not None:
                clip.outputs = self.outputs
        return new_output_group

    def __delitem__(self, key):
        self.clips[key] = None

    def __getitem__(self, key):
        return self.clips[key]

    def __setitem__(self, key, value):
        self.clips[key] = value

    def __len__(self):
        return len(self.clips)

    def serialize(self):
        data = super().serialize()
        data.update({
            "name": self.name,
            "clips": [clip.serialize() if clip else None for clip in self.clips],
            "outputs": [],
        })

        for output in self.outputs:
            if output.deleted:
                continue
            output_type = "single" if isinstance(output, DmxOutput) else "group"
            data["outputs"].append((output_type, output.serialize()))

        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        n_clips = len(data["clips"])
        self.clips = [None] * n_clips

        for output_type, output_data in data["outputs"]:
            if output_type == "single":
                output = DmxOutput()
            elif output_type == "group":
                output = DmxOutputGroup(output_data["channel_names"])
            output.deserialize(output_data)
            self.outputs.append(output)

        for i, clip_data in enumerate(data["clips"]):
            if clip_data is None:
                continue
            new_clip = Clip()
            new_clip.deserialize(clip_data)
            self.clips[i] = new_clip


class IO:
    type = None
    def __init__(self, args):
        self.args = args
        self.last_io_time = 0
        logger.debug("Created %s(%s)", self.type, self.args)


    def update(self, outputs):
        raise NotImplemented

    def serialize(self):
        return {"type": self.type, "args": self.args}

    def deserialize(self, data):
        pass

    def connect(self):
        pass

    def connected(self):
        raise NotImplemented

    def update_io_time(self):
        self.last_io_time = time.time()


class EthernetDmxOutput(IO):
    nice_title = "Ethernet DMX"
    arg_template = "host:port"
    type = "ethernet_dmx"

    def __init__(self, args):
        super().__init__(args)
        self.host, self.port = args.split(":")
        self.port = int(self.port)
        self.dmx_connection = None
        self.dmx_frame = [0] * 512
        self.connect()

    def update(self, outputs):
        for output_channel in outputs:
            if output_channel.deleted:
                continue

            channels = []
            if isinstance(output_channel, DmxOutputGroup):
                channels.extend(output_channel.outputs)
            else:
                channels = [output_channel]

            for channel in channels:
                self.dmx_frame[channel.dmx_address-1] = min(255, max(0, int(round(channel.get()))))

        try:
            self.dmx_connection.set_channels(1, self.dmx_frame)
            self.dmx_connection.render()
            self.update_io_time()
        except Exception as e:
            logger.warning(e)


    def connect(self):
        try:
            self.dmx_connection = dmxio.DmxConnection((self.host, self.port))
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.dmx_connection is not None and self.dmx_connection.connected()


class NodeDmxClientOutput(IO):
    nice_title = "Node DMX Client"
    arg_template = "host:port"
    type = "node_dmx_client"

    def __init__(self, args):
        super().__init__(args)
        self.host, self.port = args.split(":")
        self.port = int(self.port)
        self.dmx_client = None
        self.dmx_frame = [0] * 512
        self.connect()

    def update(self, outputs):
        for output_channel in outputs:
            if output_channel.deleted:
                continue

            channels = []
            if isinstance(output_channel, DmxOutputGroup):
                channels.extend(output_channel.outputs)
            else:
                channels = [output_channel]

            for channel in channels:
                self.dmx_frame[channel.dmx_address-1] = min(255, max(0, int(round(channel.get()))))

        try:
            self.dmx_client.set_channels(1, self.dmx_frame)
            self.dmx_client.send_frame()
            self.update_io_time()
        except Exception as e:
            raise e

    def connect(self):
        try:
            self.dmx_client = dmxio.NodeDmxClient((self.host, self.port), 1, 512)
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.dmx_client is not None and self.dmx_client.connected()


class OscServerInput(IO):
    nice_title = "OSC Server"
    arg_template = "port"
    type = "osc_server"

    def __init__(self, args):
        super().__init__(args)
        self.port = int(args)
        self.host = "127.0.0.1"
        self.dispatcher = None
        self.server = None
        self.thread = None
        self.connect()

    def map_channel(self, endpoint, input_channel):
        def func(endpoint, value):
            input_channel.ext_set(value)
            self.update_io_time()

        self.dispatcher.map(endpoint, func)

    def umap(self, endpoint):
        self.dispatcher.umap(endpoint, lambda endpoint, *args: print(f"Unmapped {endpoint} {args}"))

    def update(self, outputs):
        pass

    def __str__(self):
        return f"OscServer"

    def connect(self):
        try:
            self.dispatcher = Dispatcher()
            self.server = osc_server.ThreadingOSCUDPServer((self.host, self.port), self.dispatcher)

            def start_osc_listening_server():
                print("OSCServer started on {}".format(self.server.server_address))
                self.server.serve_forever()
                print("OSC Server Stopped")

            self.thread = threading.Thread(target=start_osc_listening_server)
            self.thread.daemon = True
            self.thread.start()
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.thread is not None and self.thread.is_alive()


PITCH_FAKE_CONTROL = -1

def midi_value(msg):
    if msg.type == "control_change":
        value = msg.value
        note_control = msg.control
    elif msg.type in ["note_on", "note_off"]:
        value =  msg.velocity
        note_control = msg.note
    elif msg.type == "pitchwheel":
        value =  msg.pitch
        note_control = PITCH_FAKE_CONTROL

    return note_control, value

class MidiInputDevice(IO):
    nice_title = "MIDI (Input)"
    arg_template = "name"
    type = "midi_input"

    def __init__(self, args):
        super().__init__(args)
        self.device_name = args
        self.port = None
        self.channel_map = defaultdict(lambda: defaultdict(list))
        self.connect()

    def map_channel(self, midi_channel, note_control, channel):
        global_unmap_midi(channel)
        self.channel_map[midi_channel][note_control].append(channel)

    def unmap_channel(self, channel):
        for midi_channel, note_controls in self.channel_map.items():
            for note_control, channels in note_controls.items():
                for other_channel in channels:
                    if channel == other_channel:
                        self.channel_map[midi_channel][note_control].remove(channel)
                        break

    def callback(self, message):
        global LAST_MIDI_MESSAGE
        LAST_MIDI_MESSAGE = (self.device_name, message)
        midi_channel = message.channel
        note_control, value = midi_value(message)
        if midi_channel in self.channel_map and note_control in self.channel_map[midi_channel]:
            for channel in self.channel_map[midi_channel][note_control]:
                channel.ext_set(value)
        self.update_io_time()

    def serialize(self):
        data = super().serialize()
        data["channel_map"] = {}
        for midi_channel, note_controls in self.channel_map.items():
            data["channel_map"][midi_channel] = {}
            for note_control, channels in note_controls.items():
                data["channel_map"][midi_channel][note_control] = [channel.id for channel in channels]
        return data

    def deserialize(self, data):
        super().deserialize(data)
        for midi_channel, note_controls in data["channel_map"].items():
            for note_control, channels in note_controls.items():
                for channel_id in channels:
                    self.map_channel(int(midi_channel), int(note_control), UUID_DATABASE[channel_id])

    def connect(self):
        try:
            self.port = mido.open_input(self.device_name, callback=self.callback)
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.port is not None and not self.port.closed


class MidiOutputDevice(IO):
    nice_title = "MIDI (Output)"
    arg_template = "name"
    type = "midi_output"

    def __init__(self, args):
        super().__init__(args)
        self.device_name = args
        self.port = None
        self.channel_map = {}
        self.connect()

    def update(self, _):
        if self.port is None:
            return

        for (midi_channel, note_control), channel in self.channel_map.items():
            value = channel.get()
            value = clamp(int(value), 0, 127)
            self.port.send(mido.Message("note_on", channel=midi_channel, note=note_control, velocity=value))

        self.update_io_time()

    def map_channel(self, midi_channel, note_control, channel):
        self.channel_map[(midi_channel, note_control)] = channel

    def unmap_channel(self, channel):
        for (midi_channel, note_control), other_channel in self.channel_map.items():
            if channel == other_channel:
                del self.channel_map[(midi_channel, note_control)]
                self.port.send(mido.Message("note_off", channel=midi_channel, note=note_control, velocity=0))
                break

    def serialize(self):
        data = super().serialize()
        data["channel_map"] = {
            f"{midi_channel}:{note_control}": channel.id
            for (midi_channel, note_control), channel in self.channel_map.items()
        }
        return data

    def deserialize(self, data):
        super().deserialize(data)
        for key, channel_id in data["channel_map"].items():
            midi_channel, note_control = key.split(":")
            self.map_channel(int(midi_channel), int(note_control), UUID_DATABASE[channel_id])

    def connect(self):
        try:
            self.port = mido.open_output(self.device_name)
            self.port.reset()
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.port is not None and not self.port.closed

N_TRACKS = 6

OSC_SERVER_INDEX = 0
def global_osc_server():
    return _STATE.io_inputs[OSC_SERVER_INDEX]

LAST_MIDI_MESSAGE = None
MIDI_INPUT_DEVICES = {}
MIDI_OUTPUT_DEVICES = {}
def global_midi_control(device_name, in_out):
    if in_out == "in":
        return MIDI_INPUT_DEVICES.get(device_name)
    else:
        return MIDI_OUTPUT_DEVICES.get(device_name)        

def global_unmap_midi(obj):
    for midi_device in MIDI_INPUT_DEVICES.values():
        midi_device.unmap_channel(obj)
    for midi_device in MIDI_OUTPUT_DEVICES.values():
        midi_device.unmap_channel(obj)


class ProgramState(Identifier):
    _attrs_to_dump = [
        "project_name",
        "project_filepath",
        "tempo",
    ]
    
    def __init__(self):
        global _STATE
        _STATE = self
        super().__init__()
        self.mode = "edit"
        self.project_name = "Untitled"
        self.project_filepath = None
        self.tracks = []

        for i in range(N_TRACKS):
            self.tracks.append(Track(f"Track {i}"))

        self.io_outputs = [None] * 5
        self.io_inputs = [None] * 5 

        self.global_vars = {}

        self.playing = False
        self.tempo = 120.0
        self.play_time_start_s = 0
        self.time_since_start_beat = 0
        self.time_since_start_s = 0

    def toggle_play(self):
        if self.playing:
            self.playing = False
        else:
            self.playing = True
            self.play_time_start_s = time.time()

    def update(self):
        # Update timing
        if self.playing:
            self.time_since_start_s = time.time() - self.play_time_start_s
            self.time_since_start_beat = self.time_since_start_s * (1.0/60.0) * self.tempo

            # Update values
            for track in self.tracks:
                track.update(self.time_since_start_beat)

            # Update DMX outputs
            all_track_outputs = []
            for track in self.tracks:
                all_track_outputs.extend(track.outputs)
            for io_output in self.io_outputs:
                if io_output is not None:
                    io_output.update(all_track_outputs)

    def serialize(self):
        data = {
            "tempo": self.tempo,
            "project_name": self.project_name,
            "project_filepath": self.project_filepath,
            "tracks": [track.serialize() for track in self.tracks],
            "io_inputs": [None if device is None else device.serialize() for device in self.io_inputs],
            "io_outputs": [None if device is None else device.serialize() for device in self.io_outputs],
        }

        return data

    def deserialize(self, data):
        self.tempo = data["tempo"]
        self.project_name = data["project_name"]
        self.project_filepath = data["project_filepath"]

        for i, track_data in enumerate(data["tracks"]):
            new_track = Track()
            new_track.deserialize(track_data)
            self.tracks[i] = new_track

        for i, device_data in enumerate(data["io_inputs"]):
            if device_data is None:
                continue
            device = IO_TYPES[device_data["type"]](device_data["args"])
            device.deserialize(device_data)
            self.io_inputs[i] = device
            if isinstance(device, MidiInputDevice):
                MIDI_INPUT_DEVICES[device.device_name] = device

        for i, device_data in enumerate(data["io_outputs"]):
            if device_data is None:
                continue
            device = IO_TYPES[device_data["type"]](device_data["args"])
            device.deserialize(device_data)
            self.io_outputs[i] = device
            if isinstance(device, MidiOutputDevice):
                MIDI_OUTPUT_DEVICES[device.device_name] = device

    def duplicate_obj(self, obj):
        data = obj.serialize()
        new_data = new_ids(data)
        new_obj = obj.__class__()
        new_obj.deserialize(new_data)
        return new_obj

    def execute(self, full_command):
        global MIDI_INPUT_DEVICES
        global MIDI_OUTPUT_DEVICES

        allowed_performance_commands = [
            "set_active_automation",
            "toggle_clip",
            "play_clip",
            "update_parameter"
        ]

        toks = full_command.split()
        cmd = toks[0]
        
        if cmd not in ["update_automation_point"]:
            logger.info(full_command)

        if self.mode == "performance":
            if cmd not in allowed_performance_commands:
                return Result(False)

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
            return Result(True)

        if cmd == "play_clip":
            track_id = toks[1]
            clip_id = toks[2]
            track = self.get_obj(track_id)
            clip = self.get_obj(clip_id)
            for other_clip in track.clips:
                if other_clip is None or clip == other_clip:
                    continue
                other_clip.stop()
            clip.start()
            self.playing = True
            return Result(True)

        elif cmd == "new_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            track[clip_i] = Clip(track.outputs)
            return Result(True, track[clip_i])

        elif cmd == "create_source":
            clip_id = toks[1]
            input_type = toks[2]
            clip = self.get_obj(clip_id)
            new_input_channel = clip.create_source(input_type)
            return Result(True, new_input_channel)

        elif cmd == "create_output":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            new_output_channel = track.create_output(address)
            return Result(True, new_output_channel)

        elif cmd == "create_output_group":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            group_name = toks[3]
            channel_names = full_command.split(" ", 4)[-1].split(',')
            new_output_group = track.create_output_group(address, channel_names, group_name)
            return Result(True, new_output_group)

        elif cmd == "create_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]
            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return Result(clip.node_collection.add_link(src_channel, dst_channel))

        elif cmd == "delete_node":
            # TODO: Not using clip 
            clip_id = toks[1]
            obj_id = toks[2]
            obj = self.get_obj(obj_id)
            obj.deleted = True
            return Result(True)

        elif cmd == "delete_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]

            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return Result(clip.node_collection.del_link(src_channel, dst_channel))

        elif cmd == "delete_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            clip = track[clip_i]
            clip.deleted = True
            del track[clip_i]
            return Result(True)

        elif cmd == "create_node":
            # resplit
            toks = full_command.split(" ", 3)
            clip_id = toks[1]
            type_id = toks[2]
            args = toks[3] or None

            clip = self.get_obj(clip_id)

            if type_id == "none":
                node = clip.node_collection.add_node(None, None)
            else:
                node = clip.node_collection.add_node(FUNCTION_TYPES[type_id], args)
            return Result(True, node)

        elif cmd == "delete":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            if obj.deleted:
                return Result(False)
            obj.deleted = True
            return Result(True)

        elif cmd == "set_active_automation":
            input_id = toks[1]
            automation_id = toks[2]
            input_channel = self.get_obj(input_id)
            automation = self.get_obj(automation_id)
            input_channel.set_active_automation(automation)
            return Result(True) 

        elif cmd == "add_automation":
            input_id = toks[1]
            input_channel = self.get_obj(input_id)
            return Result(True, input_channel.add_automation())

        elif cmd == "add_clip_preset":
            clip_id = toks[1]
            preset_ids = toks[2].split(",")
            preset_name = " ".join(toks[3:])
            clip = self.get_obj(clip_id)

            presets = []
            for preset_id in preset_ids:
                channel_id, automation_id = preset_id.split(":")
                presets.append((self.get_obj(channel_id), self.get_obj(automation_id)))
            preset = clip.add_preset(preset_name, presets)
            return Result(True, preset)

        elif cmd == "add_automation_point":
            automation_id = toks[1]
            values = [float(x) for x in toks[2].split(",")]
            automation = self.get_obj(automation_id)
            automation.add_point(Point(*values))
            return Result(True)

        elif cmd == "update_automation_point":
            automation_id = toks[1]
            point_id = toks[2]
            values = [float(x) for x in toks[3].split(",")]
            automation = self.get_obj(automation_id)
            point = self.get_obj(point_id)
            point.x = values[0]
            point.y = values[1]
            automation.reinterpolate()
            return Result(True)

        elif cmd == "update_parameter":
            # resplit
            toks = full_command.split(" ", 3)
            obj_id = toks[1]
            param_i = toks[2]
            if len(toks) <= 3:
                return
            value = toks[3]
            node = self.get_obj(obj_id)
            result = node.update_parameter(int(param_i), value)
            if isinstance(result, tuple):
                return Result(result[0], result[1])
            else:
                return Result(result)

        elif cmd == "update_channel_value":
            input_id = toks[1]
            value = " ".join(toks[2:])
            try:
                value = eval(value)
            except:
                print(f"Failed to evaluate {value}")
                return Result(False)
            input_channel = self.get_obj(input_id)
            value = cast[input_channel.dtype](value)
            input_channel.set(value)
            return Result(True)

        elif cmd == "delete_automation_point":
            automation_id = toks[1]
            point_id = toks[2]
            automation = self.get_obj(automation_id)
            point = self.get_obj(point_id)
            point.deleted = True
            automation.reinterpolate()
            return Result(True)

        elif cmd == "create_io":
            index = int(toks[1])
            input_output = toks[2]
            io_type = toks[3]
            args = toks[4::]
            args = " ".join(args)
            IO_LIST = self.io_outputs if input_output == "outputs" else self.io_inputs
            MIDI_LIST = MIDI_OUTPUT_DEVICES if input_output == "outputs" else MIDI_INPUT_DEVICES
            try:
                if io_type == "ethernet_dmx":
                    IO_LIST[index] = EthernetDmxOutput(args)
                    return Result(True, IO_LIST[index])
                elif io_type == "node_dmx_client":
                    IO_LIST[index] = NodeDmxClientOutput(args)
                    return Result(True, IO_LIST[index])
                elif io_type == "osc_server":
                    # TODO: Only allow one
                    IO_LIST[index] = OscServerInput(args)
                    return Result(True, IO_LIST[index])
                elif io_type == "midi_input":
                    IO_LIST[index] = MidiInputDevice(args)
                    MIDI_LIST[args] = IO_LIST[index]
                    return Result(True, IO_LIST[index])
                elif io_type == "midi_output":
                    IO_LIST[index] = MidiOutputDevice(args)
                    MIDI_LIST[args] = IO_LIST[index]
                    return Result(True, IO_LIST[index])
            except Exception as e:
                print(e)
                return Result(False, None)

        elif cmd == "connect_io":
            index = int(toks[1])
            input_output = toks[2]
            IO_LIST = self.io_outputs if input_output == "outputs" else self.io_inputs
            io = IO_LIST[index]
            io.connect()
            return Result(True, io)

        elif cmd == "duplicate_clip":
            new_track_i = int(toks[1])
            new_clip_i = int(toks[2])
            clip_id = toks[3]

            new_track = self.tracks[int(new_track_i)]
            new_track_ptr = f"*track[{new_track_i}]"
            old_clip = self.get_obj(clip_id)
            new_clip = self.duplicate_obj(old_clip)
            new_track[new_clip_i] = new_clip
            return Result(True, new_clip)

        elif cmd == "duplicate_node":
            clip_id = toks[1]
            obj_id = toks[2]
            clip = self.get_obj(clip_id)
            obj = self.get_obj(obj_id)
            new_obj = self.duplicate_obj(obj)
            collection = clip.node_collection.nodes if isinstance(new_obj, FunctionNode) else clip.inputs
            collection.append(new_obj)
            new_obj.name = update_name(new_obj.name, [obj.name for obj in collection])
            return Result(True, new_obj)

        elif cmd == "double_automation":
            automation_id = toks[1]
            automation = self.get_obj(automation_id)
            old_length = automation.length
            automation.length = (old_length * 2)
            for point in tuple(automation.points):
                if not point.deleted:
                    automation.add_point(Point(point.x+old_length, point.y))
            return Result(True)

        elif cmd == "duplicate_preset":
            input_id = toks[1]
            automation_id = toks[2]
            input_channel = self.get_obj(input_id)
            automation = self.get_obj(automation_id)
            new_automation = self.duplicate_obj(automation)
            input_channel.add_automation(new_automation)
            return Result(True, new_automation)

        elif cmd == "midi_map":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            dp = obj.get_parameter("device")
            device_name = obj.get_parameter("device").value
            id_ = obj.get_parameter("id").value
            midi_channel, note_control = id_.split("/")
            global_midi_control(device_name, "in").map_channel(int(midi_channel), int(note_control), obj)
            return Result(True)

        elif cmd == "unmap_midi":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            global_unmap_midi(obj)
            return Result(True)

    def _map_all_midi_inputs(self):
        for track in self.tracks:
            for clip in track.clips:
                if clip is None:
                    continue
                for input_channel in clip.inputs:
                    if input_channel.deleted:
                        continue
                    if isinstance(input_channel, MidiInput):
                        if not input_channel.get_parameter("device").value or input_channel.get_parameter("id").value == "/":
                            continue
                        self.execute(f"midi_map {input_channel.id}")

    def get_obj(self, id_):
        try:
            return UUID_DATABASE[id_]
        except Exception as e:
            print(UUID_DATABASE)
            raise

class Result:
    """Command result."""

    def __init__(self, success, payload=None):
        self.success = success
        self.payload = payload

IO_TYPES = {
    "ethernet_dmx": EthernetDmxOutput,
    "node_dmx_client": NodeDmxClientOutput,
    "osc_server": OscServerInput,
    "midi_input": MidiInputDevice,
    "midi_output": MidiOutputDevice,
}

ALL_INPUT_TYPES = [
    OscServerInput,
    MidiInputDevice,
]
ALL_OUTPUT_TYPES = [
    EthernetDmxOutput,
    NodeDmxClientOutput,
    MidiOutputDevice,
]

_STATE = None