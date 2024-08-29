all_bars = [Bar1, Bar2, Bar3, Bar4]

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

for i in range(4):
    all_bars[i].master.value = Animation.value
    set_bar_color(all_bars[i], 0, 8, color)

GlobalStorage.set("DecayOn", False)
