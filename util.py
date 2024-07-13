import math


def clamp(x, min_value, max_value):
    return min(max(min_value, x), max_value)


def norm_distance(p1, p2, x_limit, y_limit):
    np1 = (
        p1[0] / x_limit[1],
        p1[1] / y_limit[1],
    )
    np2 = (
        p2[0] / x_limit[1],
        p2[1] / y_limit[1],
    )
    return math.sqrt((np2[0] - np1[0]) ** 2 + (np2[1] - np1[1]) ** 2)


def inside(p1, rect):
    x = rect[0] <= p1[0] <= rect[1]
    y = rect[2] <= p1[1] <= rect[3]
    return x and y


def valid(*objs):
    return all([obj is not None and not getattr(obj, "deleted", False) for obj in objs])


def beats_to_seconds(beat, tempo):
    return beat * (1.0 / tempo) * 60.0


def seconds_to_beats(s, tempo):
    return s * (1.0 / 60.0) * tempo
