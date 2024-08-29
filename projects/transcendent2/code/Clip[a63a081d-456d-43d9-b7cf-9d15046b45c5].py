Par12Dimmer.set(Scale(Par12Dimmer.value, 0, 127, 0, 255))
Par34Dimmer.set(Scale(Par34Dimmer.value, 0, 127, 0, 255))
Bar12Dimmer.set(Scale(Bar12Dimmer.value, 0, 127, 0, 255))
Bar34Dimmer.set(Scale(Bar34Dimmer.value, 0, 127, 0, 255))
ADJDimmer.set(Scale(ADJDimmer.value, 0, 127, 0, 255))
Manual1.set(Scale(Manual1.value, 0, 127, 0, 255))
Manual2.set(Scale(Manual2.value, 0, 127, 0, 255))
Manual3.set(Scale(Manual3.value, 0, 127, 0, 255))
Manual4.set(Scale(Manual4.value, 0, 127, 0, 255))
Manual5.set(Scale(Manual5.value, 0, 127, 0, 255))
GlobalDimmer.set(Scale(GlobalDimmer.value, 0, 127, 0, 255))
DecayDimmer.set(Scale(DecayDimmer.value, 0, 127, 0, 255))


if OSCEnabled.value:
    Par12Dimmer.set(Scale(OSCFader1.value, 0.0, 1.0, 0, 255))
    Par34Dimmer.set(Scale(OSCFader2.value, 0.0, 1.0, 0, 255))
    Bar12Dimmer.set(Scale(OSCFader5.value, 0.0, 1.0, 0, 255))
    Bar34Dimmer.set(Scale(OSCFader6.value, 0.0, 1.0, 0, 255))
    ADJDimmer.set(Scale(OSCFader3.value, 0.0, 1.0, 0, 255))
    GlobalDimmer.set(Scale(OSCFader7.value, 0.0, 1.0, 0, 255))
    temp = Scale(OSCFader4.value, 0.0, 1.0, 0.2, 0.9)
    DecayDimmer.set(Scale(temp, 0.0, 1.0, 0, 255))


def toint255(array):
    return [int(n * 255) for n in array]


if OSCColorEnabled.value:
    color1 = toint255(colorsys.hls_to_rgb(1 - OSCFader8.value, 0.5, 1.0))
    color2 = toint255(colorsys.hls_to_rgb(1 - OSCFader9.value, 0.5, 1.0))
    color3 = toint255(colorsys.hls_to_rgb(1 - OSCFader10.value, 0.5, 1.0))
    color4 = toint255(colorsys.hls_to_rgb(1 - OSCFader11.value, 0.5, 1.0))

    f = 255
    Color1.set(color1 + [255])
    Color2.set(color2 + [255])
    Color3.set(color3 + [255])
    Color4.set(color4 + [255])
