Manual1 = GlobalStorage.get("Manual1")

changing = Changing(Manual1.value, key="bounce")
GlobalStorage.set("Button1.changing", changing)

acc = -1
vel = GlobalStorage.get("vel", 0)
pos = GlobalStorage.get("pos", 0)

if changing:
    GlobalStorage.set("vel", 10)


def update(pos, vel, acc):
    if pos <= 0:
        GlobalStorage.set("vel", -abs(vel * 0.89))
        GlobalStorage.set("pos", 0)
    else:
        GlobalStorage.set("vel", vel + acc)
        GlobalStorage.set("pos", pos + vel)


pos = Clamp(pos, 1, 100)
RateLimit(1 / 30.0, update, (pos, vel, acc), "bball")
pixel_n = int(Scale(pos, 0, 100, 0, 15))


if ColorSelect.value == 5:
    color1 = GlobalStorage.get(f"Color1").value
    color2 = GlobalStorage.get(f"Color2").value
    color = Mix(color1, color2, ColorMix.value)
else:
    color = GlobalStorage.get(f"Color{ColorSelect.value}")

set_bar_master(Bar1, 0)
set_bar_master(Bar2, 0)
set_bar_master(Bar3, 0)
set_bar_master(Bar4, 0)


set_bar2_color([Bar2, Bar1], pixel_n, color)
set_bar2_color([Bar4, Bar3], pixel_n, color)
