# Prop guard for Piper (Holybro X500 V2, 10 inch props)

`propguard-x500-10in.stl` is one guard. **Print four.**

Generated from `scripts/propguard.py` parameters, not measured off the aircraft.
Check the two fits below before printing all four.

## Check these first, on one test print

1. **Clamp fit.** The clamp snaps over a **16 mm** carbon arm, 260 degrees of
   wrap, 0.2 mm clearance. Print just the clamp end first if you want to be sure.
2. **Prop height.** The ring band sits **4 mm below to 11 mm above** the bottom
   of the blades, assuming the blades sit **34 mm above the top of the arm tube**.
   That assumption is the one number nobody measured. If your prop sits outside
   the 26 to 42 mm range, the band will not line up with the blades: measure it
   and the model can be regenerated in under a minute.

## Dimensions

| | |
|---|---|
| Ring inner radius | 137 mm (127 mm blade radius plus 10 mm clearance) |
| Ring wall | 1.6 mm (four perimeters at a 0.4 mm nozzle) |
| Ring band height | 15 mm |
| Footprint | 277 x 277 x 60 mm |
| Material per guard | about 38 cm3, roughly 47 g in PLA |
| All four | about 190 g added to the aircraft |

190 g on a 1.5 kg aircraft costs perhaps 10 percent of flight time. For a short
indoor demonstration that is a good trade.

## Printing

- **Orientation:** rotate 180 degrees about X so the **ring sits flat on the bed**
  and the clamp points up. The ring then prints as a tall thin wall, which is
  strong in the direction that matters (radial impact).
- **Supports:** on, touching build plate only. They are needed under the clamp.
- **Perimeters:** 4. **Infill:** 15 percent. **Layer height:** 0.28 to 0.32 mm.
- PLA is fine for a bumper. PETG survives repeated strikes better if you have it.
- Expect roughly 1 to 1.5 hours per guard.

## Fitting

The clamp goes on the arm about 105 mm inboard of the motor shaft, with the ring
centred on the propeller. Snap it over the tube and add one zip tie around the
clamp so vibration cannot walk it loose.

**After fitting all four, re-check that the aircraft still hovers level and that
nothing rubs.** Added mass and drag change the trim slightly.

## What this guard is and is not

It stops a blade reaching a hand or a wall at low speed. It is not a cage: it
does not cover above or below the propeller, and a determined impact will break
PLA. It reduces harm, it does not remove the need for distance from people.
