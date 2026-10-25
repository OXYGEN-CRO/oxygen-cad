"""Independent scikit-fem elastic buckling; imports no Oxygen numerics.

Neutral C3D10 geometry conversion and independent static assembly are from
reference_fem; all derivatives, stresses and geometric forms use scikit-fem.
"""
import hashlib,json,sys
from pathlib import Path
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import eigsh,splu,LinearOperator
from skfem import Basis,ElementTetP2,ElementVector,BilinearForm,asm
from skfem.helpers import sym_grad,trace,eye
from skfem.models.elasticity import linear_elasticity,lame_parameters
from reference_fem import geometry,run as static_reference


def run(request):
    source=request["static_request"];reference=static_reference(source)
    mesh,nodes,_,index,_,mid_edge,select=geometry(source["mesh"])
    order=source["quadrature_order"]
    basis=Basis(mesh,ElementVector(ElementTetP2()),intorder=order)
    dofs={n:(basis.nodal_dofs[:,index[n]] if n in index else basis.edge_dofs[:,mid_edge[n]]) for n in nodes}
    u=np.zeros(basis.N)
    for n,d in dofs.items():u[d]=reference["field"]["displacements"][n]
    material=source["material"];lam,mu=lame_parameters(material["youngs_modulus_mpa"],material["poisson_ratio"])
    stiffness=asm(linear_elasticity(lam,mu),basis)
    strain=sym_grad(basis.interpolate(u));stress=2*mu*strain+lam*eye(trace(strain),3)
    @BilinearForm
    def geometric(u,v,w):return np.einsum("ia...,ab...,ib...->...",u.grad,w.stress,v.grad)
    kg=asm(geometric,basis,stress=stress)
    fixed=np.unique(np.concatenate([basis.get_dofs(facets=select(source["aliases"][f"fixed_{i}"])).all() for i in range(len(source["study"]["fixed_faces"]))]))
    free=np.setdiff1d(np.arange(basis.N),fixed)
    k=stiffness[free][:,free].tocsc();b=-kg[free][:,free].tocsc()
    ks=float(k.diagonal().max());bs=float(sparse.linalg.norm(b));a=k/ks;factor=splu(a)
    count=request["mode_count"]+4
    mu,vectors=eigsh(b/bs,k=count,M=a,Minv=LinearOperator(a.shape,matvec=factor.solve),which="LA",tol=1e-12,maxiter=20000,
        ncv=min(len(free),max(2*count+1,40)),v0=np.random.default_rng(5).standard_normal(len(free)))
    permutation=np.argsort(mu)[::-1];mu=mu[permutation]
    if np.any(mu<=0):raise ValueError("Independent buckling window contains nonpositive reciprocal factors")
    factors=ks/(bs*mu);fields=np.zeros((basis.N,count));fields[free]=vectors[:,permutation]
    elastic=stiffness@fields;geometric=(kg@fields)*factors[None,:]
    residual=np.linalg.norm((elastic+geometric)[free],axis=0)/np.maximum(np.linalg.norm(elastic[free],axis=0),np.linalg.norm(geometric[free],axis=0))
    shapes=[]
    for j in range(count):
        values={n:fields[d,j].tolist() for n,d in dofs.items()};scale=max(np.linalg.norm(v) for v in values.values())
        shapes.append({n:(np.array(v)/scale).tolist() for n,v in values.items()})
    return {"load_factors":factors.tolist(),"modes":shapes,"eigen_residual":float(residual.max()),
        "reference_static":reference,"quadrature_order":order,
        "scope":"Independent ideal linearized buckling under declared dead load; no physical collapse validation",
        "adapter_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__=="__main__":
    Path(sys.argv[2]).write_text(json.dumps(run(json.loads(Path(sys.argv[1]).read_text())),indent=2,allow_nan=False)+"\n")
