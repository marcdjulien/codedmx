decay = GlobalStorage.get("DecayDimmer")
decay_on = GlobalStorage.get("DecayOn")

if decay_on:
    Par1.dimmer.value = Decay(Par1.dimmer.value, decay.value/255.0)
    Par2.dimmer.value = Decay(Par2.dimmer.value, decay.value/255.0)
    Par3.dimmer.value = Decay(Par3.dimmer.value, decay.value/255.0)
    Par4.dimmer.value = Decay(Par4.dimmer.value, decay.value/255.0)



par1_dimmer = GlobalStorage.get("Par1Dimmer").value
par2_dimmer = GlobalStorage.get("Par2Dimmer").value
par3_dimmer = GlobalStorage.get("Par3Dimmer").value
par4_dimmer = GlobalStorage.get("Par4Dimmer").value
global_dimmer = GlobalStorage.get("GlobalDimmer").value

Par1.dimmer.value = NormMult([Par1.dimmer.value, par1_dimmer, global_dimmer], 255)
Par2.dimmer.value = NormMult([Par2.dimmer.value, par2_dimmer, global_dimmer], 255)
Par3.dimmer.value = NormMult([Par3.dimmer.value, par3_dimmer, global_dimmer], 255)
Par4.dimmer.value = NormMult([Par4.dimmer.value, par4_dimmer, global_dimmer], 255)