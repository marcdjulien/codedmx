all_bars = [Bar1, Bar2, Bar3, Bar4]

Manual1 = GlobalStorage.get("Manual1")
Manual2 = GlobalStorage.get("Manual2")
Manual3 = GlobalStorage.get("Manual3")
Manual4 = GlobalStorage.get("Manual4")
Manual5 = GlobalStorage.get("Manual5")
out = {1:Bar1,2:Bar2,3:Bar3,4:Bar4}

color = GlobalStorage.get(f"Color{ColorSelect.value}")
for i in range(1, 5):
    if (
        GlobalStorage.get(f"Manual{i}").value > 0
        or GlobalStorage.get("Manual5").value > 0
    ):
        set_bar_color(all_bars[i-1], 0, 8, color)
        out[i].master.value = Animation.value
        
    else:
        set_bar_color(all_bars[i-1], 0, 8, [0, 0, 0, 0])


color = GlobalStorage.get(f"Color{ColorSelect.value}")

GlobalStorage.set("DecayOn", True)
