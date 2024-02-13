all_pars = [Par1, Par2, Par3, Par4]

Manual1 = GlobalStorage.get("Manual1")
Manual2 = GlobalStorage.get("Manual2")
Manual3 = GlobalStorage.get("Manual3")
Manual4 = GlobalStorage.get("Manual4")
Manual5 = GlobalStorage.get("Manual5")


for i in range(1, 5):
    if GlobalStorage.get(f"Manual{i}").value > 0 or GlobalStorage.get("Manual5").value > 0:
        outputs[f"Par{i}"].dimmer.value = Animation.value
    else:
        outputs[f"Par{i}"].dimmer.value = 0


color = GlobalStorage.get(f"Color{ColorSelect.value}")

set_par_color(Par1, color)
set_par_color(Par2, color)
set_par_color(Par3, color)
set_par_color(Par4, color)