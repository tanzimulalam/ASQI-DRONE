"""Outer-arc prop guard for the Holybro X500 V2 (10 inch props).

Replaces the full-ring guard (propguard.py). That one had two problems: the
inboard half of the ring came within ~113 mm of the airframe centre and hit the
frame mounted on top of the Pixhawk, and its posts stood inside the blade
circle (95-115 mm from the shaft) up to 11 mm above the blade bottom, so the
blades would strike them.

This guard only covers the OUTBOARD half of the propeller. Everything inside
the blade circle stays down at arm-tube height, well under the blades; the
only parts that rise to blade height are the posts and the arc, which are all
outside the blade tips.

Geometry, all in mm, origin at the motor shaft axis, +X pointing outboard
along the arm, Z=0 at the bottom of the propeller blades:

  arc         half circle (-90..+90 deg, outboard side), inner radius 137
  band        15 tall, z -4..+11, with a 5 mm outward lip on top for stiffness
  posts       4, at +/-45 and +/-90 deg, dropping from the band to strut level
  struts      4, at arm-tube height, fanning out from the clamp to the posts
  clamp       snaps over the 16 mm tube from below, 260 degree wrap
  PROP_H      top of the arm tube to the bottom of the blades (measure this)

Run:  python propguard_outer.py      (needs: pip install manifold3d numpy)
Writes docs/propguard-outer-x500-10in.stl, already flipped for printing
(lip down on the bed, clamp up).
"""
import math
import struct
from pathlib import Path

import numpy as np
import manifold3d as m

# ---- parameters ----------------------------------------------------------- #
PROP_DIAM = 254.0          # 10 inch
CLEARANCE = 10.0           # gap between blade tip and band
BAND_WALL = 2.0            # 5 perimeters at a 0.4 nozzle; stiffer than the full ring
BAND_BOTTOM = -4.0
BAND_TOP = 11.0
ARC_SPAN = 90.0            # degrees either side of straight outboard
LIP_W = 5.0                # outward lip along the top edge of the band
LIP_T = 2.0
PROP_H = 34.0              # top of arm tube -> bottom of blades  (ASSUMPTION)
TUBE_D = 16.0
TUBE_FIT = 0.2
CLAMP_WALL = 3.0
CLAMP_LEN = 30.0
CLAMP_WRAP = 260.0         # degrees of tube wrapped; the rest is the snap gap (underneath)
CLAMP_X = -70.0            # clamp centre, inboard of the motor axis, on bare tube
POST_ANGLES = (-90.0, -45.0, 45.0, 90.0)
POST_W = 5.0               # tangential
POST_T = 4.0               # radial
STRUT_W = 5.0
STRUT_T = 4.0
SEG = 192

RING_IN = PROP_DIAM / 2.0 + CLEARANCE
RING_OUT = RING_IN + BAND_WALL
RING_MID = (RING_IN + RING_OUT) / 2.0
TUBE_R = TUBE_D / 2.0
TUBE_Z = -(PROP_H + TUBE_R)             # arm tube centre height
BORE_R = TUBE_R + TUBE_FIT
CLAMP_OUT = BORE_R + CLAMP_WALL
BASE_Z = TUBE_Z - 6.0                   # underside of the struts
STRUT_Z1 = BASE_Z + STRUT_T


def arc_section(r_in, r_out, a0, a1, n=SEG):
    ts = np.radians(np.linspace(a0, a1, max(8, int(n * (a1 - a0) / 360))))
    pts = [(r_out * math.cos(t), r_out * math.sin(t)) for t in ts]
    pts += [(r_in * math.cos(t), r_in * math.sin(t)) for t in ts[::-1]]
    return m.CrossSection([pts])


def slab(cs, z0, z1):
    return cs.extrude(z1 - z0).translate([0, 0, z0])


def polar(r, deg):
    return r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg))


parts = []

# 1. the outboard arc and its stiffening lip
parts.append(slab(arc_section(RING_IN, RING_OUT, -ARC_SPAN, ARC_SPAN), BAND_BOTTOM, BAND_TOP))
parts.append(slab(arc_section(RING_IN, RING_OUT + LIP_W, -ARC_SPAN, ARC_SPAN), BAND_TOP - LIP_T, BAND_TOP))

# 2. posts, outside the blade circle, from strut level up through the band
for a in POST_ANGLES:
    x, y = polar(RING_MID, a)
    post = m.CrossSection.square([POST_T, POST_W], center=True).rotate(a).translate([x, y])
    parts.append(slab(post, BASE_Z, BAND_TOP))

# 3. struts at arm-tube height, clamp -> each post
for a in POST_ANGLES:
    x, y = polar(RING_MID, a)
    y0 = math.copysign(CLAMP_OUT, y)
    strut = m.CrossSection.batch_hull([
        m.CrossSection.circle(STRUT_W / 2, 32).translate([CLAMP_X, y0]),
        m.CrossSection.circle(STRUT_W / 2, 32).translate([x, y]),
    ])
    parts.append(slab(strut, BASE_Z, STRUT_Z1))


# 4. snap-on clamp, drawn in the (y, z) plane and extruded along X
def along_x(cs, x0, length):
    return cs.extrude(length).transform([[0, 0, 1, x0], [1, 0, 0, 0], [0, 1, 0, 0]])


half = CLAMP_WRAP / 2.0
clamp_cs = arc_section(BORE_R, CLAMP_OUT, 90.0 - half, 90.0 + half, 96).translate([0, TUBE_Z])
parts.append(along_x(clamp_cs, CLAMP_X - CLAMP_LEN / 2, CLAMP_LEN))

guard = m.Manifold.batch_boolean(parts, m.OpType.Add)

# keep the tube path and the snap-in gap clear, and flatten the underside
bore = along_x(m.CrossSection.circle(BORE_R, 96).translate([0, TUBE_Z]), -400, 800)
gap = along_x(m.CrossSection([[(0, 0), polar(40, 270 - (360 - CLAMP_WRAP) / 2),
                               polar(40, 270 + (360 - CLAMP_WRAP) / 2)]]).translate([0, TUBE_Z]),
              CLAMP_X - CLAMP_LEN, 2 * CLAMP_LEN)
guard = (guard - bore - gap).trim_by_plane([0, 0, 1], BASE_Z)
assert guard.status() == m.Error.NoError, guard.status()

# ---- safety check: nothing may be inside the blade circle at blade height -- #
blade_zone = m.Manifold.cylinder(60.0, PROP_DIAM / 2 + 3.0, PROP_DIAM / 2 + 3.0, SEG).translate([0, 0, -8.0])
intrusion = (guard ^ blade_zone).volume()
assert intrusion < 1e-6, f"guard intrudes into blade zone: {intrusion:.3f} mm3"


# ---- output --------------------------------------------------------------- #
def write_stl(man, path):
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    with open(path, "wb") as f:
        f.write(b"propguard outer".ljust(80, b"\0"))
        f.write(struct.pack("<I", len(t)))
        rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3),
                                      ("c", "<f4", 3), ("attr", "<u2")])
        rec["n"], rec["a"], rec["b"], rec["c"] = n, a, b, c
        f.write(rec.tobytes())


printable = guard.rotate([180, 0, 0])                 # lip down on the bed, clamp up
bb = printable.bounding_box()
printable = printable.translate([-(bb[0] + bb[3]) / 2, -(bb[1] + bb[4]) / 2, -bb[2]])
out = Path(__file__).resolve().parent.parent / "docs" / "propguard-outer-x500-10in.stl"
write_stl(printable, out)

bb = guard.bounding_box()
print("wrote", out)
print("bounding box mm: X %.1f..%.1f  Y %.1f..%.1f  Z %.1f..%.1f" % (bb[0], bb[3], bb[1], bb[4], bb[2], bb[5]))
print("footprint: %.0f x %.0f x %.0f mm" % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]))
vol = guard.volume() / 1000.0
print("volume: %.1f cm3 -> about %.0f g in PLA, %.0f g for four" % (vol, vol * 1.24, 4 * vol * 1.24))
print("closest point to the motor axis at blade height: %.1f mm (blade tip at %.1f)"
      % (RING_IN, PROP_DIAM / 2))
print("genus", guard.genus(), " shells", len(guard.decompose()))
