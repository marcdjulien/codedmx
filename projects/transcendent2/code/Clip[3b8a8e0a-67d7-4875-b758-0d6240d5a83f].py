all_bars = [Bar1, Bar2, Bar3, Bar4]

for i, Bar in enumerate(all_bars):
    if (i + 1) != Select.value:
        set_bar_master(Bar, 0)

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

i = Select.value
if 1 <= i <= 4:
    set_bar_color(all_bars[i - 1], 0, 8, color)
