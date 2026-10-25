"""Accepted constitutive/force state and its full tetrahedral bulk operator.

The native material tensor is evaluated before output conversion. Exact binding
of its candidate displacement to the accepted displacement is required. This
operator does not include changing contact projections or certify stability.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import numpy as np
from scipy import sparse
from .quadratic import weights_and_gradients
from .simulation_native_tangent import _integer, _finite
from .simulation_solver_profile import file_hash

MAX_BYTES = 536870912
MAX_DOFS = 12000
MAX_ENTRIES = 8000000


def read_bulk_frame(directory, frame):
    directory=Path(directory);name=frame.get('file')
    if not isinstance(name,str) or not re.fullmatch(r'analysis\.accepted-bulk-[0-9]{8,16}\.bin',name):
        raise ValueError('Unsafe accepted bulk filename')
    path=directory/name;size=_integer(frame.get('bytes'),'bulk file size',1,67108864)
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=size:
        raise ValueError('Missing, unsafe or truncated accepted bulk file')
    with path.open('rb') as stream:
        line=stream.readline(2049)
        if len(line)>2048 or not line.endswith(b'\n'):raise ValueError('Invalid accepted bulk header')
        h=json.loads(line)
        if h.get('schema')!=1 or h.get('layout')!='constitutive-force-state-v2':raise ValueError('Unsupported accepted bulk layout')
        for key in ('step','increment','iteration'):
            if h.get(key)!=frame.get(key):raise ValueError('Accepted bulk frame identity differs from its payload')
        nodes=_integer(h.get('nodes'),'bulk nodes',1,100000);stride=_integer(h.get('stride'),'bulk node stride',4,4)
        elements=_integer(h.get('elements'),'bulk elements',1,10000);points=_integer(h.get('points'),'bulk points',1,40000)
        states=_integer(h.get('state_count'),'bulk state count',0,64)
        if h.get('integer_bytes') not in (4,8) or h.get('real_bytes')!=8 or h.get('byte_order') not in ('little','big'):
            raise ValueError('Unsupported accepted bulk numeric ABI')
        flags=h.get('iperturb')
        if not isinstance(flags,list) or len(flags)!=2 or any(type(x) is not int for x in flags) or flags[0] not in (2,3) or flags[1] not in (0,1):
            raise ValueError('Unsupported accepted bulk kinematic flags')
        times=_finite(np.array([h.get('evaluation_time'),h.get('increment_duration'),frame.get('accepted_time')],dtype=float))
        if times[1]<=0 or abs(times[0]-times[2])>1e-10*max(abs(times[2]),1.):raise ValueError('Accepted bulk evaluation time mismatch')
        order='<' if h['byte_order']=='little' else '>';integer=np.dtype(order+'i'+str(h['integer_bytes']));real=np.dtype(order+'f8')
        expected=(3*stride*nodes+points*(39+3*states)+3*nodes)*8+14*elements*integer.itemsize
        if len(line)+expected!=size:raise ValueError('Accepted bulk payload size differs from its declared layout')
        payload=stream.read(expected+1)
        if len(payload)!=expected:raise ValueError('Truncated accepted bulk payload')
    cursor=0
    def take(count,dtype):
        nonlocal cursor
        values=np.frombuffer(payload,dtype=dtype,count=count,offset=cursor);cursor+=count*dtype.itemsize;return values
    candidate=_finite(take(stride*nodes,real).reshape(nodes,stride)[:,1:4])
    previous=_finite(take(stride*nodes,real).reshape(nodes,stride)[:,1:4])
    force=_finite(take(stride*nodes,real).reshape(nodes,stride)[:,1:4])
    records=take(14*elements,integer).reshape(elements,14)
    if np.any(records[:,0]<=0) or len(set(records[:,0]))!=elements or np.any(np.diff(records[:,0])<=0):
        raise ValueError('Invalid or duplicate accepted bulk element labels')
    if np.any(~np.isin(records[:,1],[4,10])) or not np.array_equal(records[:,2],np.where(records[:,1]==10,4,1)) or int(records[:,2].sum())!=points:
        raise ValueError('Unsupported accepted bulk quadrature or connectivity')
    if np.any(~np.isin(records[:,3],[2,-51])):raise ValueError('Unsupported accepted bulk material law')
    if np.any(records[:,3]==-51) and states<13:raise ValueError('Incomplete accepted plastic internal history')
    for record in records:
        used=record[4:4+record[1]]
        if len(set(used))!=record[1] or np.any(used<1) or np.any(used>nodes) or np.any(record[4+record[1]:]):
            raise ValueError('Invalid accepted bulk element connectivity')
    fields={}
    for key,width in [('tangent',21),('stress',6),('initial_stress',6),('initial_strain',6),('initial_state',states),('state',states)]:
        fields[key]=_finite(take(points*width,real).reshape(points,width))
    accepted=_finite(take(nodes*3,real).reshape(nodes,3))
    accepted_state=_finite(take(points*states,real).reshape(points,states))
    if not np.array_equal(candidate,accepted):
        raise ValueError('Constitutive candidate displacement differs from accepted state')
    return {'header':h,'records':records,'candidate_displacements_mm':candidate,'accepted_displacements_mm':accepted,
        'accepted_material_state':accepted_state,'material_state_matches_accepted_output':bool(np.array_equal(fields['state'],accepted_state)),
        'previous_displacements_mm':previous,'native_internal_force_n':force,'file_sha256':file_hash(path),**fields}


def native_material_tensor(packed,kind):
    """Native engineering shear order: XX YY ZZ XY XZ YZ; no Kelvin factors."""
    p=_finite(np.asarray(packed,dtype=float));out=np.zeros((6,6))
    if p.shape!=(21,):raise ValueError('Incomplete native material tensor')
    if kind==2:
        e,nu=p[:2]
        if e<=0 or not -1<nu<.5:raise ValueError('Invalid native elastic constants')
        mu=e/(2*(1+nu));lam=e*nu/((1+nu)*(1-2*nu))
        out[:3,:3]=lam;out[np.arange(3),np.arange(3)]+=2*mu;out[np.arange(3,6),np.arange(3,6)]=mu
    elif kind==-51:
        # materialdata_me.f selects native orthotropic-nine packing when its
        # remaining twelve entries are zero within the exact upstream threshold.
        scale=float(np.max(abs(p[:9])))
        if scale>0 and np.all(abs(p[9:])<=scale*1e-10):
            out[:3,:3]=np.array([[p[0],p[1],p[3]],[p[1],p[2],p[4]],[p[3],p[4],p[5]]])
            out[np.arange(3,6),np.arange(3,6)]=p[6:9]
        else:
            k=0
            for column in range(6):
                for row in range(column+1):out[row,column]=out[column,row]=p[k];k+=1
    else:raise ValueError('Unsupported native bulk material kind')
    return out


def element_operator(xyz,displacement,packed,stress,kind,nonlinear,*,material_tensors=None):
    """Total-Lagrangian B^T C B + geometric stress stiffness and native force."""
    xyz=_finite(np.asarray(xyz,dtype=float));u=_finite(np.asarray(displacement,dtype=float));n=len(xyz)
    if xyz.shape!=(n,3) or u.shape!=xyz.shape or n not in (4,10):raise ValueError('Unsupported bulk element geometry')
    if n==10:
        a=.5854101966249685;b=.1381966011250105
        gradients=[weights_and_gradients(np.array([a if j==q else b for j in range(4)])[1:])[1] for q in range(4)]
        weights=[1/24]*4
    else:gradients=[np.array([[-1.,-1.,-1.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])];weights=[1/6]
    if np.shape(packed)!=(len(gradients),21) or np.shape(stress)!=(len(gradients),6):raise ValueError('Incomplete accepted bulk quadrature tensors')
    if material_tensors is not None:
        material_tensors=_finite(np.asarray(material_tensors,dtype=float))
        if material_tensors.shape!=(len(gradients),6,6):raise ValueError('Incomplete material force derivative tensors')
    matrix=np.zeros((3*n,3*n));force=np.zeros((n,3))
    for q,(reference,weight) in enumerate(zip(gradients,weights)):
        jac=(xyz-xyz[:1]).T@reference;det=float(np.linalg.det(jac))
        if det<=0 or not np.isfinite(det):raise ValueError('Nonpositive accepted bulk reference Jacobian')
        g=reference@np.linalg.inv(jac);f=np.eye(3)+u.T@g if nonlinear else np.eye(3)
        if np.linalg.det(f)<=0:raise ValueError('Nonpositive accepted bulk deformation Jacobian')
        c=native_material_tensor(packed[q],kind) if material_tensors is None else material_tensors[q];b=np.zeros((6,n,3))
        for axis in range(3):b[axis]=np.outer(g[:,axis],f[:,axis])
        for row,(a,d) in enumerate([(0,1),(0,2),(1,2)],3):b[row]=np.outer(g[:,d],f[:,a])+np.outer(g[:,a],f[:,d])
        b=b.reshape(6,3*n);s=stress[q];tensor=np.array([[s[0],s[3],s[4]],[s[3],s[1],s[5]],[s[4],s[5],s[2]]]);w=det*weight
        matrix+=(b.T@c@b)*w
        if nonlinear:matrix+=np.kron(g@tensor@g.T,np.eye(3))*w
        force+=g@(f@tensor).T*w
    return _finite(matrix),_finite(force)


def assemble_bulk(system,data,*,material_tensors=None):
    if len(system.ids)*3>MAX_DOFS or len(system.mesh.elements)*len(system.mesh.elements[0].nodes)**2*9>MAX_ENTRIES:
        raise ValueError('Accepted bulk operator exceeds its declared assembly budget')
    records=data['records'];elements={e.id:e for e in system.mesh.elements}
    if set(records[:,0])!=set(elements):raise ValueError('Accepted bulk element coverage differs from analysis mesh')
    rows=[];cols=[];values=[];force=np.zeros_like(system.xyz);offset=0
    for record in records:
        label,nn,nq,kind=map(int,record[:4]);nodes=list(map(int,record[4:4+nn]));element=elements[label]
        if list(element.nodes)!=nodes:raise ValueError('Accepted bulk connectivity differs from analysis mesh')
        indices=np.array([system.index[n] for n in nodes]);section=slice(offset,offset+nq);offset+=nq
        matrix,local_force=element_operator(system.xyz[indices],data['accepted_displacements_mm'][np.array(nodes)-1],data['tangent'][section],data['stress'][section],kind,bool(data['header']['iperturb'][1]),material_tensors=None if material_tensors is None else material_tensors[section])
        dofs=(indices[:,None]*3+np.arange(3)).ravel();rows.extend(np.repeat(dofs,len(dofs)));cols.extend(np.tile(dofs,len(dofs)));values.extend(matrix.ravel());force[indices]+=local_force
    bulk=sparse.coo_matrix((values,(rows,cols)),shape=(3*len(system.ids),)*2).tocsr();bulk.eliminate_zeros()
    return bulk,force


class AcceptedBulkAudit:
    """One-frame cache for exact native state binding and structural operators."""
    def __init__(self,directory,system,case,increments,build):
        from .simulation_solver_profile import contract
        self.directory=Path(directory);self.system=system;self.case=case;self.build=build
        self.elements={e.id:e for e in system.mesh.elements}
        self.frames={};self.rows={};self.cache=None;self.reason=None
        self.path=self.directory/'analysis.accepted-bulk.jsonl'
        if not self.path.exists():self.reason='Native accepted constitutive/force export is unavailable';return
        if len(system.ids)*3>MAX_DOFS or len(system.mesh.elements)*len(system.mesh.elements[0].nodes)**2*9>MAX_ENTRIES:
            self.reason='Accepted bulk operator exceeds its declared assembly budget';return
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size>8*1024*1024:
            raise ValueError('Unsafe accepted bulk manifest')
        expected=[(r['step'],r['increment'],r['total_time']) for r in increments];total=0;serial=0
        with self.path.open() as stream:
            header=json.loads(stream.readline(4097));pinned=contract()
            if header.get('type')!='header' or header.get('schema')!=1 or header.get('upstream')!=pinned['upstream_version'] or header.get('source_sha256')!=pinned['source_sha256'] or header.get('instrumentation')!='accepted-constitutive-force-state-v1':
                raise ValueError('Accepted bulk export source/schema mismatch')
            for i,line in enumerate(stream):
                if len(line)>4096 or not line.endswith('\n'):raise ValueError('Invalid accepted bulk manifest line')
                frame=json.loads(line);identity=tuple(_integer(frame.get(k),k,1,10000000) for k in ('step','increment'))
                time=float(frame.get('accepted_time'));_finite(np.array([time]))
                if i>=len(expected) or identity!=expected[i][:2] or abs(time-expected[i][2])>1e-6*max(abs(time),1.):
                    raise ValueError('Accepted bulk increment coverage differs from STA output')
                if frame.get('type')!='frame' or type(frame.get('available')) is not bool:raise ValueError('Invalid accepted bulk manifest frame')
                if frame['available']:
                    stage=_integer(frame.get('evaluation_serial'),'bulk evaluation serial',1,1000000000)
                    if stage<=serial:raise ValueError('Repeated or reversed accepted bulk evaluation serial')
                    serial=stage;total+=_integer(frame.get('bytes'),'bulk file size',1,67108864)
                    if total>MAX_BYTES:raise ValueError('Accepted bulk evidence exceeds output budget')
                    _integer(frame.get('iteration'),'bulk iteration',1,10000000)
                elif not isinstance(frame.get('reason'),str) or not frame['reason']:raise ValueError('Missing accepted bulk unavailability reason')
                self.frames[identity]=frame
        if len(self.frames)!=len(expected):raise ValueError('Truncated accepted bulk accepted-increment coverage')
        self.order=list(self.frames)

    def get(self,meta):
        identity=(meta['step'],meta['increment'])
        if self.reason:return None
        frame=self.frames.get(identity)
        if frame is None:raise ValueError('Missing accepted bulk frame for contact increment')
        if not frame['available']:
            self.rows[identity]={'step':identity[0],'increment':identity[1],'available':False,'reason':frame['reason']};return None
        if self.cache is not None and self.cache[0]==identity:return self.cache[1:]
        data=read_bulk_frame(self.directory,frame)
        if not data['material_state_matches_accepted_output']:
            raise ValueError('Constitutive candidate material history differs from accepted output state')
        point_offset=0
        for record in data['records']:
            element=self.elements.get(int(record[0]))
            if element is None:raise ValueError('Unknown accepted bulk element')
            material=self.system.assignments[element.body_id]
            plastic=material.plastic_curve and getattr(self.system,'_translated_plastic',True)
            if record[3]!=(-51 if plastic else 2):raise ValueError('Accepted bulk native law differs from translated material')
            if record[3]==2:
                nq=int(record[2]);values=data['tangent'][point_offset:point_offset+nq,:2]
                if not np.allclose(values,[material.youngs_modulus_mpa,material.poisson_ratio],rtol=2e-12,atol=0):
                    raise ValueError('Accepted bulk elastic constants differ from translated material')
            point_offset+=int(record[2])
        matrix,force=assemble_bulk(self.system,data)
        # An accepted native algorithmic tensor is not automatically the exact
        # derivative of its material force law. In particular the pinned finite
        # J2 tensor differs from native stress finite differences. Preserve the
        # operator, but never label an unchecked plastic tangent a verified
        # force Jacobian or silently replace it with an elastic tensor.
        plastic=bool(np.any(data['records'][:,3]==-51))
        derivative_complete=not plastic
        from .simulation_contact_global import matrix_hash
        row=self.rows.get(identity,{})
        row.update({'step':identity[0],'increment':identity[1],'available':True,'accepted_time':frame['accepted_time'],
            'elements':len(data['records']),'points':data['header']['points'],'state_count':data['header']['state_count'],
            'exact_displacement_binding':True,'exact_material_history_binding':True,
            'bulk_force_jacobian_complete':derivative_complete,
            'material_derivative_limit':None if derivative_complete else 'Plastic algorithmic tensors require per-state two-sided material derivative qualification; finite-strain J2 has a measured native tangent discrepancy',
            'geometric_nonlinearity':bool(data['header']['iperturb'][1]),'operator_sha256':matrix_hash(matrix),
            'file':frame['file'],'file_sha256':data['file_sha256']})
        self.rows[identity]=row;self.cache=(identity,matrix,force,data)
        return self.cache[1:]

    def check_force(self,meta,contact_force=None):
        value=self.get(meta)
        if value is None:return None
        matrix,force,data=value
        combined=force+(0. if contact_force is None else contact_force)
        native=data['native_internal_force_n'][np.array(self.system.ids)-1]
        scale=max(float(np.linalg.norm(native)),float(np.linalg.norm(combined)),float(np.linalg.norm(self.system.loads(self.case)))*1e-10,1e-12)
        error=float(np.linalg.norm(combined-native))/scale
        if not np.isfinite(error) or error>1e-7:raise ValueError('Accepted bulk/contact forces differ from native post-correction forces')
        self.rows[(meta['step'],meta['increment'])]['native_internal_force_relative_error']=error
        return error

    def write(self,result,*,has_contact=False):
        from .store import write_json
        if not self.reason:
            for identity in self.order:
                if identity not in self.rows:self.get({'step':identity[0],'increment':identity[1]})
                if not has_contact:self.check_force({'step':identity[0],'increment':identity[1]})
            final=self.frames[self.order[-1]]
            if final['available']:
                value=self.get({'step':final['step'],'increment':final['increment']})
                if value:
                    matrix,force,data=value
                    for n in self.system.ids:
                        if not np.allclose(data['accepted_displacements_mm'][n-1],result['field']['displacements_mm'][n],rtol=1e-5,atol=1e-12):
                            raise ValueError('Accepted bulk displacement differs from final printed output')
                    sparse.save_npz(self.directory/'accepted-bulk-final.npz',matrix)
                    np.savez_compressed(self.directory/'accepted-bulk-final-force.npz',node_ids=np.array(self.system.ids),force_n=force)
        rows=[self.rows[k] for k in self.order] if not self.reason else []
        available=[r for r in rows if r['available']];checked=[r for r in available if 'native_internal_force_relative_error' in r]
        summary={'performed':bool(available),'reason':self.reason,'accepted_frames':len(rows),'state_bound_frames':len(available),
            'all_accepted_states_bound':bool(rows) and len(available)==len(rows),'force_checked_frames':len(checked),
            'force_jacobian_complete_frames':sum(r['bulk_force_jacobian_complete'] for r in available),
            'maximum_native_force_relative_error':max((r['native_internal_force_relative_error'] for r in checked),default=None),
            'full_contact_stability_qualified':False,'accepted_state_tangent_qualified':False,
            'scope':'Exact post-correction/accepted displacement and material-history binding; native Gauss material tensors and geometric stiffness. Frozen contact maps, changing-map derivatives and physical stability remain distinct.'}
        if self.build:summary.update(binary_sha256=self.build['binary_sha256'],receipt_sha256=self.build['receipt_sha256'])
        write_json(self.directory/'accepted-bulk-audit.json',{'schema':1,'summary':summary,'history':rows})
        result['checks']['accepted_bulk_state_audit']=summary
        from .simulation_material_derivatives import audit_final_material
        audit_final_material(self,result)
        if has_contact:
            evidence=json.loads((self.directory/'contact.json').read_text())
            evidence['accepted_bulk_state_audit']=summary
            write_json(self.directory/'contact.json',evidence)
        return summary
