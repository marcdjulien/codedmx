ADJ1.shutter.value = 255
ADJ2.shutter.value = 255

ADJ1.dimmer.value = Animation.value
ADJ2.dimmer.value = Animation2.value
ADJ1.dimmer.value = Decay(ADJ1.dimmer.value, 0.8, "adj1decay")
ADJ2.dimmer.value = Decay(ADJ2.dimmer.value, 0.9, "adj2decay")

color_mapping = {
    0: 0,
    1: 8,
    2: 16,
    3: 23,
    4: 30,
    5: 37,
    6: 44,
    7: 51,
}

ADJ1.color.value = color_mapping[Color.value]
ADJ2.color.value = color_mapping[Color.value - 1]

ADJ1.macro_speed.value = MacroSpeed.value
ADJ2.macro_speed.value = MacroSpeed.value
ADJ1.macro.value = Macro.value * MacroOnOff.value
ADJ2.macro.value = Macro.value * MacroOnOff.value

ADJ1.pan.value = Pan1.value
ADJ2.pan.value = Pan2.value
ADJ1.tilt.value = Tilt.value
ADJ2.tilt.value = Tilt.value

ADJ1.shutter.value = Shutter.value
ADJ2.shutter.value = Shutter.value + 10
