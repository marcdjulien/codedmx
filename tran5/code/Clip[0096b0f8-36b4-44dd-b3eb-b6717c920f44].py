all_pars = [Par1, Par2, Par3, Par4]
rate = Scale(Rate.value, 0, 100, 0.025, 0.5)
i = Sample(rate, Random(1, 4))
Demux(i, 255, [Par.dimmer for Par in all_pars])
color = GlobalStorage.get(f"Color{ColorSelect.value}")

for i in range(4):
    set_par_color(all_pars[i], color)
