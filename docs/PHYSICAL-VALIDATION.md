# Physical correlation protocol

Status: prepared protocol; no specimen measurements have been supplied. This
document is an implementation deliverable, not evidence of a physical pass.

## General record

Before a test, archive the exact revision, STEP checksum, study signature, input
package, process/material lot and manufacturing orientation. Record equipment,
calibration dates/uncertainties, temperature, fixture dimensions/compliance and the
as-manufactured dimensions. Choose output quantities, operating envelope, allowable
prediction errors and measurement uncertainty before seeing the measurements.
Keep calibration specimens separate from validation specimens.

For each observation retain load/time, measured response, units, sensor location
and frame, uncertainty, repeated-trial ID, raw data and anomalies. Compare the same
location and support/load conditions in simulation. Do not fit material stiffness
to a test and then claim that same specimen independently validates the model.

## Machined bracket and printed coupons

1. Select an actual task bracket and material/process supported by the current
   adapter. Record material source and measured dimensions; use printed coupons
   in multiple orientations only when the corresponding material model is available.
2. Model fixture contact and compliance explicitly; a fixed-face idealization
   needs a measured/stated basis. Define the load-application region and whether
   the force remains fixed in the global frame.
3. Place displacement sensors at the predefined study probe points. Measure
   unloaded offset, apply a declared sequence of loads and unload. Retain each
   trial's force-displacement curve and any residual deformation/slip.
4. Compare against mesh-refined predictions and their uncertain-input envelope.
   Separate fixture, dimensional and material discrepancies. Report held-out
   specimen error and applicability, not a universal success percentage.

## Modal correlation

Record fixtures, accelerometer mass/location/direction and excitation. Extract
frequencies and mode shapes, pairing modes by shape correlation rather than sorted
frequency alone. Retain raw time histories/FRFs, damping estimates and uncertainty.
Do not equate arbitrary FE eigenvector magnitude with measured vibration amplitude.

## Enclosure thermal correlation

Record actual electrical dissipation, ambient temperature, sensor contact/location,
thermal interfaces and stabilization criterion. A conduction-only study requires
measured/prescribed boundary temperatures; it does not validate fan-driven cooling.
Compare steady component/probe temperatures and total heat rejection. Airflow/CHT
validation follows only when the corresponding adapter and boundary model exist.

## Future branch protocols

Articulated payloads additionally need measured actuator constants, friction,
gearbox/contact compliance, trajectories and torque/thermal duty. Impact needs
initial conditions, contact/material rate data and measured force/motion/energy.
Molding needs the actual material grade, tool/cavity, gates, machine/process history,
thermal/shrinkage data and measured fill/defect/warpage outcomes. These branches
remain open in `SIMULATION.md`; the present protocol does not claim their support.

## Evidence states

Store measured records with a test ID and the immutable study signature. A report
must identify whether a case is numerical-only, calibrated, independently validated
within a stated envelope, inconclusive or outside that envelope. Missing physical
measurements always remain `not_performed`.
