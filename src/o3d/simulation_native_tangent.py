"""Audit the actual external Newton linear system, with separate accepted state.

This is a solver-equation diagnostic. A final Newton assembly need not be the
accepted constitutive/contact state, and a solved system is no stability proof.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from scipy import sparse
from .simulation_solver_profile import contract, file_hash
from .store import write_json

LAYOUT = 'newton-rhs-correction-accepted-v1'
MAX_BYTES = 536870912
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'Invalid native Newton {name}')
    return value


def _finite(values):
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite native Newton data')
    return values


def read_linearization(directory, frame):
    """Read one committed native-ABI export, rejecting incomplete/unsafe files."""
    directory = Path(directory)
    name = frame.get('file')
    if not isinstance(name, str) or not re.fullmatch(r'analysis\.native-tangent-[0-9]{8,16}\.bin', name):
        raise ValueError('Unsafe native Newton filename')
    path = directory / name
    size = _integer(frame.get('bytes'), 'file size', 1, MAX_BYTES)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError('Missing, unsafe or truncated native Newton file')
    with path.open('rb') as stream:
        line = stream.readline(2049)
        if len(line) > 2048 or not line.endswith(b'\n'):
            raise ValueError('Invalid native Newton binary header')
        header = json.loads(line)
        if header.get('schema') != 1 or header.get('layout') != LAYOUT:
            raise ValueError('Unsupported native Newton binary layout')
        for key in ('step', 'increment', 'iteration'):
            if header.get(key) != frame.get(key):
                raise ValueError('Native Newton frame identity differs from binary')
        n = _integer(header.get('equations'), 'equation count', 1, 30000)
        nodes = _integer(header.get('nodes'), 'node count', 1, 100000)
        stride = _integer(header.get('stride'), 'node stride', 4, 8)
        entries = _integer(header.get('off_diagonal'), 'sparse count', 0, 8000000)
        asym = _integer(header.get('asymmetric'), 'asymmetry flag', 0, 1)
        if header.get('integer_bytes') not in (4, 8) or header.get('real_bytes') != 8 or header.get('byte_order') not in ('little', 'big'):
            raise ValueError('Unsupported native Newton numeric ABI')
        order = '<' if header['byte_order'] == 'little' else '>'
        integer = np.dtype(order + 'i' + str(header['integer_bytes']))
        real = np.dtype(order + 'f8')
        expected = 8 * (3*n + (1+asym)*entries + stride*nodes + 3*nodes) + integer.itemsize * (n+1+entries+stride*nodes)
        if len(line) + expected != size:
            raise ValueError('Native Newton payload size differs from its declared layout')
        payload = stream.read(expected + 1)
        if len(payload) != expected:
            raise ValueError('Truncated native Newton payload')
    cursor = 0
    def take(count, dtype):
        nonlocal cursor
        value = np.frombuffer(payload, dtype=dtype, count=count, offset=cursor)
        cursor += count*dtype.itemsize
        return value
    diagonal = _finite(take(n, real)); lower = _finite(take(entries, real))
    upper = _finite(take(entries, real)) if asym else lower
    jq = take(n+1, integer); rows = take(entries, integer)-1
    mapping = take(nodes*stride, integer).reshape(nodes, stride)
    assembly = _finite(take(nodes*stride, real)).reshape(nodes, stride)[:, 1:4]
    rhs = _finite(take(n, real)); correction = _finite(take(n, real))
    accepted = _finite(take(nodes*3, real)).reshape(nodes, 3)
    if jq[0] != 1 or jq[-1] != entries+1 or np.any(jq[1:] < jq[:-1]) or np.any(jq < 1) or np.any(jq > entries+1):
        raise ValueError('Invalid native Newton sparse column pointers')
    columns = np.repeat(np.arange(n), np.diff(jq))
    if np.any(rows <= columns) or np.any(rows >= n):
        raise ValueError('Invalid native Newton lower-triangle row indices')
    # Each column must have distinct ordered indices; duplicates could hide a
    # malformed operator when COO construction sums entries.
    if entries > 1 and np.any((columns[1:] == columns[:-1]) & (rows[1:] <= rows[:-1])):
        raise ValueError('Unordered or duplicate native Newton sparse entries')
    mechanical = mapping[:, 1:4]
    positive = mechanical[mechanical > 0]
    # Pinned mastruct.c: negative even labels are SPCs, negative odd labels
    # are eliminated MPCs. MPC expansion is deliberately unsupported here.
    if np.any((mapping < 0) & (mapping % 2 != 0)) or np.any(mapping[:, 0]) or np.any(mapping[:, 4:]) or len(positive) != n or not np.array_equal(np.sort(positive), np.arange(1, n+1)):
        raise ValueError('Unsupported or incomplete native Newton equation mapping')
    _integer(header.get('penalty_scale'), 'penalty scale', 1, 1000000000)
    times = _finite(np.array([header.get('assembly_time'), frame.get('accepted_time')], dtype=float))
    if abs(times[0]-times[1]) > 1e-10*max(abs(times[1]), 1.):
        raise ValueError('Native Newton assembly time differs from accepted increment')
    matrix = sparse.coo_matrix((np.r_[diagonal, lower, upper],
        (np.r_[np.arange(n), rows, columns], np.r_[np.arange(n), columns, rows])), shape=(n,n)).tocsr()
    matrix.sort_indices()
    return {'header': header, 'matrix': matrix, 'equation_by_node': mechanical,
            'assembly_displacements_mm': assembly, 'accepted_displacements_mm': accepted,
            'rhs_n': rhs, 'native_correction_mm': correction, 'file_sha256': file_hash(path)}


def audit_native_newton(directory, system, case, result, increments):
    """Bind every exported equation system to accepted native increment records."""
    directory = Path(directory); path = directory/'analysis.native-tangent.jsonl'
    build = getattr(system, '_contact_build', None)
    summary = {'performed': False, 'all_accepted_frames_verified': False,
        'full_contact_stability_qualified': False, 'accepted_state_tangent_qualified': False,
        'scope': 'Actual pre-solve native Newton system; preceding assembly and accepted displacements are distinct. Constitutive internal state is not exported; no stability or physical qualification.'}
    history = []
    if not build or not path.exists():
        summary['reason'] = 'Pinned native Newton export is unavailable for this solver path'
    else:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError('Unsafe or oversized native Newton manifest')
        fixed = set()
        for support in case.supports:
            for index in system.region_nodes(support.region_id):
                for axis, active in enumerate((True, True, True) if support.kind == 'fixed' else support.components):
                    if active: fixed.add((system.ids[index], axis))
        expected_free = {(node, axis) for node in system.ids for axis in range(3)}-fixed
        expected_frames = [(r['step'], r['increment'], r['total_time']) for r in increments]
        seen = []; serial = 0; total = 0; header_seen = False; last_accepted = None
        with path.open() as stream:
            for line in stream:
                if len(line) > 4096 or not line.endswith('\n'):
                    raise ValueError('Invalid or truncated native Newton manifest record')
                frame = json.loads(line)
                if not header_seen:
                    pinned = contract()
                    if frame.get('type') != 'header' or frame.get('schema') != 1 or frame.get('source_sha256') != pinned['source_sha256'] or frame.get('upstream') != pinned['upstream_version'] or frame.get('instrumentation') != 'native-newton-linearization-v1':
                        raise ValueError('Native Newton export source/schema mismatch')
                    header_seen = True; continue
                if frame.get('type') != 'frame' or type(frame.get('available')) is not bool:
                    raise ValueError('Invalid native Newton manifest frame')
                identity = tuple(_integer(frame.get(k), k, 1, 10000000) for k in ('step', 'increment'))
                time = float(frame.get('accepted_time')); _finite(np.array([time]))
                if len(seen) >= len(expected_frames) or identity != expected_frames[len(seen)][:2] or abs(time-expected_frames[len(seen)][2]) > 1e-6*max(abs(time), 1.):
                    raise ValueError('Native Newton accepted increment coverage differs from STA output')
                seen.append((*identity, time))
                row = {'step': identity[0], 'increment': identity[1], 'accepted_time': time, 'available': frame['available']}
                if not frame['available']:
                    if not isinstance(frame.get('reason'), str) or not frame['reason']:
                        raise ValueError('Missing unavailable native Newton reason')
                    row['reason'] = frame['reason']; history.append(row); continue
                stage = _integer(frame.get('assembly_serial'), 'assembly serial', 1, 1000000000)
                if stage <= serial: raise ValueError('Native Newton assembly serial is repeated or reversed')
                serial = stage
                _integer(frame.get('iteration'), 'Newton iteration', 1, 10000000)
                total += _integer(frame.get('bytes'), 'file size', 1, MAX_BYTES)
                if total > MAX_BYTES: raise ValueError('Native Newton evidence exceeds output budget')
                data = read_linearization(directory, frame); mapping = data['equation_by_node']
                free = {(int(n)+1, int(a)) for n,a in zip(*np.nonzero(mapping > 0))}
                if free != expected_free:
                    raise ValueError('Native Newton equation map differs from declared mesh supports')
                if len(mapping) < max(system.ids): raise ValueError('Native Newton node labels are incomplete')
                matrix = data['matrix']; rhs = data['rhs_n']; delta = data['native_correction_mm']
                residual = _finite(matrix@delta-rhs)
                norm = float(np.max(np.asarray(abs(matrix).sum(axis=1))))
                denominator = norm*float(np.max(abs(delta)))+float(np.max(abs(rhs)))
                error = float(np.max(abs(residual)))/max(denominator, np.finfo(float).tiny)
                if not np.isfinite(error) or error > 1e-10:
                    raise ValueError('Exported native Newton matrix does not reproduce its solved correction')
                digest = hashlib.sha256()
                for values in (matrix.indptr.astype('<i8'), matrix.indices.astype('<i8'), matrix.data.astype('<f8')):
                    digest.update(values.tobytes())
                row.update({'iteration': frame['iteration'], 'assembly_serial': stage,
                    'equations': matrix.shape[0], 'nonzeros': matrix.nnz, 'asymmetric': bool(data['header']['asymmetric']),
                    'penalty_scale': data['header']['penalty_scale'], 'solve_backward_error': error,
                    'assembly_to_accepted_max_displacement_mm': float(np.max(abs(data['assembly_displacements_mm']-data['accepted_displacements_mm']))),
                    'operator_sha256': digest.hexdigest(), 'file': frame['file'], 'file_sha256': data['file_sha256']})
                last_accepted = data['accepted_displacements_mm']; history.append(row)
        if not header_seen or len(seen) != len(expected_frames):
            raise ValueError('Truncated native Newton accepted frame coverage')
        available = [r for r in history if r['available']]
        if last_accepted is not None and history[-1]['available']:
            for n in system.ids:
                if not np.allclose(last_accepted[n-1], result['field']['displacements_mm'][n], rtol=1e-5, atol=1e-12):
                    raise ValueError('Native Newton accepted displacement differs from printed final output')
        summary.update({'performed': bool(available), 'accepted_frames': len(history), 'verified_frames': len(available),
            'all_accepted_frames_verified': bool(history) and len(available) == len(history),
            'maximum_solve_backward_error': max((r['solve_backward_error'] for r in available), default=None),
            'maximum_assembly_to_accepted_displacement_mm': max((r['assembly_to_accepted_max_displacement_mm'] for r in available), default=None),
            'binary_sha256': build['binary_sha256'], 'receipt_sha256': build['receipt_sha256'],
            'manifest_sha256': file_hash(path)})
        if len(available) != len(history):
            summary['unavailable_reasons'] = sorted({r['reason'] for r in history if not r['available']})
    write_json(directory/'native-newton-audit.json', {'schema': 1, 'summary': summary, 'history': history})
    result['checks']['native_newton_audit'] = summary
    if getattr(system, '_contact_definition', None) is not None:
        evidence = json.loads((directory/'contact.json').read_text())
        evidence['native_newton_audit'] = summary
        write_json(directory/'contact.json', evidence)
    return summary
