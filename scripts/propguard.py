"""Generate a printable prop guard for the Holybro X500 V2 (10 inch props).

One part per arm, four needed. The guard is a full ring that surrounds the
propeller, carried on a chord beam and two posts that drop to a snap-on clamp
for the 16 mm carbon arm.

Geometry, all in mm, origin at the motor shaft axis, +X pointing outboard along
the arm, Z=0 at the bottom of the propeller blades:

  ring        inner radius 137 (10 in prop = 127 radius, plus 10 clearance)
  band        15 tall, from -4 to +11, so the blades stay inside it even if the
              measured prop height is off by up to ~8 mm
  clamp       snaps over the 16 mm tube, 260 degree wrap, 3 mm wall
  PROP_H      top of the arm tube to the bottom of the blades (measure this)

The mesh is emitted as several closed shells in one STL. Slicers union
overlapping shells, which is what we want here.
"""
import math
import numpy as np
from stl import mesh as stlmesh

# ---- parameters ----------------------------------------------------------- #
PROP_DIAM = 254.0          # 10 inch
CLEARANCE = 10.0           # gap between blade tip and ring
RING_WALL = 1.6            # 4 perimeters at a 0.4 nozzle
BAND_BOTTOM = -4.0
BAND_TOP = 11.0
PROP_H = 34.0              # top of arm tube -> bottom of blades  (ASSUMPTION)
TUBE_D = 16.0
CLAMP_WALL = 3.0
CLAMP_LEN = 30.0
CLAMP_WRAP = 260.0         # degrees of tube wrapped; the rest is the snap gap
CLAMP_X = -105.0           # clamp centre, inboard of the motor axis
CHORD_ANGLE = 40.0         # ring attachment points, degrees either side of inboard
CHORD_W = 3.0
POST_W = 8.0
POST_T = 5.0
SEG = 96                   # facets per full circle

RING_IN = PROP_DIAM / 2.0 + CLEARANCE
RING_OUT = RING_IN + RING_WALL
TUBE_R = TUBE_D / 2.0
TUBE_Z = -(PROP_H + TUBE_R)          # arm tube centre height
CLAMP_OUT = TUBE_R + CLAMP_WALL


# ---- mesh helpers --------------------------------------------------------- #
def _tris_from_quads(quads):
    out = []
    for a, b, c, d in quads:
        out.append((a, b, c))
        out.append((a, c, d))
    return out


def ring_sector_z(r_in, r_out, z0, z1, a0=0.0, a1=360.0, segments=SEG, cx=0.0, cy=0.0):
    """Annular sector extruded along Z, as a closed shell."""
    n = max(3, int(round(segments * (a1 - a0) / 360.0)))
    angs = [math.radians(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
    quads = []
    for i in range(n):
        t0, t1 = angs[i], angs[i + 1]
        p = lambda r, t, z: (cx + r * math.cos(t), cy + r * math.sin(t), z)
        # outer wall, inner wall, top, bottom
        quads.append((p(r_out, t0, z0), p(r_out, t1, z0), p(r_out, t1, z1), p(r_out, t0, z1)))
        quads.append((p(r_in, t1, z0), p(r_in, t0, z0), p(r_in, t0, z1), p(r_in, t1, z1)))
        quads.append((p(r_in, t0, z1), p(r_out, t0, z1), p(r_out, t1, z1), p(r_in, t1, z1)))
        quads.append((p(r_out, t0, z0), p(r_in, t0, z0), p(r_in, t1, z0), p(r_out, t1, z0)))
    if a1 - a0 < 359.9:                      # cap the two open ends
        for t, flip in ((angs[0], False), (angs[-1], True)):
            p = lambda r, z: (cx + r * math.cos(t), cy + r * math.sin(t), z)
            q = (p(r_in, z0), p(r_out, z0), p(r_out, z1), p(r_in, z1))
            quads.append(q[::-1] if flip else q)
    return _tris_from_quads(quads)


def ring_sector_x(r_in, r_out, x0, x1, a0, a1, segments=SEG, cy=0.0, cz=0.0):
    """Annular sector whose axis runs along X (the arm clamp)."""
    n = max(3, int(round(segments * (a1 - a0) / 360.0)))
    angs = [math.radians(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
    quads = []
    for i in range(n):
        t0, t1 = angs[i], angs[i + 1]
        p = lambda r, t, x: (x, cy + r * math.cos(t), cz + r * math.sin(t))
        quads.append((p(r_out, t0, x0), p(r_out, t1, x0), p(r_out, t1, x1), p(r_out, t0, x1)))
        quads.append((p(r_in, t1, x0), p(r_in, t0, x0), p(r_in, t0, x1), p(r_in, t1, x1)))
        quads.append((p(r_in, t0, x1), p(r_out, t0, x1), p(r_out, t1, x1), p(r_in, t1, x1)))
        quads.append((p(r_out, t0, x0), p(r_in, t0, x0), p(r_in, t1, x0), p(r_out, t1, x0)))
    for t, flip in ((angs[0], False), (angs[-1], True)):
        p = lambda r, x: (x, cy + r * math.cos(t), cz + r * math.sin(t))
        q = (p(r_in, x0), p(r_out, x0), p(r_out, x1), p(r_in, x1))
        quads.append(q[::-1] if flip else q)
    return _tris_from_quads(quads)


def beam(p0, p1, width, z0, z1):
    """Vertical-walled beam between two XY points, as a closed shell."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    ux, uy = -dy / L * width / 2.0, dx / L * width / 2.0
    c = [(x0 + ux, y0 + uy), (x1 + ux, y1 + uy), (x1 - ux, y1 - uy), (x0 - ux, y0 - uy)]
    quads = []
    for i in range(4):
        a, b = c[i], c[(i + 1) % 4]
        quads.append(((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)))
    quads.append(((c[0][0], c[0][1], z1), (c[1][0], c[1][1], z1),
                  (c[2][0], c[2][1], z1), (c[3][0], c[3][1], z1)))
    quads.append(((c[3][0], c[3][1], z0), (c[2][0], c[2][1], z0),
                  (c[1][0], c[1][1], z0), (c[0][0], c[0][1], z0)))
    return _tris_from_quads(quads)


def box(xc, yc, zc, sx, sy, sz):
    return beam((xc - sx / 2.0, yc), (xc + sx / 2.0, yc), sy, zc - sz / 2.0, zc + sz / 2.0)


# ---- the part ------------------------------------------------------------- #
tris = []

# 1. the ring around the propeller
tris += ring_sector_z(RING_IN, RING_OUT, BAND_BOTTOM, BAND_TOP)

# 2. chord beam across the inboard side, tying the ring to the posts
a = math.radians(180.0 - CHORD_ANGLE)
b = math.radians(180.0 + CHORD_ANGLE)
pa = (RING_OUT * math.cos(a), RING_OUT * math.sin(a))
pb = (RING_OUT * math.cos(b), RING_OUT * math.sin(b))
tris += beam(pa, pb, CHORD_W, BAND_BOTTOM, BAND_TOP)

# 3. two posts from the clamp up to the ring band
for px in (CLAMP_X - 10.0, CLAMP_X + 10.0):
    tris += box(px, 0.0, (TUBE_Z + CLAMP_OUT + BAND_TOP) / 2.0,
                POST_W, POST_T, (BAND_TOP - (TUBE_Z + CLAMP_OUT)))

# 4. short beam joining the post tops into the chord (stiffens the mount)
tris += beam((CLAMP_X - 14.0, 0.0), (CLAMP_X + 14.0, 0.0), POST_T, BAND_BOTTOM, BAND_TOP)
chord_mid = ((pa[0] + pb[0]) / 2.0, 0.0)
tris += beam((CLAMP_X, 0.0), chord_mid, CHORD_W, BAND_BOTTOM, BAND_TOP)

# 5. snap-on clamp for the 16 mm arm tube, opening downward
half = CLAMP_WRAP / 2.0
tris += ring_sector_x(TUBE_R + 0.2, CLAMP_OUT, CLAMP_X - CLAMP_LEN / 2.0,
                      CLAMP_X + CLAMP_LEN / 2.0, 90.0 - half, 90.0 + half, cz=TUBE_Z)

data = np.zeros(len(tris), dtype=stlmesh.Mesh.dtype)
for i, t in enumerate(tris):
    data["vectors"][i] = np.array(t, dtype=np.float32)
m = stlmesh.Mesh(data)
out = r"C:\Users\fahim\OneDrive\Desktop\ASQI-DRONE\docs\propguard-x500-10in.stl"
m.save(out)

mins = m.vectors.reshape(-1, 3).min(axis=0)
maxs = m.vectors.reshape(-1, 3).max(axis=0)
print("wrote", out)
print("triangles:", len(tris))
print("bounding box mm: X %.1f..%.1f  Y %.1f..%.1f  Z %.1f..%.1f"
      % (mins[0], maxs[0], mins[1], maxs[1], mins[2], maxs[2]))
print("footprint: %.0f x %.0f x %.0f mm" % (maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]))
ring_v = 2 * math.pi * ((RING_IN + RING_OUT) / 2) * RING_WALL * (BAND_TOP - BAND_BOTTOM)
chord_v = math.dist(pa, pb) * CHORD_W * (BAND_TOP - BAND_BOTTOM)
post_v = 2 * POST_W * POST_T * (BAND_TOP - (TUBE_Z + CLAMP_OUT))
clamp_v = (math.pi * (CLAMP_OUT ** 2 - (TUBE_R + 0.2) ** 2)) * (CLAMP_WRAP / 360.0) * CLAMP_LEN
brace_v = (28.0 * POST_T + abs(CLAMP_X - chord_mid[0]) * CHORD_W) * (BAND_TOP - BAND_BOTTOM)
vol = ring_v + chord_v + post_v + clamp_v + brace_v
print("volume: %.1f cm3  -> about %.0f g in PLA, %.0f g for four" % (vol / 1000.0, vol / 1000.0 * 1.24, 4 * vol / 1000.0 * 1.24))
