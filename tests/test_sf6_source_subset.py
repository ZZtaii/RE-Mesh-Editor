"""Check selected source geometry AND the runtime tables skipped by Blender.

Run with Python and --source pointing to a user-supplied original SF6 mesh.
These checks establish data consistency, not in-game compatibility.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import struct

import numpy as np


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1]/'modules/mesh'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sf6 = load('sf6_source')
subset = load('sf6_source_subset')


def read(data, fmt, offset):
    return struct.unpack_from('<'+fmt, data, offset)


def normals(source):
    count, table = read(source.data, 'QQ', read(source.data,'Q',56)[0])
    return [read(source.data,'QQ',p) for p in read(source.data,'Q'*count,table)]


def validate(original, result, included):
    result = sf6.SourceMesh(result)
    expected = [p for p in original.parts if p['start'] in included]
    assert len(result.parts) == len(expected)
    original_normals, result_normals = normals(original), normals(result)
    old_lods = sorted({p['lod'] for p in expected})
    cursor = 0
    for actual, old in zip(result.parts,expected):
        assert actual['start'] == cursor, 'GPU vertex allocation contains an omitted range'
        cursor += old['count']
        assert actual['count'] == old['count'] and actual['faces'] == old['faces']
        assert actual['group'] == old['group'] and actual['material'] == old['material']
        assert np.array_equal(original.faces(old),result.faces(actual))
        for kind,(stride,offset) in original.elements.items():
            a = original.data[offset+old['start']*stride:offset+(old['start']+old['count'])*stride]
            r_offset = result.elements[kind][1]
            b = result.data[r_offset+actual['start']*stride:r_offset+(actual['start']+actual['count'])*stride]
            assert a == b, 'Retained vertex attribute changed'
        old_shapes = list(original.part_shapes(old))
        new_shapes = list(result.part_shapes(actual))
        assert len(old_shapes) == len(new_shapes)
        for (an,av,_),(bn,bv,_) in zip(old_shapes,new_shapes):
            assert an == bn and np.array_equal(av,bv), 'Shape name/delta changed'
        old_parts = [p for p in original.parts if p['lod'] == old['lod']]
        new_parts = [p for p in result.parts if p['lod'] == actual['lod']]
        old_v = old['start']-old_parts[0]['start']
        new_v = actual['start']-new_parts[0]['start']
        ovp,ofp = original_normals[old['lod']]
        nvp,nfp = result_normals[actual['lod']]
        ov = np.frombuffer(original.data,'<u4',old['count'],ovp+old_v*4)
        nv = np.frombuffer(result.data,'<u4',actual['count'],nvp+new_v*4)
        assert np.array_equal(ov>>31,nv>>31), 'Normal vertex flags changed'
        assert np.array_equal((ov&0x7fffffff)-old_v,(nv&0x7fffffff)-new_v)
        of = np.frombuffer(original.data,'<u4',old['faces']*3,ofp+(old['face_start']-old_parts[0]['face_start'])*4)
        nf = np.frombuffer(result.data,'<u4',actual['faces']*3,nfp+(actual['face_start']-new_parts[0]['face_start'])*4)
        assert np.array_equal(of>>22,nf>>22), 'Packed normal face flags changed'
        active = of != 0 if old_v else np.ones(len(of),dtype=bool)
        assert np.array_equal((of[active]&0x3fffff)-old_v,(nf[active]&0x3fffff)-new_v)
        assert not np.any(nf[~active]), 'Disabled normal entries changed'
    assert cursor == (result.elements[1][1]-result.elements[0][1])//12
    for li,shapes in enumerate(result.shapes):
        ps = [p for p in result.parts if p['lod'] == li]
        for name,ranges in shapes:
            for start,offset,n in ranges:
                # Retail ranges can span adjacent parts. The allocation check
                # above already proves this LOD contains only retained vertices.
                assert ps[0]['start'] <= start < start+n <= ps[-1]['start']+ps[-1]['count'], name
                assert result.delta_offset <= offset < result.face_offset
    # Material and skeleton identifiers must remain stable for the game's MDF
    # and animation resources. Bone/name remapping is not the ordinary writer's.
    assert result.materials == original.materials
    sk = original.skeleton_offset
    end = read(original.data,'Q',56)[0]
    assert result.data[sk:end] == original.data[sk:end]
    if result.blend_offset:
        count,table = read(result.data,'QQ',result.blend_offset)
        headers = read(result.data,'Q'*count,table)
        root_tc = read(result.data,'H',headers[0])[0]
        root_sc = len(result.shapes[0])
        orig_count,orig_table = read(original.data,'QQ',original.blend_offset)
        orig_headers = read(original.data,'Q'*orig_count,orig_table)
        original_root_names = [name for name,_ in original.shapes[0]]
        for li,lp in enumerate(headers):
            tc,extra = read(result.data,'HH',lp)
            tp,_,s,ss = read(result.data,'QQQQ',lp+16)
            assert all(v == 0xffffffff or v < tc for v in read(result.data,'I'*root_tc,s))
            shape_map = read(result.data,'I'*root_sc,ss)
            assert all(v == 0xffffffff or v < len(result.shapes[li]) for v in shape_map)
            original_li = old_lods[li]
            orig_ss = read(original.data,'Q',orig_headers[original_li]+40)[0]
            current_names = [name for name,_ in result.shapes[li]]
            for root_index,index in enumerate(shape_map):
                old_root_index = original_root_names.index(result.shapes[0][root_index][0])
                old_index = read(original.data,'I',orig_ss+old_root_index*4)[0]
                expected_index = 0xffffffff
                if old_index != 0xffffffff:
                    expected_name = original.shapes[original_li][old_index][0]
                    if expected_name in current_names:
                        expected_index = current_names.index(expected_name)
                assert index == expected_index, 'Cross-LOD shape association changed'
            ps = [p for p in result.parts if p['lod'] == li]
            base=ps[0]['start']
            covered=np.zeros(sum(p['count'] for p in ps),dtype=bool)
            for ti in range(tc+extra):
                _,num,_,count,_,rp = read(result.data,'HHHBBQ',tp+ti*16)
                assert bool(num) == (ti < tc)
                for ri in range(count):
                    start,_,length,_ = read(result.data,'IIII',rp+ri*16)
                    assert ps[0]['start'] <= start < start+length <= ps[-1]['start']+ps[-1]['count']
                    covered[start-base:start-base+length]=True
            assert covered.all(), 'Vertices missing from both shaped and unshaped descriptors'
    return dict(parts=len(result.parts),lods=result.lod_count,vertices=cursor,
                shapes=sum(map(len,result.shapes)),bytes=len(result.data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--report-json',type=Path)
    args = parser.parse_args()
    src = sf6.SourceMesh(args.source.read_bytes())
    top = [p for p in src.parts if p['lod'] == 0]
    cases = {
        'omit_interior_submesh':{p['start'] for i,p in enumerate(top) if i != 3},
        'omit_first_submesh':{p['start'] for p in top[1:]},
        'single_first_part':{top[0]['start']},
        'single_last_part':{top[-1]['start']},
        'multiple_lods':{p['start'] for p in src.parts if p['group']==top[0]['group']},
    }
    nonshape = next((p for p in top if not list(src.part_shapes(p))),None)
    if nonshape:
        cases['single_nonshape_part'] = {nonshape['start']}
    if args.source.name == 'esf030_001_01.mesh.230110883':
        cases['omit_one_coat'] = {p['start'] for p in top if not(p['group']==95 and p['sub']==0)}
        cases['viper_exact_user_scope'] = {p['start'] for p in top if not(p['group']==95 and p['sub'] in (0,1))}
    report = []
    for name,included in cases.items():
        out = subset.compact_source_subset(src.data,src,included)
        result = validate(src,out,included)
        result.update(test=name,passed=True)
        report.append(result)
        print(json.dumps(result),flush=True)
    assert subset.compact_source_subset(src.data,src,{p['start'] for p in src.parts}) == src.data
    report.append(dict(test='all_source_parts_is_exact_original',passed=True))
    top_only = subset.compact_source_subset(src.data,src,{p['start'] for p in src.parts},all_lods=False)
    result = validate(src,top_only,{p['start'] for p in top})
    report.append(dict(test='all_lods_off_limits_to_selected_lod0',passed=True,**result))
    if args.report_json:
        args.report_json.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
