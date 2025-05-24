import re
import scipy
import numpy as np
import time
import operator
import uuid
import math
import threading
import mido
import json
import logging
import tempfile
import os
import traceback

from collections import defaultdict
from pythonosc.dispatcher import Dispatcher
from pythonosc import osc_server
from pathlib import Path

import util
import dmxio

# For Custom Fuction Nodes
import colorsys
import random
from math import *
from functions import *

logger = logging.getLogger(__name__)


def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)


MAX_VALUES = {
    "bool": 1,
    "int": 255,
    "float": 100.0,
}

NEAR_THRESHOLD = 0.01

TYPES = ["bool", "int", "float", "any"]

UUID_DATABASE = {}

ID_COUNT = 0

GLOBAL_CODE_ID = "functions"


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
    uuid_pattern = (
        r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
    )
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


cast = {"bool": int, "int": int, "float": float, "any": lambda x: x}


class Channel(Identifier):
    def __init__(self, **kwargs):
        super().__init__()
        self._value = kwargs.get("value")
        self.size = kwargs.get("size", 1)

        if self._value is None:
            self._value = 0 if self.size == 1 else [0] * self.size

        self.dtype = kwargs.get("dtype", "float")
        # TODO: Replace with regex.sub and include special chars
        self.name = kwargs.get("name", "Channel").replace(" ", "")

    def get(self):
        return cast[self.dtype](self._value)

    def set(self, value):
        self._value = value

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "value": self._value,
                "size": self.size,
                "dtype": self.dtype,
                "name": self.name,
            }
        )
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.value = data["value"]
        self.dtype = data["dtype"]
        self.name = data["name"]
        self.size = data["size"]

    @property
    def value(self):
        return self.get()

    @value.setter
    def value(self, value):
        self.set(value)


class CodeEditorChannel:
    """Decorator around the Channel for use in the code editor.

    This is a restricted version of the Channel that prevents
    a user from doing manipulating the internal models.
    """

    def __init__(self, channel):
        self._channel = channel
        self.valid_attributes = ["set", "get", "value", "__class__"]

        if isinstance(channel, DmxOutputGroup):
            super().__getattribute__("valid_attributes").extend(
                super().__getattribute__("_channel").map.keys()
            )

    @property
    def channel(self, value):
        pass

    @channel.setter
    def channel(self, value):
        attrs = super().__getattribute__("valid_attributes")
        raise CodeEditorException(
            f"'channel' is not a valid attribute. Only use: {', '.join(attrs)}"
        )

    def __getattribute__(self, key):
        attrs = super().__getattribute__("valid_attributes")
        if key not in attrs:
            STATE.log.append(
                CodeEditorException(
                    f"'{key}' is not a valid attribute. Only use: {', '.join(attrs)}"
                )
            )
            return
        else:
            return getattr(super().__getattribute__("_channel"), key)

    def __getitem__(self, key):
        return super().__getattribute__("_channel")[key]


class CodeEditorException(Exception):
    """Exceptions that pertain to the Code Editor."""


class Parameter(Identifier):
    def __init__(self, name="", value=None, dtype="any"):
        super().__init__()
        self.name = name
        self.value = value
        self.dtype = dtype

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "name": self.name,
                "value": self.value,
            }
        )
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
        self.name = kwargs.get("name", "")
        self.channel = Channel(**kwargs)
        self.input_type = None
        self.is_constant = True

    def update(self, clip_beat):
        pass

    @property
    def dtype(self):
        return self.channel.dtype

    @property
    def value(self):
        if isinstance(self.channel.value, list):
            return tuple(self.channel.value)
        else:
            return self.channel.value

    @property
    def size(self):
        return self.channel.size

    def set(self, value):
        self.channel.set(value)

    def get(self):
        if isinstance(self.value, list):
            return tuple(self.channel.get())
        else:
            return self.channel.get()

    def serialize(self):
        data = super().serialize()

        data.update(
            {
                "name": self.name,
                "channel": self.channel.serialize(),
                "input_type": self.input_type,
            }
        )

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
        self.max_parameter = Parameter("max", MAX_VALUES[self.channel.dtype])
        self.key_parameter = Parameter("key", "")
        self.add_parameter(self.min_parameter)
        self.add_parameter(self.max_parameter)
        self.add_parameter(self.key_parameter)

        self.automations = []
        self.active_automation = None
        self.speed = 0
        self.last_beat = 0
        self.is_constant = False

        self.history = [self.get()]*100

    def update(self, clip_beat):
        self.history.pop(0)
        self.history.append(self.get())

        if self.active_automation is None:
            return

        beat = clip_beat * (2**self.speed)
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
        else:  # manual
            self.channel.set(self.ext_channel.get())

        self.last_beat = current_beat

    def ext_get(self):
        return self.ext_channel.get()

    def ext_set(self, value):
        # TODO: Use clamp
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
            self.parameters[index].value = cast[self.channel.dtype](float(value))
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
        elif self.parameters[index] == self.key_parameter:
            if not isinstance(value, str):
                return False
            if not value.isascii():
                return False
            if not len(value) == 1:
                return False
            self.key_parameter.value = value

            logger.debug("Mapping %s to %s", value, self)
            for key, channel in tuple(STATE.key_channel_map.items()):
                if channel == self:
                    del STATE.key_channel_map[key]
            STATE.key_channel_map[value.upper()] = self
            return True
        else:
            return super().update_parameter(index, value)

    def serialize(self):
        data = super().serialize()

        data.update(
            {
                "ext_channel": self.ext_channel.serialize(),
                "mode": self.mode,
                "active_automation": self.active_automation.id
                if self.active_automation
                else None,
                "automations": [
                    automation.serialize()
                    for automation in self.automations
                    if not automation.deleted
                ],
                "speed": self.speed,
            }
        )

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
        super().__init__(dtype="int", name=name or f"Dmx{dmx_address}")
        self.dmx_address = dmx_address
        self.history = [0] * 500

    def record(self):
        self.history.pop(0)
        self.history.append(self.value)

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "dmx_address": self.dmx_address,
            }
        )
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
        self.map = {
            self.channel_names[i]: self.outputs[i] for i in range(len(self.outputs))
        }

    def record(self):
        for output in self.outputs:
            output.record()

    def update_starting_address(self, address):
        self.dmx_address = address
        for i, output_channel in enumerate(self.outputs):
            output_channel.dmx_address = i + address

    def update_name(self, name):
        self.name = name
        for i, output_channel in enumerate(self.outputs):
            output_channel.name = f"{name}.{self.channel_names[i]}"

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "name": self.name,
                "dmx_address": self.dmx_address,
                "channel_names": self.channel_names,
                "outputs": [],
            }
        )

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

    def __getattr__(self, name):
        return self.map[name]

    def __getitem__(self, name):
        return self.map[name]


class ColorNode(SourceNode):
    nice_title = "Color"

    def __init__(self, **kwargs):
        kwargs.setdefault("dtype", "any")
        kwargs.setdefault("size", 3)
        super().__init__(**kwargs)
        self.input_type = "color"

    def set(self, value):
        self.channel.set(value)


class ButtonNode(SourceNode):
    nice_title = "Button"

    def __init__(self, **kwargs):
        kwargs.setdefault("dtype", "bool")
        kwargs.setdefault("size", 1)
        super().__init__(**kwargs)
        self.input_type = "button"

    def set(self, value):
        self.channel.set(value)


class OscInput(AutomatableSourceNode):
    def __init__(self, **kwargs):
        kwargs.setdefault("name", f"OSC")
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


class GlobalCodeStorage:
    def __init__(self):
        self._vars = dict()

    def get(self, name, default=None):
        if name not in self._vars:
            self._vars[name] = default
        return self._vars[name]

    def set(self, name, value):
        self._vars[name] = value

    def items(self):
        return self._vars.items()


class Point(Identifier):
    def __init__(self, x=None, y=None):
        super().__init__()
        self.x = x
        self.y = y

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "x": self.x,
                "y": self.y,
            }
        )
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
    TIME_RESOLUTION = 1 / 60.0

    def __init__(self, dtype="int", name="", min_value=0, max_value=1):
        super().__init__()
        self.dtype = dtype
        self.name = name
        self.length = 4  # beats
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
        data.update(
            {
                "length": self.length,
                "points": [
                    point.serialize() for point in self.points if not point.deleted
                ],
                "dtype": self.dtype,
                "name": self.name,
                "interpolation": self.interpolation,
            }
        )
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

    def execute(self):
        for i, preset in enumerate(self.presets):
            channel, automation, speed = preset
            if channel.is_constant:
                channel.set(automation)
            else:
                channel.set_active_automation(automation)
                channel.speed = speed

    def update(self, preset_name, presets):
        self.name = preset_name
        self.presets = presets

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "name": self.name,
                "presets": [
                    (
                        channel.id,
                        automation if channel.is_constant else automation.id,
                        speed,
                    )
                    for channel, automation, speed in self.presets
                ],
            }
        )
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        for preset_data in data["presets"]:
            channel_id, automation_id, speed = preset_data
            channel = UUID_DATABASE[channel_id]
            self.presets.append(
                (
                    channel,
                    automation_id
                    if channel.is_constant
                    else UUID_DATABASE[automation_id],
                    speed,
                )
            )


class MultiClipPreset(Identifier):
    def __init__(self, name=None, clip_presets=None):
        super().__init__()
        self.name = name
        self.presets = clip_presets or []

    def execute(self):
        start = time.time()
        for clip_preset in self.presets:
            track, clip, preset = clip_preset
            STATE.execute(f"set_clip {track.id} {clip.id}")
            preset.execute()
        STATE.start()

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "name": self.name,
                "presets": [
                    f"{track.id}:{clip.id}:{preset.id}"
                    for track, clip, preset in self.presets
                ],
            }
        )
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        for preset_ids in data["presets"]:
            track_id, clip_id, preset_id = preset_ids.split(":")
            self.presets.append(
                (
                    UUID_DATABASE[track_id],
                    UUID_DATABASE[clip_id],
                    UUID_DATABASE[preset_id],
                )
            )


class Trigger(Identifier):
    """Triggers can be used to map aribitrary inputs to program commands."""

    def __init__(self, name, type_, event, command):
        """Constructor.

        Args:
            name (str): The name of the Trigger.
            type (str): The type of the event to fire the Trigger ("midi", "osc", or "key").
            event (tuple): Tuple of the event (e.g, midi node and value, osc, etc.).
            command (str): The command to execute when the event is met.
        """
        self.name = name
        self.type = type_
        self.event = event
        self.command = command

    def test(self, event):
        return self.event == event

    def run(self):
        STATE.log.append(f"Trigger Fired! {self.name}: {self.event} {self.command}")
        STATE.execute(self.command)


class TriggerManager:
    def __init__(self):
        self.triggers = []

    def add_trigger(self, trigger):
        self.triggers.append(trigger)

    def fire_triggers(self, type_, event):
        for trigger in self.triggers:
            if type_ == trigger.type and trigger.test(event):
                trigger.run()


class Code:
    def __init__(self, code_id):
        self.id = code_id
        self._update_path()
        self.compiled = None

    def exists(self):
        return (
            os.path.exists(self.file_path_name)
            if self.file_path_name is not None
            else False
        )

    def reload(self):
        try:
            if self.file_path_name is not None:
                if not self.exists():
                    Path(self.file_path_name).touch()

                with open(self.file_path_name, "r") as f:
                    self.compiled = compile(f.read(), self.file_path_name, "exec")
        except Exception as e:
            STATE.log.append(e)

    def run(self, pre, post, inputs, outputs):
        if self.compiled is not None:
            for key, input_ in inputs.items():
                inputs[key] = CodeEditorChannel(input_)
                exec(f"{key} = inputs['{key}']")

            for key, output_ in outputs.items():
                outputs[key] = CodeEditorChannel(output_)
                exec(f"{key} = outputs['{key}']")

            _time = STATE.time_since_start_s
            _beat = STATE.time_since_start_beat + 1

            exec(pre.compiled)
            exec(self.compiled)
            exec(post.compiled)

    def _update_path(self):
        if STATE.project_folder_path is not None:
            self.file_path_name = os.path.join(
                STATE.project_folder_path, "code", f"{self.id}.py"
            )
            self.temp = False
        else:
            self.file_path_name = tempfile.TemporaryFile().name + ".py"
            self.temp = True

    def save(self, text):
        self._update_path()

        with open(self.file_path_name, "w") as f:
            logger.debug("%s saved", self.file_path_name)
            f.write(text)

        if self.temp and STATE.project_folder_path is not None:
            new_path = os.path.join(STATE.project_folder_path, "code", f"{self.id}.py")
            os.rename(self.file_path_name, new_path)
            self.file_path_name = new_path
            self.temp = False

    def read(self):
        if self.exists():
            with open(self.file_path_name, "r") as f:
                return f.read()
        else:
            return ""


class Clip(Identifier):
    def __init__(self, name="", outputs=[], global_clip=False):
        super().__init__()
        self.name = name

        self.inputs = []
        self.outputs = outputs
        self.presets = []

        self.global_clip = global_clip

        self.speed = 0
        self.time = 0
        self.playing = False

        self.code = Code(self.id)

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

    def update(self, global_code, track_code, beat):
        if self.playing:
            self.time = beat * (2**self.speed)
            for channel in self.inputs:
                if channel.deleted:
                    continue
                channel.update(self.time)

            inputs = {src.name: src for src in self.inputs if not src.deleted}

            outputs = {
                output.name: output for output in self.outputs if not output.deleted
            }
            try:
                if self.global_clip:
                    self.run_global_code()
                self.code.run(global_code, track_code, inputs, outputs)
            except Exception as e:
                logger.warning("Failed to execute: %s", self.code.file_path_name)
                logger.warning(e)
                STATE.log.append(traceback.format_exc())
                self.stop()

    def run_global_code(self):
        """Runs the code special to Global Clips.

        Includes:
            - Adding all inputs to GlobalStorage
        """
        for input_ in self.inputs:
            GlobalStorage.set(input_.name, CodeEditorChannel(input_))

    def start(self, restart=True):
        if restart:
            self.time = 0

        if self.playing:
            return

        self.code.reload()
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
        data.update(
            {
                "name": self.name,
                "code_file_path_name": self.code.file_path_name,
                "speed": self.speed,
                "inputs": [
                    channel.serialize()
                    for channel in self.inputs
                    if not channel.deleted
                ],
                "outputs": [
                    channel.serialize()
                    for channel in self.outputs
                    if not channel.deleted
                ],
                "presets": [
                    preset.serialize() for preset in self.presets if not preset.deleted
                ],
                "global_clip": self.global_clip,
            }
        )

        return data

    def deserialize(self, data):
        super().deserialize(data)

        self.name = data["name"]
        self.speed = data["speed"]
        self.outputs = [
            UUID_DATABASE[output_data["id"]] for output_data in data["outputs"]
        ]
        self.code = Code(self.id)
        self.code.reload()

        self.global_clip = data.get("global_clip", False)

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

        for preset_data in data["presets"]:
            clip_preset = ClipPreset()
            clip_preset.deserialize(preset_data)
            self.presets.append(clip_preset)


class Sequence(Identifier):
    def __init__(self, name="", sequence_info=()):
        super().__init__()
        self.name = name
        # List of (Clip, ClipPreset, duration)
        self.sequence_info = sequence_info
        self.length = 0
        self.update_length()

    def update(self, name, sequence_info):
        self.name = name
        self.sequence_info = sequence_info

    def update_length(self):
        self.length = 0
        for seq in self.sequence_info:
            self.length += seq[2]

    def current_clip(self, beat):
        current_beat = beat % self.length
        sum_beat = 0
        last_seq = self.sequence_info[0]
        for seq in self.sequence_info:
            last_seq = seq
            clip, preset, duration = seq
            sum_beat += duration

            if current_beat < sum_beat:
                break

        clip, preset, _ = last_seq
        return clip, preset

    def serialize(self):
        data = super().serialize()
        data.update(
            {
                "name": self.name,
                "sequence_info": [(s[0].id, s[1].id, s[2]) for s in self.sequence_info],
            }
        )
        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.name = data["name"]
        self.sequence_info = [
            (UUID_DATABASE[d[0]], UUID_DATABASE[d[1]], d[2])
            for d in data["sequence_info"]
        ]
        self.update_length()


class Track(Identifier):
    def __init__(self, name="", n_clips=20, global_track=False):
        super().__init__()
        self.name = name
        self.clips = [None] * n_clips
        self.outputs = []
        self.code = Code(self.id)
        self.sequences = []
        self.sequence = None
        self.global_track = global_track

    def update(self, global_code, beat):
        if self.sequence is not None:
            seq_clip, preset = self.sequence.current_clip(beat)
            # Always execute the preset
            preset.execute()
            for clip in self.clips:
                if clip is None:
                    continue

                if clip == seq_clip:
                    if not seq_clip.playing:
                        seq_clip.start()
                else:
                    clip.stop()

        for clip in self.clips:
            if clip is not None:
                clip.update(global_code, self.code, beat)

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

    def start(self, clip):
        for other_clip in self.clips:
            if other_clip is None or clip == other_clip:
                continue
            other_clip.stop()
        clip.start()

    def toggle(self, clip):
        for other_clip in self.clips:
            if other_clip is None or clip == other_clip:
                continue
            other_clip.stop()
        clip.toggle()

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
        data.update(
            {
                "name": self.name,
                "clips": [clip.serialize() if clip else None for clip in self.clips],
                "sequences": [sequence.serialize() for sequence in self.sequences],
                "outputs": [],
                "global_track": self.global_track,
            }
        )

        for output in self.outputs:
            if output.deleted:
                continue
            output_type = "single" if isinstance(output, DmxOutput) else "group"
            data["outputs"].append((output_type, output.serialize()))

        return data

    def deserialize(self, data):
        super().deserialize(data)
        self.code = Code(self.id)

        self.name = data["name"]
        n_clips = len(data["clips"])
        self.clips = [None] * n_clips

        self.global_track = data.get("global_track", False)

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

        for sequence_data in data.get("sequences", []):
            sequence = Sequence()
            sequence.deserialize(sequence_data)
            self.sequences.append(sequence)


class IO:
    type = None

    def __init__(self, args):
        self.args = args
        self.last_io_time = 0
        logger.debug("Created %s(%s)", self.type, self.args)

    def update(self, outputs):
        raise NotImplementedError

    def serialize(self):
        return {"type": self.type, "args": self.args}

    def deserialize(self, data):
        pass

    def connect(self):
        pass

    def connected(self):
        raise NotImplementedError

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
                self.dmx_frame[channel.dmx_address - 1] = min(
                    255, max(0, int(round(channel.get())))
                )

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
                self.dmx_frame[channel.dmx_address - 1] = min(
                    255, max(0, int(round(channel.get())))
                )

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
        self.host = "0.0.0.0"
        self.dispatcher = None
        self.server = None
        self.thread = None
        self.channel_map = defaultdict(list)
        self.connect()

    def map_channel(self, endpoint, input_channel):
        def func(endpoint, value):
            STATE.osc_log.append(f"Recieved: {endpoint} = {value}")
            input_channel.ext_set(value)
            if bool(value):
                STATE.trigger_manager.fire_triggers("osc", (endpoint))
            self.update_io_time()

        self.dispatcher.map(endpoint, func)
        self.channel_map[endpoint].append(input_channel)
        STATE.osc_log.append(f"Mapped {endpoint}")

    def umap(self, endpoint, input_channel):
        self.dispatcher.umap(
            endpoint, lambda endpoint, *args: print(f"Unmapped {endpoint} {args}")
        )
        self.channel_map[endpoint].remove(input_channel)
        STATE.osc_log.append(f"Unmapped {endpoint}")

    def update(self, outputs):
        pass

    def __str__(self):
        return f"OscServer"

    def connect(self):
        try:
            self.dispatcher = Dispatcher()
            self.server = osc_server.ThreadingOSCUDPServer(
                (self.host, self.port), self.dispatcher
            )

            def start_osc_listening_server():
                STATE.osc_log.append(
                    f"OSCServer started on {self.server.server_address}"
                )
                self.server.serve_forever()
                STATE.osc_log.append("OSC Server Stopped")

            self.thread = threading.Thread(target=start_osc_listening_server)
            self.thread.daemon = True
            self.thread.start()
        except Exception as e:
            logger.warning(e)
            STATE.osc_log.append(e)

    def connected(self):
        return self.thread is not None and self.thread.is_alive()

    def serialize(self):
        data = super().serialize()
        data["channel_map"] = {}
        for endpoint, input_channels in self.channel_map.items():
            data["channel_map"][endpoint] = [channel.id for channel in input_channels]
        return data

    def deserialize(self, data):
        super().deserialize(data)
        for endpoint, input_channels in data["channel_map"].items():
            for input_channel_id in input_channels:
                self.map_channel(endpoint, UUID_DATABASE[input_channel_id])


# TODO: Map these when the real server is created.
class GhostOSCServerInput:
    """Used to store mappings when initializing or if a user
    tries to create a OSC node without a server defined."""

    channel_map = defaultdict(list)

    @staticmethod
    def map_channel(endpoint, input_channel):
        GhostOSCServerInput.channel_map[endpoint].append(input_channel)
        STATE.osc_log.append(f"Saving {endpoint}. No OSC Server defined.")

    @staticmethod
    def umap(endpoint, input_channel):
        GhostOSCServerInput.channel_map[endpoint].remove(input_channel)
        STATE.osc_log.append(f"Unmapped {endpoint}")


PITCH_FAKE_CONTROL = -1


def midi_value(msg):
    if msg.type == "control_change":
        value = msg.value
        note_control = msg.control
    elif msg.type in ["note_on"]:
        value = msg.velocity
        note_control = msg.note
    elif msg.type in ["note_off"]:
        value = 0
        note_control = msg.note
    elif msg.type == "pitchwheel":
        value = msg.pitch
        note_control = PITCH_FAKE_CONTROL

    return note_control, value


class MidiInputDevice(IO):
    nice_title = "MIDI (Input)"
    arg_template = "name"
    type = "midi_input"

    def __init__(self, args, channel_map=None):
        super().__init__(args)
        self.device_name = args
        self.port = None
        self.channel_map = defaultdict(lambda: defaultdict(list))

        self.connect()

        if channel_map is not None:
            self.map_channels(channel_map)

    def map_channel(self, midi_channel, note_control, channel):
        global_unmap_midi(channel)
        self.channel_map[midi_channel][note_control].append(channel)

        # There are two ways to update a mapping.
        # 1) Update the parameter in the gui, then trigger this map channel
        # function.
        # 2) Trigger this map channel function first, then update the parameter.
        # The following code is for method 2, should update this to be consistent.
        channel.get_parameter("device").value = self.device_name
        channel.get_parameter("id").value = f"{midi_channel}/{note_control}"

    def map_channels(self, channel_map):
        for midi_channel, note_controls in channel_map.items():
            for note_control, channels in note_controls.items():
                for channel in channels:
                    self.map_channel(midi_channel, note_control, channel)

    def unmap_channel(self, channel):
        for midi_channel, note_controls in self.channel_map.items():
            for note_control, channels in note_controls.items():
                for other_channel in channels:
                    if channel == other_channel:
                        self.channel_map[midi_channel][note_control].remove(channel)
                        channel.get_parameter("device").value = ""
                        channel.get_parameter("id").value = ""
                        break

    def reset(self):
        self.channel_map = defaultdict(lambda: defaultdict(list))

    def callback(self, message):
        global LAST_MIDI_MESSAGE
        LAST_MIDI_MESSAGE = (self.device_name, message)
        STATE.midi_log.append(f"Received {message} on {self.device_name}")
        midi_channel = message.channel
        note_control, value = midi_value(message)
        if (
            midi_channel in self.channel_map
            and note_control in self.channel_map[midi_channel]
        ):
            for channel in self.channel_map[midi_channel][note_control]:
                # TODO: Test this
                # MIDI values are 0-127, scale to 255.
                channel.ext_set(2*value)

        if value >= 127:
            STATE.trigger_manager.fire_triggers(
                "midi", (self.device_name, midi_channel, note_control)
            )

        self.update_io_time()

    def serialize(self):
        data = super().serialize()
        data["channel_map"] = {}
        for midi_channel, note_controls in self.channel_map.items():
            data["channel_map"][midi_channel] = {}
            for note_control, channels in note_controls.items():
                data["channel_map"][midi_channel][note_control] = [
                    channel.id for channel in channels
                ]
        return data

    def deserialize(self, data):
        super().deserialize(data)
        for midi_channel, note_controls in data["channel_map"].items():
            for note_control, channels in note_controls.items():
                for channel_id in channels:
                    self.map_channel(
                        int(midi_channel), int(note_control), UUID_DATABASE[channel_id]
                    )

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
            self.port.send(
                mido.Message(
                    "note_on", channel=midi_channel, note=note_control, velocity=value
                )
            )

        self.update_io_time()

    def map_channel(self, midi_channel, note_control, channel):
        self.channel_map[(midi_channel, note_control)] = channel

    def unmap_channel(self, channel):
        for (midi_channel, note_control), other_channel in self.channel_map.items():
            if channel == other_channel:
                del self.channel_map[(midi_channel, note_control)]
                self.port.send(
                    mido.Message(
                        "note_off", channel=midi_channel, note=note_control, velocity=0
                    )
                )
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
            self.map_channel(
                int(midi_channel), int(note_control), UUID_DATABASE[channel_id]
            )

    def connect(self):
        try:
            self.port = mido.open_output(self.device_name)
            self.port.reset()
        except Exception as e:
            logger.warning(e)

    def connected(self):
        return self.port is not None and not self.port.closed


N_TRACKS = 6


def global_osc_server():
    for io in STATE.io_inputs:
        if isinstance(io, OscServerInput):
            return io

    # Did not find the OscServerInput, this means we're initializing
    # or a user tried to create and map an OSC node without creating
    # the OscServerInput.
    return GhostOSCServerInput


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
    def __init__(self):
        global STATE
        STATE = self
        super().__init__()
        self.mode = "edit"
        self.project_name = "Untitled"
        self.project_file_path = None
        self.project_folder_path = None
        self.tracks = []

        for i in range(N_TRACKS - 1):
            self.tracks.append(Track(f"Track {i}"))
        self.global_track = Track(f"Global", global_track=True)
        self.tracks.append(self.global_track)

        self.io_outputs = [None] * 5
        self.io_inputs = [None] * 5

        self.global_vars = {}
        self.key_channel_map = {}

        self.playing = False
        self.tempo = 120.0
        self.play_time_start_s = 0
        self.time_since_start_beat = 0
        self.time_since_start_s = 0

        self.multi_clip_presets = []
        self.code = Code(GLOBAL_CODE_ID)

        self.trigger_manager = TriggerManager()

        self.log = []
        self.osc_log = []
        self.midi_log = []

        self.id = "global"

    def toggle_play(self):
        if self.playing:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.playing:
            return

        # Load global functions
        try:
            self.code.reload()
            for track in self.tracks:
                track.code.reload()
        except Exception as e:
            logger.warning("Failed to execute: %s", self.code.file_path_name)
            logger.warning(e)
            self.log.append(traceback.format_exc())
            return

        # Start playing
        self.playing = True
        self.play_time_start_s = time.time()

    def stop(self):
        self.playing = False

    def update(self):
        # Update timing
        if self.playing:
            self.time_since_start_s = time.time() - self.play_time_start_s
            self.time_since_start_beat = util.seconds_to_beats(
                self.time_since_start_s, self.tempo
            )

            # Update values
            for track in self.tracks:
                track.update(self.code, self.time_since_start_beat)

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
            "project_file_path": self.project_file_path,
            "project_folder_path": self.project_folder_path,
            "tracks": [track.serialize() for track in self.tracks],
            "io_inputs": [
                None if device is None else device.serialize()
                for device in self.io_inputs
            ],
            "io_outputs": [
                None if device is None else device.serialize()
                for device in self.io_outputs
            ],
            "multi_clip_presets": [
                mcp.serialize() for mcp in self.multi_clip_presets
            ],
        }

        return data

    def deserialize(self, data, project_file_path):
        # Reset the project file path in case it was moved.
        self.project_file_path = project_file_path
        self.project_folder_path = os.path.dirname(project_file_path)

        self.tempo = data["tempo"]
        self.project_name = data["project_name"]
        self.code = Code(GLOBAL_CODE_ID)

        for i, track_data in enumerate(data["tracks"]):
            new_track = Track()
            new_track.deserialize(track_data)
            self.tracks[i] = new_track

        self.global_track = self.tracks[-1]
        assert self.global_track.global_track

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

        for multi_clip_preset_data in data.get("multi_clip_presets", []):
            multi_clip_preset = MultiClipPreset()
            multi_clip_preset.deserialize(multi_clip_preset_data)
            self.multi_clip_presets.append(multi_clip_preset)

        # Play each global clip at least once to prepopulate any required vars
        for clip in self.global_track.clips:
            if not util.valid(clip):
                continue
            self.execute(f"toggle_clip {self.global_track.id} {clip.id}")
            self.update()
            self.execute(f"toggle_clip {self.global_track.id} {clip.id}")
        self.stop()

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
            "toggle_play",
            "set_active_automation",
            "toggle_clip",
            "play_clip",
            "set_clip",
            "update_parameter",
        ]

        toks = full_command.split()
        cmd = toks[0]

        if cmd not in ["update_automation_point"]:
            logger.info(full_command)

        if self.mode == "performance":
            if cmd not in allowed_performance_commands:
                return Result(False)

        if cmd == "toggle_play":
            self.toggle_play()
            return Result(True)
        elif cmd == "toggle_clip":
            track_id = toks[1]
            clip_id = toks[2]
            track = self.get_obj(track_id)
            clip = self.get_obj(clip_id)
            track.toggle(clip)
            track.sequence = None
            if clip.playing:
                self.start()
            return Result(True)

        if cmd == "play_clip":
            track_id = toks[1]
            clip_id = toks[2]
            track = self.get_obj(track_id)
            clip = self.get_obj(clip_id)
            track.sequence = None
            track.start(clip)
            self.start()
            return Result(True)

        elif cmd == "set_clip":
            track_id = toks[1]
            clip_id = toks[2]
            track = self.get_obj(track_id)
            clip = self.get_obj(clip_id)
            track.sequence = None
            track.start(clip)
            return Result(True)

        elif cmd == "new_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            track[clip_i] = Clip(f"Controller #{clip_i}", track.outputs, global_clip=track.global_track)
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
            channel_names = full_command.split(" ")[-1].split(",")
            new_output_group = track.create_output_group(
                address, channel_names, group_name
            )
            return Result(True, new_output_group)

        elif cmd == "delete_node":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            obj.deleted = True
            return Result(True)

        elif cmd == "delete_clip":
            track_id, clip_i = toks[1].split(",")
            clip_i = int(clip_i)
            track = self.get_obj(track_id)
            assert clip_i < len(track.clips)
            clip = track[clip_i]
            clip.deleted = True
            del track[clip_i]
            return Result(True)

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
            data_string = " ".join(toks[1::])
            data = json.loads(data_string)

            preset_name = data["name"]
            clip = self.get_obj(data["clip"])
            all_preset_info = data["preset_info"]

            presets = []
            for preset_info in all_preset_info:
                channel = self.get_obj(preset_info["channel"])
                automation = (
                    preset_info["automation"]
                    if channel.is_constant
                    else self.get_obj(preset_info["automation"])
                )
                speed = preset_info["speed"]
                presets.append((channel, automation, speed))

            if data["preset_id"]:
                preset = self.get_obj(data["preset_id"])
                preset.update(preset_name, presets)
            else:
                preset = clip.add_preset(preset_name, presets)

            return Result(True, preset)

        elif cmd == "add_multi_clip_preset":
            all_multi_clip_preset_ids = toks[1].split(",")
            multi_clip_preset_name = " ".join(toks[2:])

            clip_presets = []
            for multi_clip_preset_id in all_multi_clip_preset_ids:
                track_id, clip_id, preset_id = multi_clip_preset_id.split(":")
                track = self.get_obj(track_id)
                clip = self.get_obj(clip_id)
                preset = self.get_obj(preset_id)
                clip_presets.append((track, clip, preset))

            multi_clip_preset = MultiClipPreset(
                multi_clip_preset_name, clip_presets=clip_presets
            )
            self.multi_clip_presets.append(multi_clip_preset)
            return Result(True, multi_clip_preset)

        elif cmd == "add_sequence":
            data_string = " ".join(toks[1::])
            data = json.loads(data_string)

            name = data["name"]
            track = self.get_obj(data["track"])
            sequence_data = data["sequence_info"]

            sequence_info = []
            for si in sequence_data:
                clip_id, preset_id, duration = si
                clip = self.get_obj(clip_id)
                preset = self.get_obj(preset_id)
                sequence_info.append((clip, preset, duration))

            if data["sequence_id"]:
                sequence = self.get_obj(data["sequence_id"])
                sequence.update(name, sequence_info)
            else:
                track.sequences.append(Sequence(name, sequence_info))
            return Result(True)

        elif cmd == "add_trigger":
            data_string = " ".join(toks[1::])
            data = json.loads(data_string)

            name = data["name"]
            type_ = data["type"].lower()
            command = data["command"]

            if type_.lower() == "midi":
                device_name, toks = data["event"].split(",")
                channel, note_control = toks.split("/")

                device_name = device_name.strip()
                channel = int(channel)
                note_control = int(note_control)

                event = (device_name, channel, note_control)

            else:
                event = data["event"]

            self.trigger_manager.add_trigger(Trigger(name, type_, event, command))
            return Result(True)

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
            MIDI_LIST = (
                MIDI_OUTPUT_DEVICES if input_output == "outputs" else MIDI_INPUT_DEVICES
            )
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
            old_clip = self.get_obj(clip_id)
            new_clip = self.duplicate_obj(old_clip)
            new_clip.code.save(old_clip.code.read())
            new_track[new_clip_i] = new_clip
            return Result(True, new_clip)

        elif cmd == "duplicate_node":
            clip_id = toks[1]
            obj_id = toks[2]
            clip = self.get_obj(clip_id)
            obj = self.get_obj(obj_id)
            new_obj = self.duplicate_obj(obj)
            clip.inputs.append(new_obj)
            new_obj.name = update_name(new_obj.name, [obj.name for obj in clip.inputs])
            return Result(True, new_obj)

        elif cmd == "double_automation":
            automation_id = toks[1]
            automation = self.get_obj(automation_id)
            old_length = automation.length
            automation.length = old_length * 2
            for point in tuple(automation.points):
                if not point.deleted:
                    automation.add_point(Point(point.x + old_length, point.y))
            return Result(True)

        elif cmd == "duplicate_channel_preset":
            input_id = toks[1]
            automation_id = toks[2]
            input_channel = self.get_obj(input_id)
            automation = self.get_obj(automation_id)
            new_automation = self.duplicate_obj(automation)
            input_channel.add_automation(new_automation)
            return Result(True, new_automation)

        elif cmd == "duplicate_clip_preset":
            clip_id = toks[1]
            preset_id = toks[2]
            clip = self.get_obj(clip_id)
            preset = self.get_obj(preset_id)
            new_preset = clip.add_preset(preset.name, preset.presets)
            return Result(True, new_preset)

        elif cmd == "midi_map":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            device_name = obj.get_parameter("device").value
            id_ = obj.get_parameter("id").value
            midi_channel, note_control = id_.split("/")
            global_midi_control(device_name, "in").map_channel(
                int(midi_channel), int(note_control), obj
            )
            return Result(True)

        elif cmd == "update_midi_device":
            obj_id = toks[1]
            new_device_name = toks[2]
            obj = self.get_obj(obj_id)
            id_ = obj.get_parameter("id").value
            midi_channel, note_control = id_.split("/")
            global_midi_control(new_device_name, "in").map_channel(
                int(midi_channel), int(note_control), obj
            )
            return Result(True)

        elif cmd == "unmap_midi":
            obj_id = toks[1]
            obj = self.get_obj(obj_id)
            global_unmap_midi(obj)
            return Result(True)

        elif cmd == "remap_midi_device":
            data_string = " ".join(toks[1::])
            data = json.loads(data_string)

            old_device_index = data["index"]
            old_device = self.io_inputs[old_device_index]
            new_device_name = data["new_device_name"]

            new_device = MidiInputDevice(
                new_device_name, channel_map=old_device.channel_map
            )
            self.io_inputs[old_device_index] = new_device
            MIDI_INPUT_DEVICES[new_device_name] = new_device

            del MIDI_INPUT_DEVICES[old_device.device_name]
            old_device.reset()

            return Result(True, new_device)

    def get_obj(self, id_):
        return UUID_DATABASE[id_]

    def channel_from_key(self, key):
        return self.key_channel_map.get(key)


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

STATE = None
GlobalStorage = GlobalCodeStorage()
Global = GlobalStorage

# Weird hack.
# Without this, the program will hang for several seconds
# when first adding a source node, because reinterpolate()
# will be called. Call first here instead.
scipy.interpolate.interp1d([0, 0], [1, 1])
