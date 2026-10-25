"""Independent scikit-fem/Shapely common-refinement bonded elasticity.

No Oxygen numerical imports: separate bulk/forms, GEOS supermesh intersection,
finite-element trace interpolation, mortar assembly and constraint reduction.
"""
import sys,json,hashlib
from pathlib import Path
import numpy as np
import scipy,skfem,shapely
from scipy import sparse
from scipy.sparse.linalg import splu
from skfem import MeshTri1,MeshTri2,ElementTriP2,ElementVector,ElementTetP2,Basis,FacetBasis,asm,LinearForm,BilinearForm,Functional
from skfem.helpers import dot
from skfem.models.elasticity import linear_elasticity,lame_parameters
from skfem.supermeshing import intersect,elementwise_quadrature
from reference_fem import geometry


@Functional
def area(w):return 1.+0*w.x[0]


@BilinearForm
def pairing(u,v,w):return w.metric*u*v


def trace_mesh(mesh,region,axes):
    nodes={int(n):p for n,p in mesh['nodes'].items()};triangles=np.array(mesh['surfaces'][region]);vertices=sorted(set(triangles[:,:3].ravel()));index={n:i for i,n in enumerate(vertices)}
    first=MeshTri1(np.array([nodes[n] for n in vertices])[:,axes].T,np.array([[index[n] for n in t[:3]] for t in triangles]).T,sort_t=False)
    result=MeshTri2.from_mesh(first);dofs={n:int(result.dofs.nodal_dofs[0,i]) for i,n in enumerate(vertices)};edges={}
    for t in triangles:
        for (a,b),n in zip([(0,1),(1,2),(2,0)],t[3:]):edges[tuple(sorted((int(t[a]),int(t[b]))))]=int(n)
    for i,(a,b) in enumerate(result.facets.T):dofs[edges[tuple(sorted((vertices[a],vertices[b])))]]=int(result.dofs.facet_dofs[0,i])
    xyz=np.zeros((result.doflocs.shape[1],3))
    for n,d in dofs.items():xyz[d]=nodes[n]
    return result,dofs,xyz


def run(request):
    data=request['mesh'];nodes={int(n):p for n,p in data['nodes'].items()};ids=sorted(nodes);lookup={n:i for i,n in enumerate(ids)};size=3*len(ids)
    force=np.zeros(size);rows=[];cols=[];values=[]
    for body,material in request['materials'].items():
        elements=[e for e,b in zip(data['elements'],request['element_bodies']) if b==body];used={n for e in elements for n in e}
        local={'nodes':{n:nodes[n] for n in used},'elements':elements,'surfaces':{k:v for k,v in data['surfaces'].items() if request['region_bodies'][k]==body}}
        mesh,_,_,index,_,mid,select=geometry(local);basis=Basis(mesh,ElementVector(ElementTetP2()),intorder=6)
        dofs={n:basis.nodal_dofs[:,index[n]] if n in index else basis.edge_dofs[:,mid[n]] for n in used};mapping=np.empty(basis.N,dtype=int)
        for n,d in dofs.items():mapping[d]=3*lookup[n]+np.arange(3)
        k=asm(linear_elasticity(*lame_parameters(material['youngs_modulus_mpa'],material['poisson_ratio'])),basis).tocoo()
        rows.extend(mapping[k.row]);cols.extend(mapping[k.col]);values.extend(k.data)
        for load in request['loads']:
            if request['region_bodies'][load['region_id']]!=body:continue
            fb=FacetBasis(mesh,ElementVector(ElementTetP2()),facets=select(load['region_id']),intorder=6);traction=np.array(load['vector'])/float(asm(area,fb))
            @LinearForm
            def applied(v,w):return dot(traction,v)
            np.add.at(force,mapping,asm(applied,fb))
    k=sparse.coo_matrix((values,(rows,cols)),shape=(size,size)).tocsr()
    interface=request['interface'];axes=request['chart_axes'];primary,pd,pxyz=trace_mesh(data,interface['primary_region_id'],axes);secondary,sd,sxyz=trace_mesh(data,interface['secondary_region_id'],axes)
    common,pi,si=intersect(primary,secondary)
    pb=Basis(primary,ElementTriP2(),elements=pi,quadrature=elementwise_quadrature(primary,common,pi,intorder=6))
    sb=Basis(secondary,ElementTriP2(),elements=si,quadrature=elementwise_quadrature(secondary,common,si,intorder=6))
    gradient=np.array([sb.interpolate(sxyz[:,a]).grad for a in range(3)])
    metric=np.linalg.norm(np.cross(gradient[:,0].transpose(1,2,0),gradient[:,1].transpose(1,2,0)),axis=2)
    d=asm(pairing,sb,metric=metric).tocsc();b=asm(pairing,pb,sb,metric=metric).tocsc();h=splu(d).solve(b.toarray())
    inverse_primary={d:n for n,d in pd.items()};relations={n:{inverse_primary[j]:float(w) for j,w in enumerate(h[i]) if abs(w)>1e-14} for n,i in sd.items()}
    fixed={3*lookup[int(n)]+axis for n,mask in request['node_constraints'].items() for axis,active in enumerate(mask) if active}
    independent=[3*i+a for i,n in enumerate(ids) if n not in relations for a in range(3) if 3*i+a not in fixed];columns={d:i for i,d in enumerate(independent)}
    tr=[];tc=[];tv=[]
    for i,n in enumerate(ids):
        for m,w in relations.get(n,{n:1.}).items():
            for axis in range(3):
                target=3*lookup[m]+axis
                if target not in fixed:tr.append(3*i+axis);tc.append(columns[target]);tv.append(w)
    t=sparse.coo_matrix((tv,(tr,tc)),shape=(size,len(independent))).tocsr();reduced=(t.T@k@t).tocsc();u=np.asarray(t@splu(reduced).solve(t.T@force)).ravel();r=k@u-force
    residual=float(np.linalg.norm(t.T@r)/max(np.linalg.norm(t.T@force),1e-300))
    return {'displacements_mm':{n:u.reshape(-1,3)[i].tolist() for i,n in enumerate(ids)},'strain_energy_n_mm':float(.5*u@(k@u)),'equation_residual':residual,
        'mortar_projection_residual':float(np.linalg.norm(d@h-b.toarray())/np.linalg.norm(b.toarray())),
        'supermesh_elements':len(common.t.T),'software':{'scikit_fem':skfem.__version__,'shapely':shapely.__version__,'geos':shapely.geos_version_string,'numpy':np.__version__,'scipy':scipy.__version__},
        'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Independent linear isotropic surface-mortar numerical reference, not physical interface calibration'}


if __name__=='__main__':Path(sys.argv[2]).write_text(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())),indent=2,allow_nan=False)+'\n')
