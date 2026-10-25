"""Perfect bonded C3D10 surfaces through common-refinement variational mortar.

No coordinate adjustment, nearest-node tie, artificial spring or extrapolation.
Primary/secondary order is explicit and nonmatching traces retain every midside.
"""
from collections import defaultdict
import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree
from .simulation_eigen import check_cancel

MAX_CONSTRAINT_ENTRIES=10_000_000


def triangle_basis(point):
    r,s=point;b=np.array([1-r-s,r,s]);g=np.array([[-1.,-1.],[1.,0.],[0.,1.]])
    n=np.r_[b*(2*b-1),[4*b[i]*b[j] for i,j in [(0,1),(1,2),(2,0)]]]
    d=np.vstack([(4*b[:,None]-1)*g,[4*(b[i]*g[j]+b[j]*g[i]) for i,j in [(0,1),(1,2),(2,0)]]])
    return n,d


class SurfaceProjector:
    def __init__(self,system,region):
        triangles=system.raw_mesh['surfaces'].get(region)
        if not triangles:raise ValueError(f"Bond region {region} requires complete quadratic boundary triangles")
        self.nodes=np.array(triangles);self.xyz=np.array([[system.mesh.nodes[n] for n in t] for t in triangles])
        controls=self.xyz.copy()
        for j,(a,b) in enumerate([(0,1),(1,2),(2,0)]):controls[:,j+3]=2*self.xyz[:,j+3]-(self.xyz[:,a]+self.xyz[:,b])/2
        self.lo=controls.min(axis=1);self.hi=controls.max(axis=1);self.centers=(self.lo+self.hi)/2
        self.radius=float(np.linalg.norm((self.hi-self.lo)/2,axis=1).max());self.tree=cKDTree(self.centers)



class BondedSurfaces:
    def __init__(self,system,cancel=None):
        self.system=system;self.relations={};self.records=[];self.groups=[];self.order=[];self.patches=[];self.matrices=[]
        active=[i for i in system.study.interfaces if i.kind!='disabled']
        extent=max(float(np.linalg.norm(np.ptp(system.xyz,axis=0))),1e-12)
        for interface in active:
            check_cancel(cancel)
            if interface.kind!='bonded' or interface.formulation!='quadratic_surface_mortar':raise ValueError("This adapter requires explicit full-quadratic surface bonds; paired penalties/separation/thermal interfaces are not reinterpreted")
            primary=system.regions.get(interface.primary_region_id);secondary=system.regions.get(interface.secondary_region_id)
            if primary is None or secondary is None or primary.body_id==secondary.body_id:raise ValueError("Bond regions require existing distinct body ownership")
            from .simulation_mortar import mortar_map
            tolerance=min(interface.position_tolerance_mm,extent*1e-10)
            mapping,audit,patches,pairs,d,b=mortar_map(system,primary.id,secondary.id,tolerance,cancel)
            nodes=sorted(mapping);maximum=audit['maximum_geometry_bound_mm'];affine=0.
            for n,weights in mapping.items():
                if n in self.relations:raise ValueError("A secondary bond node belongs to multiple interfaces; repair the dependent-side selection")
                if n in weights:raise ValueError("A bond cannot constrain a node to itself")
                if abs(sum(weights.values())-1)>1e-11:raise ValueError("Mortar weights do not preserve rigid translation")
                reproduced=sum(w*np.array(system.mesh.nodes[m]) for m,w in weights.items())
                defect=float(np.linalg.norm(reproduced-system.mesh.nodes[n]));affine=max(affine,defect/extent)
                if defect>tolerance:raise ValueError("Mortar projection does not preserve rigid rotation")
            self.groups.extend(pairs)
            self.patches.append(patches)
            self.matrices.append((d,b))
            self.relations.update(mapping)
            self.records.append({'id':interface.id,'formulation':interface.formulation,'source':interface.source,'primary_region_id':primary.id,'secondary_region_id':secondary.id,
                'primary_body_id':primary.body_id,'secondary_body_id':secondary.body_id,'secondary_nodes':nodes,'maximum_projection_error_mm':maximum,
                'declared_position_tolerance_mm':interface.position_tolerance_mm,'rigid_mapping_tolerance_mm':tolerance,'maximum_affine_reproduction_error':affine,'coordinate_adjustment':False,
                'interpolation':'variational common-refinement full six-node quadratic traces, including signed weights; no extrapolation or finite spring','mortar':audit})
        visiting=set();done=set()
        def visit(n):
            if n in done:return
            if n in visiting:raise ValueError("Cyclic dependent bond constraints require an independently reduced interface graph; no arbitrary constraint is removed")
            visiting.add(n)
            for m in self.relations[n]:
                if m in self.relations:visit(m)
            visiting.remove(n);done.add(n);self.order.append(n)
        for n in self.relations:visit(n)

    def transformation(self,case,cancel=None):
        system=self.system;fixed=set()
        for support in case.supports:
            if support.kind not in {'fixed','partial'}:raise ValueError("Bonded quadratic supports require fixed/world-axis partial components")
            for i in system.region_nodes(support.region_id):
                n=system.ids[i]
                axes=(True,True,True) if support.kind=='fixed' else support.components
                for axis,active in enumerate(axes):
                    if active:
                        if n in self.relations:raise ValueError("Secondary bond DOFs also have a direct support; move the support to the independent side or reverse the interface explicitly")
                        fixed.add(3*i+axis)
        # Every surface patch has noncollinear coverage and reproduces affine
        # motion. Therefore its two solid rigid spaces are identical. Grouping
        # these components is exact for the six global rigid motions, not a
        # substitute for a spring or an artificial support.
        parent=list(range(len(system.solid_components)))
        def find(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for a,b in self.groups:parent[find(a)]=find(b)
        groups=defaultdict(set)
        for i,nodes in enumerate(system.solid_components):groups[find(i)].update(nodes)
        for nodes in groups.values():
            xyz=np.array([system.mesh.nodes[n] for n in nodes]);center=xyz.mean(axis=0);length=max(float(np.linalg.norm(np.ptp(xyz,axis=0))),1e-12);rows=[]
            for n in nodes:
                for axis in range(3):
                    if 3*system.index[n]+axis in fixed:
                        x,y,z=(np.array(system.mesh.nodes[n])-center)/length;rotation=np.array([[0,z,-y],[-z,0,x],[y,-x,0]])
                        rows.append(np.r_[np.eye(3)[axis],rotation[axis]])
            if len(rows)<6 or np.linalg.matrix_rank(np.array(rows),tol=1e-9)<6:raise ValueError("Bonded assembly has unrestrained rigid motion; repair each complete load path's supports")
        independent=[3*i+a for i,n in enumerate(system.ids) if n not in self.relations for a in range(3) if 3*i+a not in fixed]
        if not independent:raise ValueError("Bonded assembly has no independent mechanical DOFs")
        columns={d:j for j,d in enumerate(independent)};expansions={};count=0
        for n in self.order:
            check_cancel(cancel)
            weights=defaultdict(float)
            for m,w in self.relations[n].items():
                for target,v in expansions.get(m,{m:1.}).items():weights[target]+=w*v
            expansions[n]={m:w for m,w in weights.items() if abs(w)>1e-14};count+=len(expansions[n])*3
            if count>MAX_CONSTRAINT_ENTRIES:raise ValueError("Bond constraint expansion exceeds its explicit sparse-entry budget")
        r=[];c=[];v=[]
        for i,n in enumerate(system.ids):
            for m,w in expansions.get(n,{n:1.}).items():
                for a in range(3):
                    d=3*system.index[m]+a
                    if d not in fixed:r.append(3*i+a);c.append(columns[d]);v.append(w)
        t=sparse.coo_matrix((v,(r,c)),shape=(3*len(system.ids),len(independent))).tocsr()
        return t,fixed

    def constraint_forces(self,force,displacement):
        self._displacement=np.asarray(displacement).reshape(-1,3)
        system=self.system;pending=np.asarray(force).reshape(-1,3).copy();multipliers={}
        for n in reversed(self.order):
            value=pending[system.index[n]].copy();multipliers[n]=value
            for m,w in self.relations[n].items():pending[system.index[m]]+=w*value
        reports=[]
        for record,patches in zip(self.records,self.patches):
            jump=amplitude=surface_area=0.
            for sn,mn,ns,nm,measure in patches:
                a=ns@self._displacement[[system.index[int(n)] for n in sn]];b=nm@self._displacement[[system.index[int(n)] for n in mn]]
                jump+=float(np.einsum('qi,qi,q->',a-b,a-b,measure));amplitude+=float(np.einsum('qi,qi,q->',a,a,measure)+np.einsum('qi,qi,q->',b,b,measure));surface_area+=float(measure.sum())
            primary=defaultdict(lambda:np.zeros(3));secondary={n:multipliers[n] for n in record['secondary_nodes']}
            for n,value in secondary.items():
                for m,w in self.relations[n].items():primary[m]-=w*value
            resultant=sum(secondary.values(),np.zeros(3));moment=sum((np.cross(system.mesh.nodes[n],f) for n,f in secondary.items()),np.zeros(3))
            net=resultant+sum(primary.values(),np.zeros(3));net_moment=moment+sum((np.cross(system.mesh.nodes[n],f) for n,f in primary.items()),np.zeros(3))
            reports.append(record|{'trace_jump_rms_mm':float(np.sqrt(jump/max(surface_area,1e-300))),'relative_trace_jump':float(np.sqrt(jump/max(amplitude,1e-300))), 'secondary_resultant_n':resultant.tolist(),'secondary_moment_n_mm':moment.tolist(),'action_reaction_error_n':float(np.linalg.norm(net)),
                'action_reaction_moment_error_n_mm':float(np.linalg.norm(net_moment)),
                'secondary_nodal_forces_n':{n:v.tolist() for n,v in secondary.items()},'primary_nodal_forces_n':{n:v.tolist() for n,v in primary.items()}})
        return reports,pending.ravel()
