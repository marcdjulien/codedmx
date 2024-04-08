import util
import model
import logging
import time
import random

logger = logging.getLogger(__name__)


__ALL__ = [
    "Scale",
    "Demux",
    "Multiplexer",
    "Changing",
    "ToggleOnChange",
    "Random",
    "LastChanged",
    "RateLimit",
    "Sample",
    "Mix",
    "SampleTrigger",
    "Delay",
    "DelayBeats",
    "Decay",
    "NormMult",
]


class FunctionFactory:
    def __init__(self):
        self._functions = {}

    def get(self, cls, key):
        if key not in self._functions:
            logger.debug(f"Creating new {cls.__name__}(key={key})")
            self._functions[key] = cls()
        return self._functions[key]


def Scale(x, in_min, in_max, out_min, out_max):
    """Scale(x, in_min, in_max, out_min, out_max) -> Return a scaled value.

    x (number): The number to scale.
    in_min (number): The original minimum number of the input.
    in_max (number): The original maximum number of the input.
    out_min (number): The resulting minimum number.
    out_max (number): The resulting maximum number.
    """
    value = (((x - in_min) / (in_max - in_min)) * (out_max - out_min)) + out_min
    return util.clamp(value, out_min, out_max)


def Clamp(x, min_value, max_value):
    """Clamp(x, min_value, max_value) -> Return a clamped value.

    x (number): The number to scale.
    min_value (number): The minimum number to clamp the input.
    max_value (number): The maximum number to clamp the input.
    """
    return util.clamp(x, min_value, max_value)


def Demux(select, value, outputs):
    """Demux(select, value, outputs) -> None.

    Sets the selected output chosen by 'select' to 'value'.

    select (number): The index (1-indexed) of the output to select.
                     0 means no output is selected.
    value (number): The value to set the Output to.
    outputs (list): A list of Outputs to select from.
    """
    n = len(outputs)
    if isinstance(value, list):
        reset_value = [0] * len(value)
    else:
        reset_value = 0

    for output in outputs:
        output.value = reset_value

    select = int(select)
    if select in range(n + 1):
        if select != 0:
            outputs[select - 1].value = value


def Multiplexer(select, inputs):
    """Multiplexer(select, inputs) -> Returns the selected input
    chosen by 'select' to 'value'.

    select (number): The index (1-indexed) of the input to select.
                     0 means no output is selected and None is returned.
    inputs (list): A list of Inputs to select from.
    """
    if select in range(1, len(inputs) + 1):
        return inputs[select].value


class FunctionChanging:
    def __init__(self):
        self._last_value = None

    def transform(self, new_value):
        changing = False
        if isinstance(new_value, (list)):
            changing = tuple(new_value) == self._last_value
            self._last_value = tuple(new_value)
        else:
            changing = self._last_value != new_value
            self._last_value = new_value
        return changing


def Changing(value, key):
    """Changing(value, key) -> Returns True if the value is changing.

    value (number, list): The number(s) to check.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionChanging, key)
    return obj.transform(value)


class FunctionToggleOnChange:
    def __init__(self):
        self._last_value = None
        self._toggle_value = 0

    def transform(self, new_value, rising_only):
        changing = False
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

        return self._toggle_value


def ToggleOnChange(value, rising_only, key):
    """ToggleOnChange(value, rising_only, key) -> Returns a toggling bool when the input changes.

    value (number): The number to check.
    rising_only (boolean): Whether to only toggle on a rising edge.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionToggleOnChange, key)
    return obj.transform(value, rising_only)


def Random(a, b):
    """Random(a, b) -> Random integer between a and b, inclusive.

    a (integer): The lower bound of the random range.
    b (integer): The higher bound of the random range.
    """
    if a < b:
        return random.randint(a, b)
    else:
        return random.randint(b, a)


class FunctionLastChanged:
    def __init__(self):
        self._last_values = []
        self._last_changed_index = 0

    def transform(self, new_values):
        if len(self._last_values) != len(new_values):
            self._last_values = new_values
            return 0

        for i, last_value in enumerate(self._last_values):
            changing = False
            new_value = new_values[i].value
            if isinstance(new_value, (list)):
                changing = tuple(new_value) == last_value
                self._last_values[i] = tuple(new_value)
            else:
                changing = last_value != new_value
                self._last_values[i] = new_value

            if changing:
                self._last_changed_index = i

        return self._last_changed_index


def LastChanged(values, key):
    """LastChanged(values) -> Returns the index (0-indexed) of the last changed value.

    values (list): The list of values to check.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionLastChanged, key)
    return obj.transform(values)


class FunctionRateLimit:
    def __init__(self):
        self._last_sample_time = 0

    def transform(self, rate, function, args):
        if rate <= 0 or (time.time() - self._last_sample_time) >= rate:
            self._last_sample_time = time.time()
            return function(*args)


def RateLimit(rate, function, args, key):
    """RateLimit(rate, function, args) -> None.

    Runs 'fuction' at the rate of 'rate'.

    rate (float): The rate to run the function.
    function (function): The custom fuction to run.
    args (tuple): Tuple of arguments to pass to funciton.
    """
    obj = FUNCTION_FACTORY.get(FunctionRateLimit, key)
    return obj.transform(rate, function, args)


class FunctionSample:
    def __init__(self):
        self._last_sample_time = 0
        self._last_value = 0

    def transform(self, rate, cur_value):
        if rate <= 0:
            return cur_value
        if (time.time() - self._last_sample_time) < rate:
            return self._last_value
        else:
            self._last_value = cur_value
            self._last_sample_time = time.time()
            return self._last_value


def Sample(rate, cur_value, key):
    """Sample(rate, cur_value, key) -> Returns a sampled value.

    rate (float): The sample rate.
    cur_value (number): The value to sample.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionSample, key)
    return obj.transform(rate, cur_value)


def Mix(a, b, amount):
    """Mix(a, b, amount) -> Returns a weighted average.

    A*amount + B*(1-amount)

    a (number or list): A value.
    b (number or list): B value.
    amount (float): The amount of the A value.
    """
    mix = util.clamp(amount, 0.0, 1.0)

    result = None
    if isinstance(a, (float, int)) and isinstance(b, (float, int)):
        result = (a * mix) + (b * (1.0 - mix))
    else:
        result = []
        for i, x in enumerate(a):
            r = (x * mix) + (b[i] * (1.0 - mix))
            result.append(r)

    return result


class FunctionSampleTrigger:
    def __init__(self):
        self._toggle = FunctionToggleOnChange()
        self._last_value = 0

    def transform(self, trigger, cur_value):
        if self._toggle.transform(trigger, rising_only=True):
            self._last_value = cur_value
        return self._last_value


def SampleTrigger(trigger, cur_value, key):
    """SampleTrigger(trigger, cur_value, key) -> Returns a sampled value based on a trigger.

    The sampled value is updated when the tirgger is on a rising edge.

    trigger (integer): The trigger value.
    cur_value (number): The value to sample.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionSampleTrigger, key)
    return obj.transform(trigger, cur_value)


class FunctionDelay:
    def __init__(self):
        self._delay = 0
        self._buffer = []
        self._last_time = 0
        self._last_value = None

    def get_n(self):
        return int(self._delay * 60)

    def transform(self, cur_value, delay):
        self._delay = delay

        n = self.get_n()
        n_buf = len(self._buffer)

        if self._last_value is None:
            if isinstance(cur_value, (int, float)):
                self._last_value = 0
            elif isinstance(cur_value, (list, tuple)):
                self._last_value = [0] * len(cur_value)

        if n <= 0:
            return cur_value
        elif n_buf != n:
            if isinstance(cur_value, (list, tuple)):
                reset_value = [0] * len(cur_value)
            else:
                reset_value = 0

            if n_buf < n:
                self._buffer.extend([reset_value] * (n - n_buf))
            else:
                self._buffer = self._buffer[0:n]

        self._buffer.insert(0, cur_value)
        self._last_value = self._buffer.pop()
        self._last_time = time.time()
        return self._last_value


def Delay(value, delay_amount, key):
    """Delay(value, delay_amount, key) -> Returns a delayed value.

    value (number or list): The value to delay.
    delay_amount (float): How long to delay the value in seconds.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionDelay, key)
    return obj.transform(delay_amount, value)


class FunctionDelayBeats(FunctionDelay):
    def get_n(self):
        beats = self._delay
        time_s = (float(beats) / model.STATE.tempo) * 60.0
        return int(time_s * 60)


def DelayBeats(value, delay_time, key):
    """DelayBeats(value, delay_time, key) -> Returns a delayed value.

    value (number or list): The value to delay.
    delay_time (float): How long to delay the value in beats.
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionDelayBeats, key)
    return obj.transform(value, delay_time)


class FunctionDecay:
    RATE = 1 / 64  # beats

    def __init__(self):
        self.value = None
        self._rate_limiter = FunctionRateLimit()

    def transform(self, value, decay_amount):
        rate_s = util.beats_to_seconds(self.RATE, model.STATE.tempo)
        self._rate_limiter.transform(rate_s, self.update_value, (value, decay_amount))
        return self.value[0] if len(self.value) == 1 else self.value

    def update_value(self, value, decay_amount):
        if self.value is None:
            if isinstance(value, (list, tuple)):
                self.value = [0] * len(value)
            else:
                self.value = [0]

        if isinstance(value, (int, float)):
            value = [value]

        for i, v in enumerate(value):
            if v >= self.value[i]:
                self.value[i] = v
            else:
                self.value[i] *= decay_amount

            if self.value[i] <= 0:
                self.value[i] = 0


def Decay(value, decay_amount, key):
    """Decay(value, decay_amount, key) -> Returns a decayed value.

    value (number or list): The value to decay.
    decay_amount (float): How much to decay the value (0.0 - 1.0).
    key (string): A unique name for this value.
    """
    obj = FUNCTION_FACTORY.get(FunctionDecay, key)
    return obj.transform(value, decay_amount)


def NormMult(values, factor):
    """NormMult(values, factor) -> Returns (x1/factor)*(x2/factor)*...(xn/factor).

    values (list of number): List of values.
    factor (float): Factor to divide by. Usually the max value of each
                    element in the list.
    """
    result = 1.0
    for value in values:
        result *= float(value) / factor
    return result * factor


FUNCTION_FACTORY = FunctionFactory()


"""
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
"""
