"""Human-readable result for the explicit SF6 LOD0 hybrid export."""


def format_hybrid_report(report):
    if not isinstance(report, dict) or not isinstance(report.get('parts'), list):
        return ['SF6 hybrid export completed, but no per-part report was returned.']

    parts = report['parts']
    source_vertices = sum(int(part.get('source_vertices', 0)) for part in parts)
    rebuilt_vertices = sum(int(part.get('rebuilt_vertices', 0)) for part in parts)
    zero_delta_vertices = sum(int(part.get('zero_delta_vertices', 0)) for part in parts)
    shape_links = sum(int(part.get('shape_links', 0)) for part in parts)
    part_word = 'part' if len(parts) == 1 else 'parts'
    lines = [
        f'SF6 hybrid LOD0: {len(parts)} {part_word}, {shape_links} shape links; '
        f'{source_vertices} source-mapped vertices, {rebuilt_vertices} rebuilt vertices.',
        'This is a rebuilt LOD0 mesh and skeleton. Verified vertices retain unchanged source shape deltas. '
        'It is not full source preservation; lower LODs are not retained.',
    ]
    if report.get('selected_only'):
        lines.append('Selected Objects Only: only the selected LOD0 mesh parts are in this output.')
        if 'source_lod0_shape_links' in report:
            lines.append(
                f"Selected LOD0 shape links: {report.get('selected_source_shape_links', shape_links)} "
                f"of {report['source_lod0_shape_links']} source links."
            )
    if shape_links:
        lines.append(f'{zero_delta_vertices} rebuilt vertices in shaped parts have zero shape deltas.')
    else:
        lines.append('The selected parts have no source shape links; this output has no blend shapes.')
    if 'source_lods' in report and 'output_lods' in report:
        lines.append(
            f"LODs: {report['source_lods']} source -> {report['output_lods']} output; "
            f"shape links: {report.get('source_shape_links', '?')} source -> "
            f"{report.get('output_shape_links', shape_links)} output."
        )
    if report.get('source_auxiliary_table_omitted'):
        lines.append('The source auxiliary table is absent from the hybrid output.')
    if report.get('normal_table_rebuilt'):
        lines.append('The LOD0 normal recalculation table was rebuilt.')
    if 'bone_names_added' in report or 'bone_names_removed' in report:
        lines.append(
            f"Skeleton name changes: {len(report.get('bone_names_added', []))} added, "
            f"{len(report.get('bone_names_removed', []))} removed."
        )
    for part in parts:
        identity = f"Group {part.get('group', '?')} / {part.get('material', '?')}"
        status = ('no source identity (rebuilt)' if part.get('status') == 'no_source'
                  else str(part.get('status', 'unknown')).replace('_', ' '))
        shape_note = (' All linked shapes have zero movement on this part.'
                      if part.get('status') == 'no_source' and part.get('shape_links') else '')
        lines.append(
            f"{identity}: {status}; "
            f"{int(part.get('source_vertices', 0))} source vertices / "
            f"{int(part.get('rebuilt_vertices', 0))} rebuilt vertices; "
            f"{int(part.get('source_triangles', 0))} source triangles / "
            f"{int(part.get('rebuilt_triangles', 0))} rebuilt triangles; "
            f"{int(part.get('shape_links', 0))} shape links; "
            f"{int(part.get('zero_delta_vertices', 0))} zero-delta vertices.{shape_note}"
        )
    return lines


def report_hybrid_export(operator, report):
    """Send the summary to Blender notifications and all parts to its console."""
    lines = format_hybrid_report(report)
    print('\n'.join(lines))
    operator.report({'INFO'}, lines[0])
    if isinstance(report, dict):
        if report.get('selected_only') and not report.get('output_shape_links'):
            operator.report({'WARNING'}, 'Selected parts have no source shape links; hybrid output has no blend shapes.')
        for part in report.get('parts', []):
            if part.get('rebuilt_vertices'):
                if part.get('status') == 'no_source' and part.get('shape_links'):
                    detail = 'no source identity; all linked shapes have zero movement'
                elif part.get('shape_links'):
                    detail = f"{part.get('zero_delta_vertices', 0)} have zero shape deltas"
                else:
                    detail = 'no source shape links'
                operator.report(
                    {'WARNING'},
                    f"Group {part.get('group', '?')} / {part.get('material', '?')}: "
                    f"{part['rebuilt_vertices']} rebuilt vertices; {detail}."
                )
