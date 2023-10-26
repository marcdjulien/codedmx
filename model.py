from collections import defaultdict
import re
from threading import Lock
import scipy
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

# For Custom Fuction Nodes
import colorsys

def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)

MAX_VALUES = {
    "bool": 1,
    "int": 255,
    "float": 100.0,
}

TYPES = ["bool", "int", "float", "array", "any"]

UUID_DATABASE = {}
id_count = 0

def update_name(name, other_names):
    def toks(obj_name):
        match = re.fullmatch(r"(\D*)(\d*)$", obj_name)
        if match:
            prefix, number = match.groups()
            if not number:
                number = 1
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


cast = {
    "bool": int,
    "int": int,
    "float": float,
    "any": lambda x: x
}
class Channel(Identifier):
    def __init__(self, direction="in", value=None, dtype="float", name=None, size=1):
        super().__init__()
        if value is None:
            value = 0 if size == 1 else[0]*size

        self.value = value
        self.size = size
        self.direction = direction
        self.dtype = dtype
        self.name = name

    def get(self):
        return cast[self.dtype](self.value)

    def set(self, value):
        self.value = value


class Parameter(Identifier):
    def __init__(self, name, value=None):
        super().__init__()
        self.name = name
        self.value = value

    def __str__(self):
        return f"Parameter({self.name}, {self.value})"


class Parameterized:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parameters = []

    def update_parameter(self, index, value):
        if 0 <= index < len(self.parameters):
            return True, None
        else:
            return False, None

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

    def serialize_parameters(self, ptr):
        data = []
        for param_i, parameter in enumerate(self.parameters):
            data.append(f"execute update_parameter {ptr} {param_i} {parameter.value}")
        return data


class ClipInputChannel(Parameterized, Channel):
    nice_title = "Input"

    def __init__(self, direction="in", value=0, dtype="float", name=None):
        self.input_type = dtype
        super().__init__(direction, value, dtype, name)
        self.automations = []
        self.active_automation = None
        self.active_automation_i = None
        self.mode = "automation"
        self.min_parameter = Parameter("min", 0)
        self.max_parameter = Parameter("max",  MAX_VALUES[self.dtype])
        self.add_parameter(self.min_parameter)
        self.add_parameter(self.max_parameter)
        self.ext_value = self.value
        self.speed = 0
        self.last_beat = 0

    def update(self, clip_beat):
        beat = (clip_beat * (2**self.speed))
        current_beat = beat % self.active_automation.length
        restarted = current_beat < self.last_beat

        if self.mode == "armed":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.set(value)
            if restarted:
                self.mode = "recording"
        elif self.mode == "recording":
            if restarted:
                self.mode = "automation"
            point = (current_beat, self.ext_get())
            self.active_automation.add_point(point, replace_near=True)
            self.set(self.ext_get())
        elif self.mode == "automation":
            if self.active_automation is not None:
                value = self.active_automation.value(current_beat)
                self.set(value)
        else: # manual
            self.set(self.ext_get())

        self.last_beat = current_beat

    def ext_get(self):
        return cast[self.dtype](self.ext_value)

    def ext_set(self, value):
        self.ext_value = value

    def set(self, value):
        value = max(self.min_parameter.value, value)
        value = min(self.max_parameter.value, value)
        super().set(value)

    def set_active_automation(self, automation):
        assert automation in self.automations
        self.active_automation = automation
        self.active_automation_i = self.automations.index(automation)
        return True

    def add_automation(self, clear=False):
        n = len(self.automations)
        new_automation = ChannelAutomation(
            self.dtype, 
            f"Preset #{n}", 
            min_value=self.get_parameter("min").value, 
            max_value=self.get_parameter("max").value, 
            clear=clear
        )
        self.automations.append(new_automation)
        self.set_active_automation(new_automation)
        return new_automation

    def remove_automation(self, index):
        self.automations[index].delete = True
        return True

    def update_parameter(self, index, value):
        if self.parameters[index] in [self.min_parameter, self.max_parameter]:
            self.parameters[index].value = cast[self.dtype](value)
            min_value = self.min_parameter.value
            max_value = self.max_parameter.value
            for automation in self.automations:
                if automation.deleted:
                    continue
                for i, x in enumerate(automation.values_x):
                    if x is None:
                        continue
                    y = automation.values_y[i]
                    automation.update_point(i, (x, clamp(y, min_value, max_value)))
            return True, None
        else:
            return super().update_parameter(index, value)

    def serialize(self, clip_ptr, i, id_to_ptr=None):
        if i is None:
            input_ptr = f"{clip_ptr}.in[{{input_i}}]"
        else:
            input_ptr = f"{clip_ptr}.in[{i}]"
            if id_to_ptr is not None:
                id_to_ptr[self.id] = input_ptr

        data = []
        dtype = "none" if self.deleted else self.dtype
        data.append(f"execute create_input {clip_ptr} {self.input_type}")

        if self.deleted:
            data.append(f"execute delete {input_ptr}")
        else:
            data.append(f"execute update_channel_value {input_ptr} {self.get()}")
            data.append(f"{input_ptr}.name:{repr(self.name)}")
            data.append(f"{input_ptr}.mode:{repr(self.mode)}")
            data.extend(self.serialize_parameters(input_ptr))
            for auto_i, automation in enumerate(self.automations):
                data.extend(automation.serialize(input_ptr, auto_i))
        return data


class DmxOutput(Channel):
    def __init__(self, dmx_address=1, name=""):
        super().__init__(direction="in", dtype="int", name=name or f"DMX CH. {dmx_address}")
        self.dmx_address = dmx_address
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

        data.append(f"execute create_output {track_ptr} {self.dmx_address}")
        if self.deleted:
            data.append(f"execute delete {output_ptr}")
        else:
            data.append(f"{output_ptr}.name:{repr(self.name)}")

        return data


class DmxOutputGroup(Identifier):

    def __init__(self, channel_names, dmx_address=1, name="Group"):
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

    def serialize(self, track_ptr, i, id_to_ptr):
        if i is None:
            output_ptr = f"{track_ptr}.group[{{output_i}}]"
        else:
            output_ptr = f"{track_ptr}.group[{i}]"
            if id_to_ptr is not None:
                id_to_ptr[self.id] = output_ptr

        data = []

        data.append(f"execute create_output_group {track_ptr} {self.dmx_address}", {','.join(self.channel_names)})
        if self.deleted:
            data.append(f"execute delete {output_ptr}")
        else:
            data.append(f"{output_ptr}.name:{repr(self.name)}")

        return data


class OscInput(ClipInputChannel):
    def __init__(self, dtype):
        super().__init__(direction="out", dtype=dtype, name=f"OSC[{dtype}]")
        self.endpoint_parameter = Parameter("endpoint", value="/")
        self.add_parameter(self.endpoint_parameter)
        self.input_type = "osc_input_"+self.dtype

    def update_parameter(self, index, value):
        if self.parameters[index] == self.endpoint_parameter:
            if value.startswith("/"):
                self.parameters[index].value = value
                global_osc_server().map_channel(value, self)
            return True, None
        else:
            return super().update_parameter(index, value)


class MidiInput(ClipInputChannel):
    def __init__(self):
        super().__init__(direction="out", dtype="int", name=f"MIDI")
        self.device_parameter = Parameter("device", value="")
        self.id_parameter = Parameter("id", value="/")
        self.add_parameter(self.device_parameter)
        self.add_parameter(self.id_parameter)
        self.input_type = "midi"

    def update_parameter(self, index, value):
        if self.parameters[index] == self.device_parameter:
            if not value:
                return False, None
            self.parameters[index].value = value
            return True, None
        elif self.parameters[index] == self.id_parameter:
            if self.device_parameter.value is None:
                return False, None
            result = value.split("/")
            if not len(result) == 2 and result[0] and result[1]:
                return False, None
            self.parameters[index].value = value
            return True, None
        else:
            return super().update_parameter(index, value)


class ChannelLink(Identifier):
    def __init__(self, src_channel, dst_channel):
        super().__init__()
        self.src_channel = src_channel
        self.dst_channel = dst_channel

    def update(self):
        self.dst_channel.set(self.src_channel.get())


class FunctionNode(Parameterized, Identifier):

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.inputs: Channel = []
        self.outputs: Channel = []
        self.type = None
        self.args = None

    def transform(self):
        raise NotImplemented

    def outputs(self):
        return self.outputs

    def serialize(self, clip_ptr, i, id_to_ptr):
        if i is None:
            node_ptr = f"{clip_ptr}.node[{{node_i}}]"
        else:
            node_ptr = f"{clip_ptr}.node[{i}]"

        data = []
        node_type = "none" if self.deleted else self.type
        args = "," if self.deleted else (self.args or ",")
        data.append(f"execute create_node {clip_ptr} {node_type} {args}")
        if not self.deleted:
            data.append(f"{node_ptr}.name:{repr(self.name)}")
            data.extend(self.serialize_parameters(node_ptr))
            for input_i, input_channel in enumerate(self.inputs):
                input_ptr = f"{node_ptr}.in[{input_i}]"
                id_to_ptr[input_channel.id] = input_ptr
                data.append(f"execute update_channel_value {input_ptr} {input_channel.get()}")
            for output_i, output_channel in enumerate(self.outputs):
                output_ptr = f"{node_ptr}.out[{output_i}]"
                id_to_ptr[output_channel.id] = output_ptr

        return data

class FunctionDeleted(FunctionNode):
    nice_title = "Deleted"

    def __init__(self, name):
        super().__init__(name)
        self.deleted = True

class FunctionCustomNode(FunctionNode):
    nice_title = "Custom"

    def __init__(self, args, name="Custom"):
        super().__init__(name)
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
                return False, None

            delta = n - len(self.inputs)
            if n < len(self.inputs):
                for i in range(n, len(self.inputs)):
                    channels.append(self.inputs.pop(-1))
            elif n > len(self.inputs):
                for _ in range(delta):
                    i = len(self.inputs)
                    new_channel = Channel("in", dtype="any", name=f"i[{i}]")
                    self.inputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[0].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.n_out_parameter:
            n = int(value)
            if n < 0:
                return False, None
                
            delta = n - len(self.outputs)
            if n < len(self.outputs):
                for i in range(n, len(self.outputs)):
                    channels.append(self.outputs.pop(-1))
            elif n > len(self.outputs):
                for _ in range(delta):
                    i = len(self.outputs)
                    new_channel = Channel("out", dtype="any", name=f"o[{i}]")
                    self.outputs.append(new_channel)
                    channels.append(new_channel)
            self.parameters[1].value = value
            return True, (delta, channels)
        elif self.parameters[index] == self.code_parameter:
            self.parameters[2].value = value.replace("\n", "[NEWLINE]")
            return True, None
        else:
            return super().update_parameter(index, value)


    # TODO: Update?
    def serialize(self, clip_ptr, i, id_to_ptr):
        if i is None:
            node_ptr = f"{clip_ptr}.node[{{node_i}}]"
        else:
            node_ptr = f"{clip_ptr}.node[{i}]"

        data = []
        node_type = "none" if self.deleted else self.type
        args = "," if self.deleted else (self.args or ",")
        data.append(f"execute create_node {clip_ptr} {node_type} {args}")
        if not self.deleted:
            data.append(f"{node_ptr}.name:{repr(self.name)}")
            for param_i, parameter in enumerate(self.parameters):
                data.append(f"execute update_parameter {node_ptr} {param_i} {parameter.value}")
            for input_i, input_channel in enumerate(self.inputs):
                input_ptr = f"{node_ptr}.in[{input_i}]"
                id_to_ptr[input_channel.id] = input_ptr
                data.append(f"execute update_channel_value {input_ptr} {input_channel.get()}")
            for output_i, output_channel in enumerate(self.outputs):
                output_ptr = f"{node_ptr}.out[{output_i}]"
                id_to_ptr[output_channel.id] = output_ptr

        return data


class FunctionBinaryOperator(FunctionNode):
    nice_title = "Binary Operator"

    def __init__(self, name="Operator"):
        super().__init__(name)
        self.op_parameter = Parameter("op")
        self.add_parameter(self.op_parameter)
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
        if self.parameters[index] == self.op_parameter and value in ["+", "-", "/", "*"]:
            self.parameters[index].value = value

            # TODO: Add other operators
            self.f = {
                "+": operator.add,
                "-": operator.sub,
                "/": operator.truediv,
                "*": operator.mul,
            }[value]
            return True, None
        else:
            return super().update_parameter(index, value)

class FunctionScale(FunctionNode):
    nice_title = "Scale"

    def __init__(self, name="Scale"):
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
            Channel("in", 0, name=f"x"), 
        ]
        self.outputs.append(
            Channel("out", 0, name=f"y")
        )
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
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionDemux(FunctionNode):
    nice_title = "Demux"

    clear_values = {
        "bool": 0,
        "int": 0,
        "float": 0.0,
        "array": [],
    }

    def __init__(self, n, name="Demux"):
        super().__init__(name)
        self.n = n
        self.parameters = []
        self.inputs = [
            Channel("in", 0, dtype="int", name=f"sel"),
            Channel("in", dtype="any", name=f"val")
        ]
        for i in range(n):
            self.outputs.append(Channel("out", dtype="any", name=f"{i+1}"))

        self.type = "demux"
        self.args = str(n)

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

    def __init__(self, n, name="Multiplexer"):
        super().__init__(name)
        self.n = n
        self.inputs = [
            Channel("in", 1, dtype="int", name=f"sel")
        ]
        for i in range(n):
            self.inputs.append(Channel("in", dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel("out", dtype="any", name=f"out"))
        self.type = "multiplexer"
        self.args = str(n)

    def transform(self):
        selected = int(self.inputs[0].get())
        if selected in range(1, self.n+1):
            self.outputs[0].set(self.inputs[selected].get())


class FunctionPassthrough(FunctionNode):
    nice_title = "Passthrough"

    def __init__(self, name="Passthrough"):
        super().__init__(name)
        self.inputs.append(Channel("in", dtype="any", name=f"in"))
        self.outputs.append(Channel("out", dtype="any", name=f"out"))
        self.type = "passthrough"

    def transform(self):
        self.outputs[0].set(self.inputs[0].get())


class FunctionTimeSeconds(FunctionNode):
    nice_title = "Seconds"

    def __init__(self, time_func, name="Seconds"):
        super().__init__(name)
        self.outputs.append(Channel("out", dtype="float", name="s"))
        self.type = "time_s"
        self.time_func = time_func

    def transform(self):
        self.outputs[0].set(self.time_func())


class FunctionTimeBeats(FunctionNode):
    nice_title = "Beats"

    def __init__(self, time_func, name="Beats"):
        super().__init__(name)
        self.outputs.append(Channel("out", dtype="float", name="beat"))
        self.type = "time_beat"
        self.time_func = time_func

    def transform(self):
        self.outputs[0].set(self.time_func())


class FunctionChanging(FunctionNode):
    nice_title = "Changing"

    def __init__(self, name="Changing"):
        super().__init__(name)
        self.inputs.append(Channel("in", dtype="any", name=f"in"))
        self.outputs.append(Channel("out", dtype="bool", name=f"out"))
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

    def __init__(self, name="ToggleOnChange"):
        super().__init__(name)
        self.inputs.append(Channel("in", dtype="any", name=f"in"))
        self.outputs.append(Channel("out", dtype="bool", name=f"out"))
        self.type = "toggle_on_change"
        self._last_value = None
        self._toggle_value = 0

    def transform(self):
        changing = False
        new_value = self.inputs[0].get()
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            self._last_value = new_value

        if changing:
            self._toggle_value = int(not self._toggle_value)
        self.outputs[0].set(self._toggle_value)
        

class FunctionLastChanged(FunctionNode):
    nice_title = "Last Changed"

    def __init__(self, n, name="LastChanged"):
        super().__init__(name)
        self.n = n
        for i in range(n):
            self.inputs.append(Channel("in", 0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel("out", dtype="int", name=f"out{n}"))
        self.type = "last_changed"
        self.args = n
        self._last_values = [None]*n
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

    def __init__(self, n, name="Aggregator"):
        super().__init__(name)
        self.n = n
        for i in range(n):
            self.inputs.append(Channel("in", 0, dtype="any", name=f"{i+1}"))

        self.outputs.append(Channel("out", dtype="any", size=n, name=f"out{n}"))
        self.type = "aggregator"
        self.args = n

    def transform(self):
        value = [channel.get() for channel in self.inputs]
        self.outputs[0].set(value)


class FunctionSeparator(FunctionNode):
    nice_title = "Separator"

    def __init__(self, n, name="Separator"):
        super().__init__(name)
        self.n = n
        self.inputs.append(Channel("in", dtype="any", size=n, name=f"in{n}"))

        for i in range(n):
            self.outputs.append(Channel("out", dtype="any", name=f"out{n}"))
        self.type = "separator"
        self.args = n

    def transform(self):
        values = self.inputs[0].get()
        for i, value in enumerate(values):
            self.outputs[i].set(value)


class FunctionRandom(FunctionNode):
    nice_title = "Random"

    def __init__(self, name="Random"):
        super().__init__(name)
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


class FunctionSample(FunctionNode):
    nice_title = "Sample"

    def __init__(self, name="Sample"):
        super().__init__(name)
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


class FunctionBuffer(FunctionNode):
    nice_title = "Buffer"

    def __init__(self, name="Buffer"):
        super().__init__(name)
        self.n_parameter = Parameter("n", value=60)
        self.add_parameter(self.n_parameter)
        self.inputs = [
            Channel("in", name=f"in", dtype="any")
        ]
        self.outputs.append(
            Channel("out", name=f"out", dtype="any")
        )
        self.type = "buffer"

        self._buffer = []

    def transform(self):
        self._buffer.insert(0, self.inputs[0].get())
        self.outputs[0].set(self._buffer.pop())

    def update_parameter(self, index, value):
        if self.parameters[index] == self.n_parameter:
            try:
                value = int(value)
            except Exception as e:
                print(e)
                return False, None

            container_value = self.inputs[0].get()
            if isinstance(container_value, list):
                reset_value = [0] * len(container_value)
            else:
                reset_value = 0

            self._buffer = [reset_value] * value
            return True, None
        else:
            return super().update_parameter(index, value)


class FunctionCanvas1x8(FunctionNode):
    nice_title = "Canvas 1x8"

    def __init__(self, name="Canvas1x8"):
        super().__init__(name)
        self.inputs = [
            Channel("in", 0, name="start", dtype="int"),
            Channel("in", 0, name="size", dtype="int"),
            Channel("in", 0, name="r", dtype="int"),
            Channel("in", 0, name="g", dtype="int"),
            Channel("in", 0, name="b", dtype="int"),
            Channel("in", 0, name="clear", dtype="bool"),
        ]
        self.outputs.extend([
            Channel("out", 0, name=f"{rgb}{n}", dtype="float")
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

    def __init__(self, name="PixelMover1"):
        super().__init__(name)
        self.inputs = [
            Channel("in", 0, name="i1", dtype="int"),
            Channel("in", 0, name="r1", dtype="int"),
            Channel("in", 0, name="g1", dtype="int"),
            Channel("in", 0, name="b1", dtype="int"),
            Channel("in", 0, name="i2", dtype="int"),
            Channel("in", 0, name="r2", dtype="int"),
            Channel("in", 0, name="g2", dtype="int"),
            Channel("in", 0, name="b2", dtype="int"),
        ]
        self.outputs.extend([
            Channel("out", 0, name=f"{rgb}{n}", dtype="float")
            for n in range(8)
            for rgb in "rgb"
        ])
        self.type = "canvas1x8"

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


class NodeCollection:
    """Collection of nodes and their the a set of inputs and outputs"""

    def __init__(self):
        self.nodes: Node = []  # Needs to be a tree
        self.links = []


    def add_node(self, cls, arg):
        if cls is None:
            none = FunctionDeleted(name="none")
            self.nodes.append(none)
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

    def serialize(self, clip_ptr, id_to_ptr):
        data = []
        for node_i, node in enumerate(self.nodes):
            if node is None:
                continue
            data.extend(node.serialize(clip_ptr, node_i, id_to_ptr))

        for link in self.links:
            if link.deleted:
                continue
            data.append(f"execute create_link {clip_ptr} {id_to_ptr[link.src_channel.id]} {id_to_ptr[link.dst_channel.id]}")

        return data


class ChannelAutomation(Identifier):
    default_interpolation_type = {
        "bool": "previous",
        "int": "linear",
        "float": "linear",
    }
    TIME_RESOLUTION = 1/60.0
    def __init__(self, dtype, name, min_value=0, max_value=1, clear=False):

        super().__init__()
        self.length = 4 # beats
        self.values_x = [] if clear else [0, self.length]
        self.values_y = [] if clear else [min_value, max_value]
        self.f = None if clear else scipy.interpolate.interp1d(self.values_x, self.values_y)
        self.dtype = dtype
        self.name = name
        self.interpolation = self.default_interpolation_type[self.dtype]

    def value(self, beat_time):
        if self.f is None:
            v = 0
        else:
            v = self.f(beat_time % self.length)
        if self.dtype == "bool":
            return int(v > 0.5)
        elif self.dtype == "int":
            return int(v)
        else:
            return float(v)

    def n_points(self):
        return len(self.values_x)

    def add_point(self, p1, replace_near=False):
        if replace_near:
            max_x_index = self.max_x_index()
            for i, x in enumerate(self.values_x):
                if x is None:
                    continue
                if abs(x - p1[0]) >= 0.01:
                    continue

                if i in [0, max_x_index]:
                    self.values_y[i] = p1[1]
                else:
                    self.values_x[i] = None
                    self.values_y[i] = None
                    self.values_x.append(p1[0])
                    self.values_y.append(p1[1])
                break                       
            else:
                self.values_x.append(p1[0])
                self.values_y.append(p1[1])
        else:
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

    def set_interpolation(self, kind):
        self.interpolation = kind
        self.reinterpolate()

    def reinterpolate(self):
        values_x = [x for x in self.values_x if x is not None]
        values_y = [y for y in self.values_y if y is not None]
        self.f = scipy.interpolate.interp1d(
            values_x, 
            values_y, 
            kind=self.interpolation, 
            assume_sorted=False
        )

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
        if self.deleted:
            data.append(f"execute delete {auto_ptr}")
            return data
        data.append(f"{auto_ptr}.length:{repr(self.length)}")
        data.append(f"{auto_ptr}.name:{repr(self.name)}")
        data.append(f"{auto_ptr}.interpolation:{repr(self.interpolation)}")
        for point_i in range(len(self.values_x)):
            x, y = self.values_x[point_i], self.values_y[point_i]
            if x is None:
                continue
            data.append(f"execute add_automation_point {auto_ptr} {x},{y}")

        return data


class Clip(Identifier):
    def __init__(self, outputs):
        super().__init__()

        self.name = ""

        self.inputs = []
        self.outputs = outputs
        self.node_collection = NodeCollection()

        # Speed to play clip
        self.speed = 0

        self.time = 0

        self.playing = False

    def create_input(self, input_type):
        n = len(self.inputs)
        if input_type.startswith("osc_input"):
            input_type = input_type.replace("osc_input_", "")
            new_inp = OscInput(input_type)
        elif input_type == "midi":
            new_inp = MidiInput()
        else:
            new_inp = ClipInputChannel("out", dtype=input_type, name=f"In.{n}")
        self.inputs.append(new_inp)
        return new_inp

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

    def serialize(self, track_ptr, i=None, id_to_ptr=None):
        if i is None:
            clip_ptr = f"{track_ptr}.clip[{{clip_i}}]"
        else:
            clip_ptr = f"{track_ptr}.clip[{i}]"

        data = []
        data.append(f"execute new_clip {track_ptr},{i}")
        data.append(f"{clip_ptr}.name:{repr(self.name)}")
        data.append(f"{clip_ptr}.speed:{repr(self.speed)}")
        data.append(f"{clip_ptr}.playing:{repr(self.playing)}")

        for input_i, input_channel in enumerate(self.inputs):
            data.extend(input_channel.serialize(clip_ptr, input_i, id_to_ptr))

        # Outputs are serialized at the Track level, but we still need
        # to maintain the Clips individual output pointer mapping.
        # Populate id_to_ptr by calling serialize, but don't save the data.
        for output_i, output_channel in enumerate(self.outputs):
            output_channel.serialize(clip_ptr, output_i, id_to_ptr)

        data.extend(self.node_collection.serialize(clip_ptr, id_to_ptr))

        return data


class Track(Identifier):
    def __init__(self, name, n_clips=20):
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
        return new_output

    def create_output_group(self, address, channel_names):
        new_output_group = DmxOutputGroup(channel_names, address)
        self.outputs.append(new_output_group)
        return new_output_group

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


class IO:
    type = None
    def __init__(self, arg_string):
        self.arg_string = arg_string

    def update(self, outputs):
        raise NotImplemented


class EthernetDmxOutput(IO):
    nice_title = "Ethernet DMX"
    arg_template = "host:port"
    type = "ethernet_dmx"

    def __init__(self, host_port):
        super().__init__(host_port)
        self.host, self.port = host_port.split(":")
        self.port = int(self.port)
        self.dmx_connection = dmxio.DmxConnection((self.host, self.port))
        self.dmx_frame = [1] * 512

    def update(self, outputs):
        for output_channel in outputs:
            if output_channel.deleted:
                continue
            self.dmx_frame[output_channel.dmx_address-1] = min(255, max(0, int(round(output_channel.get()))))

        try:
            self.dmx_connection.set_channels(1, self.dmx_frame)
            self.dmx_connection.render()
        except Exception as e:
            raise e

    def __str__(self):
        return f"NodeDmxClient({self.host}:{self.port})"


class OscServerInput(IO):
    nice_title = "OSC Server"
    arg_template = "port"
    type = "osc_server"

    def __init__(self):
        super().__init__(arg_string="")
        self.host = "127.0.0.1"
        self.dispatcher = Dispatcher()

    def start(self, port):
        self.server = osc_server.ThreadingOSCUDPServer((self.host, port), self.dispatcher)

        def start_osc_listening_server():
            print("OSCServer started on {}".format(self.server.server_address))
            self.server.serve_forever()
            print("OSC Server Stopped")

        self.arg_string = str(port)
        self.thread = threading.Thread(target=start_osc_listening_server)
        self.thread.daemon = True
        self.thread.start()

    def map_channel(self, endpoint, input_channel):
        def func(endpoint, value):
            input_channel.ext_set(value)
        self.dispatcher.map(endpoint, func)

    def umap(self, endpoint):
        self.dispatcher.umap(endpoint, lambda endpoint, *args: print(f"Unmapped {endpoint} {args}"))

    def update(self, outputs):
        pass

    def __str__(self):
        return f"OscServer"


class MidiDevice(IO):
    nice_title = "MIDI"
    arg_template = "name"
    type = "midi"

    def __init__(self, device_name):
        super().__init__(arg_string=device_name)
        self.device_name = device_name
        self.is_input = device_name in mido.get_input_names()
        if self.is_input:
            self.port = mido.open_input(device_name, callback=self.callback)
        else:
            self.port = mido.open_output(device_name)

        self.channel_map = defaultdict(lambda: defaultdict(list))

        # TODO: Confgure via GUI
        if not self.is_input:
            self.port.reset()

    def update(self, _):
        # TODO: Make Customizable
        white = 3
        pink = 53
        orange = 60
        yellow = 96
        green = 17
        light_blue = 41
        dark_blue = 45
        red = 5
        if not self.is_input:
            out_map = [
                (0, 0, green),
                (0, 8, yellow),
                (0, 16, orange),
                (0, 24, red),
                (0, 32, white),
                (0, 33, dark_blue),
                (0, 25, light_blue),
                (0, 17, pink),

                (0, 2, green),
                (0, 10, yellow),
                (0, 18, orange),
                (0, 26, red),
                (0, 34, white),
                (0, 35, dark_blue),
                (0, 27, light_blue),
                (0, 19, pink),
            ]
            for channel, note_control, value in out_map:
                self.port.send(mido.Message("note_on", channel=channel, note=note_control, velocity=value))

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
        if message.is_cc():
            note_control = message.control
            value = message.value
        else:
            note_control = message.note
            value = 255 if message.type == "note_on" else 0
        if midi_channel in self.channel_map and note_control in self.channel_map[midi_channel]:
            for channel in self.channel_map[midi_channel][note_control]:
                channel.ext_set(value)


IO_OUTPUTS = [None] * 5
IO_INPUTS = [OscServerInput(), None, None, None, None] 
N_TRACKS = 6

OSC_SERVER_INDEX = 0
def global_osc_server():
    if OSC_SERVER_INDEX is not None:
        return IO_INPUTS[OSC_SERVER_INDEX]

LAST_MIDI_MESSAGE = None
MIDI_INPUT_DEVICES = {}
MIDI_OUTPUT_DEVICES = {}
def global_midi_control(device_name, in_out):
    if in_out == "in":
        print(MIDI_INPUT_DEVICES, device_name, in_out)
        return MIDI_INPUT_DEVICES.get(device_name)
    else:
        return MIDI_OUTPUT_DEVICES.get(device_name)        

def global_unmap_midi(obj):
    for midi_device in MIDI_INPUT_DEVICES.values():
        midi_device.unmap_channel(obj)


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

        for i in range(N_TRACKS):
            self.tracks.append(Track(f"Track {i}"))

        self._active_clip = None

        self.playing = False
        self.tempo = 120.0
        self.play_time_start_s = 0
        self.time_since_start_beat = 0
        self.time_since_start_s = 0
        self.all_track_outputs = []

    def toggle_play(self):
        if self.playing:
            self.playing = False
        else:
            self.playing = True
            self.play_time_start_s = time.time()

    def update(self):
        global IO_OUTPUTS
        global IO_INPUTS
        global OSC_SERVER_INDEX

        # Update timing
        if self.playing:
            self.time_since_start_s = time.time() - self.play_time_start_s
            self.time_since_start_beat = self.time_since_start_s * (1.0/60.0) * self.tempo

            # Update values
            for track in self.tracks:
                track.update(self.time_since_start_beat)

            # Update DMX outputs
            for io_output in IO_OUTPUTS:
                if io_output is not None:
                    io_output.update(self.all_track_outputs)

    def stop_io(self):
        pass


    def execute(self, full_command):
        global IO_OUTPUTS
        global IO_INPUTS
        global OSC_SERVER_INDEX
        global MIDI_INPUT_DEVICES
        global MIDI_OUTPUT_DEVICES
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
            track[clip_i] = Clip(track.outputs)
            return True, track[clip_i]

        elif cmd == "create_input":
            clip_id = toks[1]
            input_type = toks[2]
            clip = self.get_obj(clip_id)
            new_input_channel = clip.create_input(input_type)
            return True, new_input_channel

        elif cmd == "create_output":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            new_output_channel = track.create_output(address)
            self.all_track_outputs.append(new_output_channel)
            return True, new_output_channel

        elif cmd == "create_output_group":
            track_id = toks[1]
            track = self.get_obj(track_id)
            address = int(toks[2])
            channel_names = full_command.split(" ", 3)[-1].split(',')
            new_output_group = track.create_output_group(address, channel_names)
            self.all_track_outputs.append(new_output_group)
            return True, new_output_group

        elif cmd == "create_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]
            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return clip.node_collection.add_link(src_channel, dst_channel)

        elif cmd == "delete_link":
            clip_id = toks[1]
            src_id = toks[2]
            dst_id = toks[3]

            clip = self.get_obj(clip_id)
            src_channel = self.get_obj(src_id)
            dst_channel = self.get_obj(dst_id)
            return clip.node_collection.del_link(src_channel, dst_channel)

        elif cmd == "create_node":
            # resplit
            toks = full_command.split(" ", 3)
            clip_id = toks[1]
            type_id = toks[2]
            args = toks[3]

            clip = self.get_obj(clip_id)

            if type_id == "none":
                node = clip.node_collection.add_node(None, None)
            elif type_id == "binary_operator":
                node = clip.node_collection.add_node(FunctionBinaryOperator, None)
            elif type_id == "scale":
                node = clip.node_collection.add_node(FunctionScale, None)
            elif type_id == "demux":
                n = int(args)
                node = clip.node_collection.add_node(FunctionDemux, n)
            elif type_id == "multiplexer":
                n = int(args)
                node = clip.node_collection.add_node(FunctionMultiplexer, n)
            elif type_id == "aggregator":
                n = int(args)
                node = clip.node_collection.add_node(FunctionAggregator, n)
            elif type_id == "separator":
                n = int(args)
                node = clip.node_collection.add_node(FunctionSeparator, n)
            elif type_id == "time_s":
                time_func = lambda: self.time_since_start_s
                node = clip.node_collection.add_node(FunctionTimeSeconds, time_func)
            elif type_id == "time_beat":
                time_func = lambda: self.time_since_start_beat
                node = clip.node_collection.add_node(FunctionTimeBeats, time_func)
            elif type_id == "random":
                node = clip.node_collection.add_node(FunctionRandom, None)
            elif type_id == "passthrough":
                node = clip.node_collection.add_node(FunctionPassthrough, None)
            elif type_id == "changing":
                node = clip.node_collection.add_node(FunctionChanging, None)
            elif type_id == "toggle_on_change":
                node = clip.node_collection.add_node(FunctionToggleOnChange, None)
            elif type_id == "last_changed":
                n = int(args)
                node = clip.node_collection.add_node(FunctionLastChanged, n)
            elif type_id == "sample":
                node = clip.node_collection.add_node(FunctionSample, None)
            elif type_id == "buffer":
                node = clip.node_collection.add_node(FunctionBuffer, None)
            elif type_id == "canvas1x8":
                node = clip.node_collection.add_node(FunctionCanvas1x8, None)
            elif type_id == "pixelmover1":
                node = clip.node_collection.add_node(FunctionPixelMover1, None)
            elif type_id == "custom":
                node = clip.node_collection.add_node(FunctionCustomNode, args)
            return True, node

        elif cmd == "delete":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            if obj.deleted:
                return False
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
            # resplit
            toks = full_command.split(" ", 3)
            obj_id = toks[1]
            param_i = toks[2]
            if len(toks) <= 3:
                return
            value = toks[3]
            node = self.get_obj(obj_id)
            result = node.update_parameter(int(param_i), value)
            return result

        elif cmd == "update_channel_value":
            input_id = toks[1]
            value = " ".join(toks[2:])
            try:
                value = eval(value)
            except:
                print(f"Failed to evaluate {value}")
                return False
            input_channel = self.get_obj(input_id)
            value = cast[input_channel.dtype](value)
            input_channel.set(value)
            return True

        elif cmd == "remove_automation_point":
            src = toks[1]
            point_index = toks[2]
            input_channel = self.get_obj(src)
            automation = input_channel.active_automation
            return automation.remove_point(int(point_index))

        elif cmd == "create_io":
            index = int(toks[1])
            input_output = toks[2]
            io_type = toks[3]
            args = toks[4::]
            args = " ".join(args)
            IO_LIST = IO_OUTPUTS if input_output == "outputs" else IO_INPUTS
            MIDI_LIST = MIDI_OUTPUT_DEVICES if input_output == "outputs" else MIDI_INPUT_DEVICES
            try:
                if io_type == "ethernet_dmx":
                    IO_LIST[index] = EthernetDmxOutput(args)
                    return True, IO_LIST[index]
                elif io_type == "osc_server":
                    # TODO: Only allow one
                    IO_LIST[index].start(int(args))
                    return True, IO_LIST[index]
                elif io_type == "midi":
                    IO_LIST[index] = MidiDevice(args)
                    MIDI_LIST[args] = IO_LIST[index]
                    self._map_all_midi_inputs()
                    self._map_all_midi_inputs()
                    return True, IO_LIST[index]
            except Exception as e:
                print(e)
                return False, None

        elif cmd == "duplicate_clip":
            new_track_i = toks[1]
            new_clip_i = toks[2]
            clip_id = toks[3]

            new_track = self.tracks[int(new_track_i)]
            new_track_ptr = f"*track[{new_track_i}]"
            old_clip = self.get_obj(clip_id)

            id_to_ptr = {}
            data = old_clip.serialize(new_track_ptr, new_clip_i, id_to_ptr)
            self.deserialize(data)
            return True, new_track.clips[int(new_clip_i)]

        elif cmd == "duplicate_input":
            clip_id = toks[1]
            input_id = toks[2]
            clip = self.get_obj(clip_id)
            clip_ptr = self.get_ptr_from_clip(clip)
            input_channel = self.get_obj(input_id)
            
            id_to_ptr = {}
            new_i = len(clip.inputs)
            data = input_channel.serialize(clip_ptr, new_i, id_to_ptr)
            self.deserialize(data)
            new_input_channel = clip.inputs[int(new_i)]
            new_input_channel.name = update_name(new_input_channel.name, [obj.name for obj in clip.inputs])
            return True, new_input_channel

        elif cmd == "duplicate_node":
            clip_id = toks[1]
            node_id = toks[2]
            clip = self.get_obj(clip_id)
            clip_ptr = self.get_ptr_from_clip(clip)
            node = self.get_obj(node_id)
            
            new_i = len(clip.node_collection.nodes)
            id_to_ptr = {}
            data = node.serialize(clip_ptr, new_i, id_to_ptr)
            self.deserialize(data)
            new_node = clip.node_collection.nodes[int(new_i)]
            new_node.name = update_name(new_node.name, [obj.name for obj in clip.node_collection.nodes])
            return True, new_node, id_to_ptr

        elif cmd == "double_automation":
            automation_id = toks[1]
            automation = self.get_obj(automation_id)
            old_length = automation.length
            automation.set_length(old_length * 2)
            for i in range(automation.n_points()):
                x = automation.values_x[i]
                y = automation.values_y[i]
                if x is not None:
                    automation.add_point((x+old_length, y))
            return True
        elif cmd == "midi_map":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            dp = obj.get_parameter("device")
            device_name = obj.get_parameter("device").value
            id_ = obj.get_parameter("id").value
            midi_channel, note_control = id_.split("/")
            global_midi_control(device_name, "in").map_channel(int(midi_channel), int(note_control), obj)
            return True
        elif cmd == "unmap_midi":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            global_unmap_midi(obj)
            return True

        print("Previous command failed")

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

    def get_obj(self, id_, missing_ok=False):
        if id_.startswith("*"):
            return self.get_obj_ptr(id_[1::])
        else:
            try:
                return UUID_DATABASE[id_]
            except Exception as e:
                print(UUID_DATABASE)
                if missing_ok:
                    return
                else:
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
            print(self.tracks[int(ti)].clips[int(ci)].node_collection.nodes[int(ni)].__class__.__name__)
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

    def get_ptr_from_clip(self, clip):
        for track_i, track in enumerate(self.tracks):
            for clip_i, other_clip in enumerate(track.clips):
                if clip == other_clip:
                    return f"*track[{track_i}].clip[{clip_i}]"

    def get_clip_from_ptr(self, clip_key):
        """*track[i].clip[j].+ -> Clip"""
        match = re.match(r"track\[(\d+)\]\.clip\[(\d+)\]", clip_key[1::])
        if match:
            track_i = int(match.groups()[0])
            clip_i = int(match.groups()[1])
            return self.tracks[track_i][clip_i]
        raise RuntimeError(f"Failed to find clip for {clip_key}")

    def deserialize(self, f):
        for line in f:
            line = line.strip()
            if not line:
                continue
            print(f">>> {line}")
            toks = line.strip().split(" ", 1)
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
                save(f"execute create_output {track_ptr} {output_channel.dmx_address}")
                if output_channel.deleted:
                    save(f"execute delete {output_ptr}")
                else:
                    save(f"{output_ptr}.name:{repr(output_channel.name)}")

            for clip_i, clip in enumerate(track.clips):
                if clip is None:
                    continue
                for data in clip.serialize(track_ptr, clip_i, id_to_ptr):
                    save(data)


IO_TYPES = {
    "ethernet_dmx": EthernetDmxOutput,
    "osc_server": OscServerInput,
    "midi": MidiDevice,
}

ALL_INPUT_TYPES = [
    OscServerInput,
    MidiDevice,
]
ALL_OUTPUT_TYPES = [
    EthernetDmxOutput,
    MidiDevice,
]

