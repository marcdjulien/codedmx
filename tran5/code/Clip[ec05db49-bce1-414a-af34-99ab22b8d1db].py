fg = [Par1, Par2]
bg = [Par3, Par4]
fbg = (fg, bg)

lg = [Par1, Par4]
rg = [Par2, Par3]
lrg = (lg, rg)

xg1 = [Par1, Par3]
xg2 = [Par2, Par4]
xgg = (xg1, xg2)

all_groups = [fbg, lrg, xgg,]

gi = Group.value

color1 = GlobalStorage.get("Color1")
color2 = GlobalStorage.get("Color2")


if 1 <= gi <= 3:
    groups = all_groups[gi-1]
    for par in groups[0]:   
        set_par_color(par, color1)
        par.dimmer.value = Animation.value

    for par in groups[1]:   
        set_par_color(par, color2)
        par.dimmer.value = 255 - Animation.value

GlobalStorage.set("DecayOn", False)
        

 