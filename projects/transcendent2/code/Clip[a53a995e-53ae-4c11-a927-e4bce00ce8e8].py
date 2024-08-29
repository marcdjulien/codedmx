ADJ1.shutter.value = 255
ADJ2.shutter.value = 255

ADJ1.dimmer.value = Animation.value
ADJ2.dimmer.value = Animation.value if Invert.value == 0 else (255 - Animation.value)

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
ADJ2.color.value = color_mapping[Color.value]

ADJ1.macro_speed.value = MacroSpeed.value
ADJ2.macro_speed.value = MacroSpeed.value
ADJ1.macro.value = Macro.value * MacroOnOff.value
ADJ2.macro.value = Macro.value * MacroOnOff.value

ADJ1.pan.value = Pan1.value
ADJ2.pan.value = Pan2.value

if ShareTilt.value:
    ADJ1.tilt.value = Tilt.value
    ADJ2.tilt.value = Tilt.value
else:
    ADJ1.tilt.value = Tilt.value
    ADJ2.tilt.value = Tilt2.value
