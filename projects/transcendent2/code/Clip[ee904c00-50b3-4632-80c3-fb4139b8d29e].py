all_pars = [Par1, Par2, Par3, Par4]
Demux(Select.value, Animation.value, [Par.dimmer for Par in all_pars])

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

i = Select.value
if 1 <= i <= 4:
    set_par_color(all_pars[i - 1], color)

Input1.set(_beat)

GlobalStorage.set("DecayOn", True)
