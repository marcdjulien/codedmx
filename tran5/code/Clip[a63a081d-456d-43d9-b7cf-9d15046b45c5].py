GlobalStorage.set("Par1Dimmer", Par1Dimmer)
GlobalStorage.set("Par2Dimmer", Par2Dimmer)
GlobalStorage.set("Par3Dimmer", Par3Dimmer)
GlobalStorage.set("Par4Dimmer", Par4Dimmer)
GlobalStorage.set("Manual1", Manual1)
GlobalStorage.set("Manual2", Manual2)
GlobalStorage.set("Manual3", Manual3)
GlobalStorage.set("Manual4", Manual4)
GlobalStorage.set("Manual5", Manual5)
GlobalStorage.set("GlobalDimmer", GlobalDimmer)

Par1Dimmer.set(Scale(Par1Dimmer.value, 0, 127, 0, 255))
Par2Dimmer.set(Scale(Par2Dimmer.value, 0, 127, 0, 255))
Par3Dimmer.set(Scale(Par3Dimmer.value, 0, 127, 0, 255))
Par4Dimmer.set(Scale(Par4Dimmer.value, 0, 127, 0, 255))
Manual1.set(Scale(Manual1.value, 0, 127, 0, 255))
Manual2.set(Scale(Manual2.value, 0, 127, 0, 255))
Manual3.set(Scale(Manual3.value, 0, 127, 0, 255))
Manual4.set(Scale(Manual4.value, 0, 127, 0, 255))
Manual5.set(Scale(Manual5.value, 0, 127, 0, 255))
GlobalDimmer.set(Scale(GlobalDimmer.value, 0, 127, 0, 255))

decay = Scale(DecayDimmer.value, 0, 127, 0, 230)
DecayDimmer.set(decay)

GlobalStorage.set("DecayDimmer", DecayDimmer)
GlobalStorage.set("DecayOn", True)
for i in range(1, 5):
    GlobalStorage.set(f"Color{i}", inputs[f"Color{i}"])  


# TODO: Create a way to switch between Preset Decay and manual decay