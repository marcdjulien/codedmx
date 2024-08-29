def set_par_color(Par, Color):
    if isinstance(Color, (list, tuple)):
        color = Color
    else:
        color = Color.value
    Par.red.value = color[0]
    Par.green.value = color[1]
    Par.blue.value = color[2]


def set_bar_color(Bar, start, n, Color):
    if isinstance(Color, (list, tuple)):
        color = Color
    else:
        color = Color.value

    for i in range(start, start + n):
        Bar[f"r{i}"].value = color[0]
        Bar[f"g{i}"].value = color[1]
        Bar[f"b{i}"].value = color[2]


def set_bar2_color(Bars, pixel_n, Color):
    def set_bar_color(Bar, start, n, Color):
        if isinstance(Color, (list, tuple)):
            color = Color
        else:
            color = Color.value

        for i in range(start, start + n):
            Bar[f"r{i}"].value = color[0]
            Bar[f"g{i}"].value = color[1]
            Bar[f"b{i}"].value = color[2]

    if 0 <= pixel_n <= 7:
        set_bar_color(Bars[0], pixel_n, 1, Color)
    elif 8 <= pixel_n <= 15:
        set_bar_color(Bars[1], pixel_n - 8, 1, Color)


def set_bar_master(Bar, value):
    factor = value / 255.0
    for i in range(8):
        Bar[f"r{i}"].value *= factor
        Bar[f"g{i}"].value *= factor
        Bar[f"b{i}"].value *= factor
