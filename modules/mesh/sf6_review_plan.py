"""Prepare a complete, validated review without authorizing an export.

The only output of preparation is detached data. All generated mesh files live
under the unified writer's temporary directory and are removed before return.
Selecting duplicate owners is never equivalent to accepting corrective loss.
"""
from copy import deepcopy
import os
from . import sf6_unified_export as unified
from .sf6_auto_hybrid import DuplicateOwnerChoiceRequired


def canonical_losses(events):
    """Replace narrower rows only when the displayed broader loss covers them."""
    rank={'section':0,'object':1,'output':2}
    ordered=sorted(events,key=lambda e:(-rank[e['scope']],e['object'],e['loss_token']))
    result=[]
    for event in ordered:
        covered=None
        for row in result:
            if (row['object_pointer']==event['object_pointer'] and
                    rank[row['scope']]>=rank[event['scope']] and
                    set(row['face_ids'])>=set(event['face_ids']) and
                    set(row['shape_names'])>=set(event['shape_names']) and
                    row['source_status']==event['source_status']):
                covered=row;break
        if covered is None:
            covered=deepcopy(event)
            covered['covered_loss_tokens']=[]
            covered['covered_reasons']=[]
            result.append(covered)
        if event['loss_token'] not in covered['covered_loss_tokens']:
            covered['covered_loss_tokens'].append(event['loss_token'])
        if event['reason'] not in covered['covered_reasons']:
            covered['covered_reasons'].append(event['reason'])
    return result


def prepare_review(path,options,exporter,*,staging_directory=None):
    """Return needs_owner_choice or a complete ready-to-review validated plan.

    Callers must pass the logical final destination, never a packaging staging
    path. Existing approval values are ignored; no approval values are written
    back to options. A later execute must explicitly supply the displayed plan's
    plan_token and approved_loss_tokens after the user accepts those losses.
    """
    working=deepcopy(dict(options))
    for key in ('_sf6UnifiedExpectedPlan','_sf6UnifiedExpectedOutputSHA256',
                '_sf6ApprovedCorrectiveLosses','_sf6HybridReport','_sf6UnifiedReport'):
        working.pop(key,None)
    try:
        report=unified._preview_unified(path,working,exporter,staging_directory=staging_directory)
    except DuplicateOwnerChoiceRequired as error:
        report=working['_sf6UnifiedReport']
        return dict(schema=1,status='needs_owner_choice',complete=False,
            destination=os.path.abspath(path),plan_token=report['plan_token'],
            losses=[],approved_loss_tokens=[],requires_loss_approval=False,
            retained_sections=[],output_sections=[],duplicate_groups=deepcopy(error.groups),
            report=deepcopy(report))
    events=report.get('loss_events',[])
    losses=canonical_losses(events)
    sections=deepcopy(report['output_sections'])
    return dict(schema=1,status='ready',complete=True,destination=os.path.abspath(path),
        plan_token=report['plan_token'],output_sha256=report['output_sha256'],
        losses=losses,approved_loss_tokens=sorted({event['loss_token'] for event in events}),
        requires_loss_approval=bool(events),retained_sections=[p for p in sections if p['shape_names']],
        output_sections=sections,duplicate_groups=deepcopy(report.get('duplicate_groups',[])),
        retained_section_count=sum(bool(p['shape_names']) for p in sections),
        ordinary_section_count=sum(p['status']=='ordinary_geometry' for p in sections),
        shape_free_compatible_section_count=sum(p['status']=='compatible_geometry' for p in sections),
        report=deepcopy(report))
