"""Independent scikit-fem reference for the exact C3D10 mesh and study inputs.

Runs in an optional, separately pinned verification environment. It imports no
Oxygen assembly, boundary integration, interpolation, or solver routines.
Supported reference problems: isotropic static elasticity, unloaded modes and steady conduction.
"""
from __future__ import annotations
import hashlib
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import scipy
import skfem
from skfem import MeshTet1,MeshTet2,ElementTetP2,ElementVector,Basis,FacetBasis,Functional,LinearForm,BilinearForm,asm,condense,solve
from skfem.models.elasticity import linear_elasticity,lame_parameters
from skfem.models.poisson import laplace
from skfem.helpers import dot


def geometry(data):
    nodes={int(n):np.asarray(p,dtype=float) for n,p in data['nodes'].items()}
    if not all(p.shape==(3,) and np.isfinite(p).all() for p in nodes.values()):raise ValueError('Reference nodes must be finite 3D points')
    cells=np.asarray(data['elements'],dtype=np.int64)
    if cells.ndim!=2 or cells.shape[1]!=10:raise ValueError('Reference adapter requires C3D10')
    vertices=sorted(map(int,set(cells[:,:4].ravel())));index={n:i for i,n in enumerate(vertices)}
    coarse=np.array([[index[n] for n in e[:4]] for e in cells]).T
    first=MeshTet1(np.array([nodes[n] for n in vertices]).T,coarse,sort_t=False)
    mesh=MeshTet2.from_mesh(first)
    node_dofs={n:int(mesh.dofs.nodal_dofs[0,i]) for i,n in enumerate(vertices)}
    edge_nodes={}
    # CalculiX ordering, expressed only as the source format conversion. All
    # reference interpolation, derivatives and integration come from scikit-fem.
    for e in cells:
        for (a,b),n in zip(((0,1),(1,2),(2,0),(0,3),(1,3),(2,3)),e[4:]):
            key=tuple(sorted((int(e[a]),int(e[b]))))
            previous=edge_nodes.setdefault(key,int(n))
            if previous!=n:raise ValueError('Nonconforming source edge')
    mid_edge={}
    for i,(a,b) in enumerate(mesh.edges.T):
        n=edge_nodes[tuple(sorted((int(vertices[a]),int(vertices[b]))))]
        node_dofs[n]=int(mesh.dofs.edge_dofs[0,i]);mid_edge[n]=i
    if set(node_dofs)!=set(nodes):raise ValueError('Source nodes and reference geometry DOFs differ')
    positions=mesh.doflocs.copy()
    for n,dof in node_dofs.items():positions[:,dof]=nodes[n]
    mesh=replace(mesh,doflocs=positions)
    facets={tuple(sorted(map(int,facet))):i for i,facet in enumerate(mesh.facets.T)}
    boundary=set(map(int,mesh.boundary_facets()))
    def select(name):
        triangles=data['surfaces'][name]
        selected=np.array([facets[tuple(sorted(index[int(n)] for n in tri[:3]))] for tri in triangles],dtype=np.int32)
        if len(set(selected))!=len(selected) or not set(selected).issubset(boundary):raise ValueError('Reference selection is duplicated or not an exterior boundary')
        return selected
    return mesh,nodes,vertices,index,node_dofs,mid_edge,select


@Functional
def unit_area(w):return 1.+0*w.x[0]


def run(request):
    data=request['mesh'];study=request['study'];material=request['material'];aliases=request['aliases']
    if study['type'] not in ('linear_static','modal','thermal'):raise ValueError('Independent adapter supports only linear static, unloaded modal and steady thermal references')
    if material.get('plastic_curve'):raise ValueError('Reference adapter refuses to silently linearize plasticity')
    mesh,nodes,vertices,index,node_dofs,mid_edge,select=geometry(data)
    order=int(request.get('quadrature_order',2))
    if order not in (2,4,6):raise ValueError('Reference quadrature order must be 2, 4 or 6')
    mechanical=study['type'] in ('linear_static','modal')
    element=ElementVector(ElementTetP2()) if mechanical else ElementTetP2()
    basis=Basis(mesh,element,intorder=order)
    def region(role):return select(aliases[role])
    def facet(role):return FacetBasis(mesh,element,facets=region(role),intorder=6)
    if mechanical:
        dofs={n:(basis.nodal_dofs[:,index[n]] if n in index else basis.edge_dofs[:,mid_edge[n]]) for n in nodes}
    else:dofs={n:np.array([d]) for n,d in node_dofs.items()}
    for n,d in dofs.items():
        if not np.allclose(basis.doflocs[:,d],nodes[n][:,None],rtol=0,atol=1e-10):raise ValueError('Reference geometry and solution DOF locations differ')
    force=np.zeros(basis.N);areas={}
    if mechanical:
        if not study['fixed_faces']:raise ValueError('Reference mechanical setup requires explicit supports')
        if study['type']=='linear_static' and not study['forces']:raise ValueError('Reference static setup requires explicit loads')
        if study['type']=='modal' and study['forces']:raise ValueError('Reference modal setup refuses applied loads')
        E=float(material['youngs_modulus_mpa']);nu=float(material['poisson_ratio'])
        if not np.isfinite([E,nu]).all() or E<=0 or not -1<nu<.5:raise ValueError('Invalid reference elastic properties')
        fixed=np.unique(np.concatenate([basis.get_dofs(facets=region(f'fixed_{i}')).all() for i in range(len(study['fixed_faces']))]))
        stiffness=asm(linear_elasticity(*lame_parameters(E,nu)),basis)
        for i,load in enumerate(study['forces']):
            fb=facet(f'load_{i}');area=float(asm(unit_area,fb));areas[f'load_{i}']=area
            traction=np.array(load['vector_n'])/area
            @LinearForm
            def uniform(v,w):return dot(traction,v)
            force+=asm(uniform,fb)
        if study['type']=='modal':
            from scipy.sparse.linalg import eigsh
            density=float(material['density_g_cm3'])*1e-9
            if not np.isfinite(density) or density<=0:raise ValueError('Reference modal density must be positive')
            @BilinearForm
            def inertia(u,v,w):return density*dot(u,v)
            mass=asm(inertia,basis)
            free=np.setdiff1d(np.arange(basis.N),fixed)
            count=int(study['mode_count'])+4
            if not 5<=count<=24 or count>=len(free):raise ValueError('Invalid reference eigenmode count')
            eigenvalues,vectors=eigsh(stiffness[free][:,free],k=count,M=mass[free][:,free],sigma=0,which='LM',tol=1e-10,
                                     v0=np.linspace(.1,1,len(free)))
            permutation=np.argsort(eigenvalues);eigenvalues=eigenvalues[permutation]
            if np.any(eigenvalues<=0):raise ValueError('Nonpositive reference elastic eigenvalue')
            values=np.zeros((basis.N,count));values[free]=vectors[:,permutation]
            elastic=stiffness @ values;inertial=(mass @ values)*eigenvalues[None,:]
            equation_error=float(np.max(np.linalg.norm((elastic-inertial)[free],axis=0)/np.maximum(np.linalg.norm(elastic[free],axis=0)+np.linalg.norm(inertial[free],axis=0),1e-30)))
            result={'frequencies_hz':(np.sqrt(eigenvalues)/(2*np.pi)).tolist(),
                    'modes':[{n:values[d,i].tolist() for n,d in dofs.items()} for i in range(count)],
                    'mass_quadrature_order':order,'eigen_residual':equation_error,
                    'amplitude':'arbitrary generalized eigenvector normalization'}
        else:
            values=solve(*condense(stiffness,force,D=fixed))
            displacements={n:values[d].tolist() for n,d in dofs.items()}
            loads={n:force[d].tolist() for n,d in dofs.items()}
            residual=stiffness @ values-force
            fixed_set=set(map(int,fixed))
            reactions={n:residual[d].tolist() for n,d in dofs.items() if all(int(v) in fixed_set for v in d)}
            applied=np.sum(list(loads.values()),axis=0);reacted=np.sum(list(reactions.values()),axis=0)
            moment=np.sum([np.cross(nodes[n],v) for n,v in loads.items()],axis=0)+np.sum([np.cross(nodes[n],v) for n,v in reactions.items()],axis=0)
            scale=max(sum(np.linalg.norm(v) for v in loads.values()),1e-12)
            result={'displacements':displacements,'equivalent_loads_n':loads,'support_reactions_n':reactions,
                    'strain_energy_n_mm':float(.5*values @ (stiffness @ values)),'max_displacement_mm':max(float(np.linalg.norm(v)) for v in displacements.values()),
                    'force_balance_error':float(np.linalg.norm(applied+reacted)/scale),
                    'moment_balance_error':float(np.linalg.norm(moment)/(scale*max(np.linalg.norm(np.ptp(mesh.p,axis=1)),1e-12)))}
    else:
        if study['fixed_faces'] or study['forces']:raise ValueError('Thermal reference does not consume mechanical supports/forces')
        conductivity=float(material['thermal_conductivity_w_m_k'])
        if not np.isfinite(conductivity) or conductivity<=0:raise ValueError('Invalid conductivity')
        stiffness=conductivity*asm(laplace,basis)
        prescribed={}
        for i,boundary in enumerate(study['temperatures']):
            for dof in basis.get_dofs(facets=region(f'temperature_{i}')).all():
                if int(dof) in prescribed and prescribed[int(dof)]!=boundary['temperature_c']:raise ValueError('Incompatible prescribed temperatures')
                prescribed[int(dof)]=boundary['temperature_c']
        convection=study.get('convection',[]);radiation=study.get('radiation',[])
        if not prescribed and not convection and not radiation:raise ValueError('Thermal reference requires temperature-based heat rejection')
        fixed=np.array(sorted(prescribed),dtype=int);known=np.zeros(basis.N)
        for d,t in prescribed.items():known[d]=t
        for i,source in enumerate(study['heat_sources']):
            fb=facet(f'heat_{i}');area=float(asm(unit_area,fb));areas[f'heat_{i}']=area
            flux=source['power_w']*1000/area
            @LinearForm
            def power(v,w):return flux*v
            force+=asm(power,fb)
        if study.get('internal_heat_w') is not None:
            volume=float(asm(unit_area,basis));generation=study['internal_heat_w']*1000/volume
            @LinearForm
            def internal(v,w):return generation*v
            force+=asm(internal,basis)
        source_force=force.copy();exchange=[]
        for kind,boundaries in [('convection',convection),('radiation',radiation)]:
            for i,boundary in enumerate(boundaries):
                fb=FacetBasis(mesh,element,facets=region(f'{kind}_{i}'),intorder=14)
                ambient=boundary['ambient_temperature_c']
                if kind=='convection':
                    coefficient=boundary['coefficient_w_m2_k']*1e-3
                    @BilinearForm
                    def robin(u,v,w):return coefficient*u*v
                    @LinearForm
                    def environment(v,w):return coefficient*ambient*v
                    stiffness=stiffness+asm(robin,fb);force+=asm(environment,fb)
                else:
                    from scipy.constants import Stefan_Boltzmann
                    coefficient=boundary['emissivity']*Stefan_Boltzmann*1e-3
                exchange.append((kind,fb,coefficient,ambient))
        free=np.setdiff1d(np.arange(basis.N),fixed)
        def equations(values,tangent=False):
            residual=stiffness @ values-force;matrix=stiffness.copy() if tangent else None
            for kind,fb,c,ambient in exchange:
                if kind!='radiation':continue
                field=fb.interpolate(values)
                if np.min(field)+273.15<=0:raise ValueError('Reference radiation reaches nonpositive absolute temperature')
                @LinearForm
                def radiative(v,w):return c*((w.temperature+273.15)**4-(ambient+273.15)**4)*v
                residual+=asm(radiative,fb,temperature=field)
                if tangent:
                    @BilinearForm
                    def derivative(u,v,w):return 4*c*(w.temperature+273.15)**3*u*v
                    matrix=matrix+asm(derivative,fb,temperature=field)
            return residual,matrix
        if radiation:
            from scipy.sparse.linalg import spsolve
            seeds=list(prescribed.values())+[b['ambient_temperature_c'] for b in convection+radiation]
            values=np.full(basis.N,float(np.mean(seeds)))
            values[fixed]=known[fixed]
            for iteration in range(80):
                residual,tangent=equations(values,True)
                if np.linalg.norm(residual[free])<1e-10*max(np.linalg.norm(force),1e-12):break
                delta=np.zeros(basis.N);delta[free]=spsolve(tangent[free][:,free].tocsc(),-residual[free])
                accepted=False
                for fraction in (1.,.5,.25,.125,.0625,.03125,.015625,.0078125,.00390625,.001953125,.0009765625):
                    candidate=values+fraction*delta
                    if candidate.min()+273.15<=0:continue
                    trial,_=equations(candidate)
                    if np.linalg.norm(trial[free])<np.linalg.norm(residual[free]):values=candidate;accepted=True;break
                if not accepted:raise RuntimeError('Independent radiation Newton line search failed')
            else:raise RuntimeError('Independent radiation Newton solve did not converge')
        else:values=solve(*condense(stiffness,force,x=known,D=fixed))
        residual,_=equations(values)
        rejected=0.
        for kind,fb,c,ambient in exchange:
            @Functional
            def out(w):return c*(w.temperature-ambient) if kind=='convection' else c*((w.temperature+273.15)**4-(ambient+273.15)**4)
            rejected+=float(asm(out,fb,temperature=fb.interpolate(values)))
        scale=max(float(np.sum(np.abs(source_force))),float(np.sum(np.abs(residual[fixed]))),abs(rejected),1e-12)
        result={'temperature_c':{n:float(values[d[0]]) for n,d in dofs.items()},
                'equivalent_heat_load_n_mm_s':{n:float(source_force[d[0]]) for n,d in dofs.items()},
                'heat_rejected_w':rejected/1000,'prescribed_heat_w':float(residual[fixed].sum())/1000,
                'heat_balance_error':float(abs(source_force.sum()+residual[fixed].sum()-rejected)/scale)}
    free=np.setdiff1d(np.arange(basis.N),fixed)
    if study['type']!='modal':
        equation_error=float(np.linalg.norm(residual[free])/max(np.linalg.norm(force),np.linalg.norm(stiffness @ values),1e-12))
    if not np.isfinite(values).all() or equation_error>1e-7:raise RuntimeError('Independent solve has incomplete/nonfinite output or an excessive equation residual')
    package=Path(skfem.__file__).parent
    package_hash=hashlib.sha256()
    for p in sorted(package.rglob('*.py')):package_hash.update(str(p.relative_to(package)).encode()+b'\0'+p.read_bytes())
    return {'schema_version':1,'type':study['type'],'nodes':len(nodes),'elements':len(data['elements']),
            'quadrature_order':order,'cell_quadrature_points':int(basis.X.shape[1]),'boundary_quadrature_order':6,'areas_mm2':areas,
            'equation_residual':equation_error,'field':result,
            'software':{'name':'scikit-fem','version':skfem.__version__,'package_sources_sha256':package_hash.hexdigest(),'numpy':np.__version__,'scipy':scipy.__version__,'python':platform.python_version(),'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            'scope':'Independent assembly on the same geometry/material/BC idealization; numerical agreement is not physical validation'}


if __name__=='__main__':
    request=Path(sys.argv[1]);output=Path(sys.argv[2])
    result=run(json.loads(request.read_text()))
    result['request_sha256']=hashlib.sha256(request.read_bytes()).hexdigest()
    output.write_text(json.dumps(result,allow_nan=False,indent=2)+'\n')
