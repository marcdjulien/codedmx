all_bars = [Bar1, Bar2, Bar3, Bar4]


decay = GlobalStorage.get("DecayDimmer")
decay_on = GlobalStorage.get("DecayOn")

if decay_on:
    for bar_i, Bar in enumerate(all_bars):
        for pixel_i in range(8):
            value = (
                Bar[f"r{pixel_i}"].value,
                Bar[f"g{pixel_i}"].value,
                Bar[f"b{pixel_i}"].value,
            )
            pixel_value = Decay(value, decay.value / 255.0, key=f"bar{bar_i}-{pixel_i}")
            (
                Bar[f"r{pixel_i}"].value,
                Bar[f"g{pixel_i}"].value,
                Bar[f"b{pixel_i}"].value,
            ) = pixel_value

par1_dimmer = GlobalStorage.get("Bar12Dimmer").value
par2_dimmer = GlobalStorage.get("Bar12Dimmer").value
par3_dimmer = GlobalStorage.get("Bar34Dimmer").value
par4_dimmer = GlobalStorage.get("Bar34Dimmer").value
global_dimmer = GlobalStorage.get("GlobalDimmer").value

Bar1.master.value = NormMult([Bar1.master.value, par1_dimmer, global_dimmer], 255)
Bar2.master.value = NormMult([Bar2.master.value, par2_dimmer, global_dimmer], 255)
Bar3.master.value = NormMult([Bar3.master.value, par3_dimmer, global_dimmer], 255)
Bar4.master.value = NormMult([Bar4.master.value, par4_dimmer, global_dimmer], 255)
