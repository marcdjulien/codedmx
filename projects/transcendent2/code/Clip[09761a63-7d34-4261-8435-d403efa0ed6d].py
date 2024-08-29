bullets = GlobalStorage.get("bullets", [])
Manual1 = GlobalStorage.get("Manual1")
changing = Changing(Manual1.value, key="bullet")

if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

if changing and Manual1.value:
    bullets.append((0, color))


def update(bullets, color):
    to_remove = []
    for i, (pos, color) in enumerate(bullets):
        bullets[i] = (pos + 1, color)
        if pos > 16:
            to_remove.append(i)
    for i in to_remove[::-1]:
        bullets.pop(i)


RateLimit(1 / 30.0, update, (bullets, color), "manual_bullet")

set_bar_master(Bar1, 0)
set_bar_master(Bar2, 0)
set_bar_master(Bar3, 0)
set_bar_master(Bar4, 0)

for pos, color in bullets:
    set_bar2_color([Bar2, Bar1], pos, color)
    set_bar2_color([Bar4, Bar3], pos, color)
