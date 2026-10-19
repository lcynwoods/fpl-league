"""Pick a large categorical palette and check it for colour-vision deficiency.

sRGB -> linear -> OKLab for perceptual distance, plus Vienot/Brettel
dichromat simulation for protanopia, deuteranopia and tritanopia. The
point is to measure separation rather than eyeball it.
"""
import colorsys, json, math

def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

def hex_to_lin(h):
    h = h.lstrip("#")
    return [srgb_to_linear(int(h[i:i+2], 16) / 255) for i in (0, 2, 4)]

def lin_to_hex(rgb):
    return "#" + "".join("%02x" % round(linear_to_srgb(c) * 255) for c in rgb)

def oklab(lin):
    r, g, b = lin
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l, m, s = l ** (1/3), m ** (1/3), s ** (1/3)
    return (0.2104542553*l + 0.7936177850*m - 0.0040720468*s,
            1.9779984951*l - 2.4285922050*m + 0.4505937099*s,
            0.0259040371*l + 0.7827717662*m - 0.8086757660*s)

# Vienot et al. dichromat simulation, in linear RGB via LMS
RGB2LMS = ((0.31399022, 0.63951294, 0.04649755),
           (0.15537241, 0.75789446, 0.08670142),
           (0.01775239, 0.10944209, 0.87256922))
LMS2RGB = (( 5.47221206, -4.64196010,  0.16963708),
           (-1.12524190,  2.29317094, -0.16789520),
           ( 0.02980165, -0.19318073,  1.16364789))

def mul(mat, v):
    return [sum(mat[i][j] * v[j] for j in range(3)) for i in range(3)]

def simulate(lin, kind):
    L, M, S = mul(RGB2LMS, lin)
    if kind == "protan":
        L = 1.05118294 * M - 0.05116099 * S
    elif kind == "deutan":
        M = 0.95130920 * L + 0.04866992 * S
    else:
        S = -0.86744736 * L + 1.86727089 * M
    return [max(0.0, min(1.0, c)) for c in mul(LMS2RGB, [L, M, S])]

def de(a, b):
    """OKLab distance x100, the scale the guidance uses."""
    la, lb = oklab(a), oklab(b)
    return 100 * math.sqrt(sum((x - y) ** 2 for x, y in zip(la, lb)))

def contrast(lin, surface):
    def lum(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = lum(lin) + 0.05, lum(surface) + 0.05
    return max(a, b) / min(a, b)

def report(hexes, surface_hex, label):
    lins = [hex_to_lin(h) for h in hexes]
    surf = hex_to_lin(surface_hex)
    worst = {}
    for kind in ("normal", "protan", "deutan", "tritan"):
        sims = lins if kind == "normal" else [simulate(c, kind) for c in lins]
        pairs = [(de(sims[i], sims[j]), hexes[i], hexes[j])
                 for i in range(len(sims)) for j in range(i + 1, len(sims))]
        worst[kind] = min(pairs)
    lo = min(contrast(c, surf) for c in lins)
    print("  %-6s n=%d  worst pair: normal %.1f | protan %.1f | deutan %.1f | tritan %.1f"
          % (label, len(hexes), worst["normal"][0], worst["protan"][0],
             worst["deutan"][0], worst["tritan"][0]))
    print("         min contrast vs surface %.2f:1   closest normal pair %s/%s"
          % (lo, worst["normal"][1], worst["normal"][2]))
    return worst
