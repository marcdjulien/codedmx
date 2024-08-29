all_bars = [Bar1, Bar2, Bar3, Bar4]


def random():
    i = GlobalStorage.get("sprinkle_i", 0)
    i = Random(1,16)
    i = GlobalStorage.set("sprinkle_i", i)


rate = Scale(Rate.value, 0, 127, 0.05, 0.5)
RateLimit(rate, random, (), "sprinkle_rate_limit")

pixel_n = GlobalStorage.get("sprinkle_i", 0)

for Bar in all_bars:
    set_bar_color(Bar, 0, 8, (0, 0, 0))
    Bar.master.value = 255

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")


set_bar2_color([Bar2, Bar1], pixel_n - 1, color)
set_bar2_color([Bar4, Bar3], pixel_n - 1, color)

GlobalStorage.set("DecayOn", True)
