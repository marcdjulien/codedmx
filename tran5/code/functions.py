def set_par_color(Par, Color):
    if isinstance(Color, (list, tuple)):
        color = Color
    else:
        color = Color.value
    Par.red.value = color[0]
    Par.green.value = color[1]
    Par.blue.value = color[2]