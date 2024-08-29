all_pars = [Par1, Par2, Par3, Par4]

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

for i in range(4):
    all_pars[i].dimmer.value = Animation.value
    set_par_color(all_pars[i], color)

GlobalStorage.set("DecayOn", False)
