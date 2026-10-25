"""Resolve independent dead-load multipliers without inferring missing schedules.

Schema validation imports only standard-library code. Numerical imports occur
only when assembling a worker's spatial load bases.
"""
from __future__ import annotations
import hashlib
import math


def has_history(case):
    return bool(case.history or any(load.history for load in case.loads))


def resolved_histories(case):
    independent=any(load.history for load in case.loads)
    if independent:
        if not case.history and any(not load.history for load in case.loads):
            raise ValueError('Independent loads require a history for every load or an explicit shared case history')
        end=case.history[-1][0] if case.history else next(load.history[-1][0] for load in case.loads if load.history)
        if any(load.history and load.history[-1][0]!=end for load in case.loads):
            raise ValueError('Independent load histories must end at the same declared case time; no extrapolation')
    return {load.id:load.history or case.history or [(0.,0.),(1.,1.)] for load in case.loads}


class LoadPath:
    def __init__(self,system,case,*,event_duration=None):
        import numpy as np
        self.system=system;self.case=case;self.curves=resolved_histories(case)
        self.independent=any(load.history for load in case.loads)
        if event_duration is None:
            if any(curve[0]!=(0.,0.) for curve in self.curves.values()) or (case.history and case.history[0]!=(0.,0.)):
                raise ValueError('Nonlinear load history must start at time zero and zero multiplier')
            self.duration=case.history[-1][0] if case.history else next(iter(self.curves.values()))[-1][0] if self.curves else 1.
        else:
            self.duration=event_duration
            if case.loads and not has_history(case):raise ValueError('Event loads require an explicit time multiplier history')
            if any(curve[-1][0]<event_duration for curve in self.curves.values()) or (case.history and case.history[-1][0]<event_duration):
                raise ValueError('Load history does not cover the entire event')
        self.times=sorted({0.,self.duration,*[t for curve in self.curves.values() for t,_ in curve if t<=self.duration]})
        if not self.curves and case.history:self.times=sorted({t for t,_ in case.history if t<=self.duration}|{self.duration})
        if len(case.loads)*len(system.ids)*3>20_000_000:
            raise ValueError('Independent nodal load bases exceed 20 million values; partition the declared study')
        # Strip temporal data only for this spatial integration, never for a solve.
        self.bases={load.id:system.loads(case.model_copy(update={'history':[],
            'loads':[load.model_copy(update={'history':[]})]})) for load in case.loads}
        self.reference_force=sum(self.bases.values(),np.zeros(3*len(system.ids)))
        self.active_dofs=np.logical_or.reduce([value!=0 for value in self.bases.values()]) if self.bases else np.zeros_like(self.reference_force,dtype=bool)
        self.force_scale_n=sum(float(np.linalg.norm(values.reshape(-1,3),axis=1).sum())*max(abs(v) for _,v in self.curves[name]) for name,values in self.bases.items())

    def factors(self,time):
        import numpy as np
        epsilon=8*math.ulp(self.duration)
        if not math.isfinite(time) or time < -epsilon or time>self.duration+epsilon:
            raise ValueError('Load evaluation lies outside the declared history; no extrapolation')
        time=min(max(time,0.),self.duration)
        return {name:float(np.interp(time,[p[0] for p in curve],[p[1] for p in curve])) for name,curve in self.curves.items()}

    def at(self,time):
        import numpy as np
        factors=self.factors(time)
        return sum((factors[name]*values for name,values in self.bases.items()),np.zeros_like(self.reference_force))

    def output_time(self,time):
        """Resolve printed endpoint rounding without extrapolating a load curve.

        DAT/STA times have fewer digits than the deck/state exporter. Preserve
        raw output times; only the load evaluation snaps to an unambiguous knot.
        """
        tolerance=max(abs(time),self.duration)*1e-6
        matches=[value for value in self.times if abs(value-time)<=tolerance]
        if len(matches)>1:raise ValueError('Load phases are ambiguous at solver printed-time precision')
        return matches[0] if matches else time

    def shared_factor(self,time):
        if self.independent:return None
        import numpy as np
        curve=self.case.history or [(0.,0.),(1.,1.)]
        return float(np.interp(time,[p[0] for p in curve],[p[1] for p in curve]))

    def audit(self):
        import numpy as np
        return {'schema':1,'time_knots':self.times,'duration':self.duration,'independent_loads':self.independent,
            'time_convention':'physical seconds for events; rate-independent load parameter for nonlinear static',
            'force_scale_n':self.force_scale_n,'loads':{load.id:{'source':load.source,'history':self.curves[load.id],
                'history_origin':'load' if load.history else 'case' if self.case.history else 'default nonlinear ramp',
                'spatial_force_sha256':hashlib.sha256(np.asarray(self.bases[load.id],dtype='<f8').tobytes()).hexdigest(),
                'reference_resultant_n':self.bases[load.id].reshape(-1,3).sum(axis=0).tolist(),
                'reference_moment_n_mm':np.cross(self.system.xyz,self.bases[load.id].reshape(-1,3)).sum(axis=0).tolist()}
                for load in self.case.loads}}
