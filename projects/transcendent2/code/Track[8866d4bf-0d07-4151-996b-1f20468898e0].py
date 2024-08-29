global_dimmer = GlobalStorage.get("GlobalDimmer").value

adj_dimmer = GlobalStorage.get("ADJDimmer").value
ADJ1.dimmer.value = NormMult([ADJ1.dimmer.value, adj_dimmer, global_dimmer], 255)
ADJ2.dimmer.value = NormMult([ADJ2.dimmer.value, adj_dimmer, global_dimmer], 255)
