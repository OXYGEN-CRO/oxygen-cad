"""Conversions for the canonical mm/N/s/tonne mechanical system.

F = m*a fixes the mass unit: N*s²/mm = 1000 kg = one tonne.
1 g/cm³ = 1000 kg/m³ = 1e-9 tonne/mm³. By contrast, a fluid
density already entered in kg/m³ converts by 1e-12. Do not mix them.
CalculiX's manual, section 3 (Units), uses the same consistent system:
https://www.dhondt.de/ccx_2.10.pdf
"""

GRAMS_PER_TONNE = 1_000_000.0


def density_tonne_mm3(density_g_cm3: float) -> float:
    return density_g_cm3 * 1e-9


def fluid_density_tonne_mm3(density_kg_m3: float) -> float:
    return density_kg_m3 * 1e-12
