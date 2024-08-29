fg = [Par1, Par2]
bg = [Par3, Par4]
lg = [Par1, Par4]
rg = [Par2, Par3]
xg1 = [Par1, Par3]
xg2 = [Par2, Par4]

all_groups = [fg, bg, lg, rg, xg1, xg2]

gi = Group.value
si = Select.value
ci = Color.value
color = GlobalStorage.get(f"Color{ci+1}")

for group in all_groups:
    for par in group:
        par.dimmer.value = 0
        set_par_color(par, color)

if 1 <= gi <= 6:
    group = all_groups[gi - 1]
    if si == 2:
        group[0].dimmer.value = 255
        group[1].dimmer.value = 255
    else:
        group[si].dimmer.value = 255
        group[int(not si)].dimmer.value = 0
