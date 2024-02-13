other_pars = [Par2, Par3, Par4]

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

Par1.dimmer.value = Animation.value
set_par_color(Par1, color)


Par2.dimmer.value = DelayBeats(Par1.dimmer.value, 1)
set_par_color(Par2, DelayBeats(color, 1))

Par3.dimmer.value = DelayBeats(Par1.dimmer.value, 2)
set_par_color(Par3, DelayBeats(color, 2))

Par4.dimmer.value = DelayBeats(Par1.dimmer.value, 3)
set_par_color(Par4, DelayBeats(color, 3))


GlobalStorage.set("DecayOn", False)