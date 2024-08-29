fg = [Bar1, Bar3]
bg = [Bar2, Bar4]
lg = [Bar1, Bar2]
rg = [Bar3, Bar4]
xg1 = [Bar1, Bar4]
xg2 = [Bar2, Bar3]

all_groups = [fg, bg, lg, rg, xg1, xg2]

gi = Group.value
si = Select.value
ci = Color.value
color = GlobalStorage.get(f"Color{ci+1}")

for group in all_groups:
    for bar in group:
        set_bar_master(bar, 0)

if 1 <= gi <= 6:
    group = all_groups[gi - 1]
    if si == 2:
        set_bar_color(group[0], 0, 8, color)
        set_bar_master(group[0], 255)
        set_bar_color(group[1], 0, 8, color)
        set_bar_master(group[1], 255)
    else:
        set_bar_color(group[si], 0, 8, color)
        set_bar_master(group[si], 255)
