all_pars = [Par1, Par2, Par3, Par4]

i = GlobalStorage.get("spiral_i", 0)
i %= 4
i += 1


def increment():
    i = GlobalStorage.get("spiral_i", 0)
    i += 1
    i = GlobalStorage.set("spiral_i", i)


rate = Scale(Rate.value, 0, 127, 0.05, 0.5)
RateLimit(rate, increment, (), "spiral_rate_limit")

Demux(i, Animation.value, [Par.dimmer for Par in all_pars])
color = GlobalStorage.get(f"Color{ColorSelect.value}")


if 1 <= i <= 4:
    all_pars[i - 1].red.value = color.value[0]
    all_pars[i - 1].green.value = color.value[1]
    all_pars[i - 1].blue.value = color.value[2]
