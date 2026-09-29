"""Human-readable result for the explicit SF6 LOD0 hybrid export."""


def format_hybrid_report(report):
    if not isinstance(report, dict) or not isinstance(report.get('parts'), list):
        return ['SF6 hybrid export completed, but no per-part report was returned.']

    parts = report['parts']
    source_vertices = sum(int(part.get('source_vertices', 0)) for part in parts)
    rebuilt_vertices = sum(int(part.get('rebuilt_vertices', 0)) for part in parts)
    transferred_vertices = sum(int(part.get('transferred_vertices', 0)) for part in parts)
    zero_delta_vertices = sum(int(part.get('zero_delta_vertices', 0)) for part in parts)
    shape_links = sum(int(part.get('shape_links', 0)) for part in parts)
    edited_shape_entries = int(report.get('edited_source_shape_delta_entries', 0))
    part_word = 'part' if len(parts) == 1 else 'parts'
    lines = [
        f'SF6 hybrid LOD0: {len(parts)} {part_word}, {shape_links} shape links; '
        f'{source_vertices} source-mapped vertices, {rebuilt_vertices} rebuilt vertices.',
        'This is a rebuilt LOD0 mesh and skeleton. Source-mapped vertices keep untouched source shape '
        'deltas and export meaningful Blender shape-key edits. '
        'It is not full source preservation; lower LODs are not retained.',
    ]
    if edited_shape_entries:
        entry_word = 'entry' if edited_shape_entries == 1 else 'entries'
        lines.append(
            f'{edited_shape_entries} source-mapped shape delta {entry_word} '
            f"{'was' if edited_shape_entries == 1 else 'were'} exported from edited Blender keys."
        )
    if report.get('selected_only'):
        lines.append('Selected Objects Only: only the selected LOD0 mesh parts are in this output.')
        if 'source_lod0_shape_links' in report:
            lines.append(
                f"Selected LOD0 shape links: {report.get('selected_source_shape_links', shape_links)} "
                f"of {report['source_lod0_shape_links']} source links."
            )
    evaluation = report.get('evaluated_geometry', {})
    if evaluation.get('corner_split_vertices'):
        lines.append(
            f"{evaluation['corner_split_vertices']} additional vertex rows preserve differing evaluated UV corners."
        )
    if evaluation.get('sharp_split_vertices'):
        lines.append(
            f"{evaluation['sharp_split_vertices']} additional vertex rows preserve sharp edges."
        )
    if evaluation.get('normal_split_vertices'):
        lines.append(
            f"{evaluation['normal_split_vertices']} additional vertex rows preserve distinct evaluated corner normals."
        )
    if evaluation.get('modifier_parts'):
        lines.append(
            f"Modifiers evaluated on {evaluation['modifier_parts']} parts; "
            'corrective keys use the same evaluated vertex and triangle layout as the Basis.'
        )
        for part in evaluation.get('parts', []):
            modifiers = part.get('active_modifiers', [])
            if modifiers:
                names = ', '.join(str(modifier['name']) for modifier in modifiers)
                lines.append(
                    f"{part['source_name']}: {names}; "
                    f"{part['source_vertices']} editable -> {part['evaluated_vertices']} evaluated vertices."
                )
                for modifier in modifiers:
                    if modifier['type'] == 'DATA_TRANSFER' and modifier.get('custom_normals'):
                        mask = (f"; mask {modifier['vertex_group']}" +
                                (' (inverted)' if modifier.get('invert_vertex_group') else '')
                                if modifier.get('vertex_group') else '')
                        lines.append(
                            f"{modifier['name']}: Custom Normals from {modifier['normal_source']}; "
                            f"mapping {modifier['normal_mapping']}, {modifier['mix_mode']} "
                            f"factor {modifier['mix_factor']:g}{mask}. "
                            'The source uses its current scene state; final Basis normals are exported.'
                        )
                    if modifier['type'] == 'MULTIRES' and 'viewport_level' in modifier:
                        level_note = (
                            f"{modifier['name']}: Multires viewport level {modifier['viewport_level']} "
                            f"(sculpt {modifier['sculpt_level']}, render {modifier['render_level']}); "
                            'stored sculpt detail is evaluated at the viewport resolution.'
                        )
                        if 'simplify_subdivision_cap' in modifier:
                            level_note += (
                                ' Scene Simplify subdivision cap: '
                                f"{modifier['simplify_subdivision_cap']}."
                            )
                        lines.append(level_note)
    placeholders = report.get('placeholder_parts_without_shapes', [])
    if placeholders:
        lines.append(
            f'{len(placeholders)} C_Hip plane placeholder parts keep their ordinary geometry '
            'and have no corrective shape links.'
        )
    if shape_links:
        if transferred_vertices:
            lines.append(
                f'{transferred_vertices} rebuilt vertices have Blender shape deltas; '
                f'{zero_delta_vertices} rebuilt vertices in shaped parts still have zero deltas. '
                'These deltas come from evaluated Blender keys rather than copied retail vertex rows.'
            )
        else:
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
        part_edited_entries = int(part.get('edited_source_shape_delta_entries', 0))
        part_entry_word = 'entry' if part_edited_entries == 1 else 'entries'
        status = ('no source identity (rebuilt)' if part.get('status') == 'no_source'
                  else 'C_Hip plane placeholder' if part.get('status') == 'placeholder'
                  else str(part.get('status', 'unknown')).replace('_', ' '))
        shape_note = (' All linked shapes have zero movement on this part.'
                      if part.get('status') == 'no_source' and part.get('shape_links')
                      and not part.get('transferred_vertices') else '')
        lines.append(
            f"{identity}: {status}; "
            f"{int(part.get('source_vertices', 0))} source vertices / "
            f"{int(part.get('rebuilt_vertices', 0))} rebuilt vertices; "
            f"{int(part.get('source_triangles', 0))} source triangles / "
            f"{int(part.get('rebuilt_triangles', 0))} rebuilt triangles; "
            f"{int(part.get('shape_links', 0))} shape links; "
            f"{int(part.get('zero_delta_vertices', 0))} zero-delta vertices; "
            f"{int(part.get('transferred_vertices', 0))} vertices with Blender shape deltas; "
            f"{part_edited_entries} edited source shape {part_entry_word}.{shape_note}"
        )
    return lines


def report_hybrid_export(operator, report):
    """Send the summary to Blender notifications and all parts to its console."""
    lines = format_hybrid_report(report)
    print('\n'.join(lines))
    operator.report({'INFO'}, lines[0])
    if isinstance(report, dict):
        edited_shape_entries = int(report.get('edited_source_shape_delta_entries', 0))
        if edited_shape_entries:
            entry_word = 'entry' if edited_shape_entries == 1 else 'entries'
            operator.report(
                {'INFO'},
                f'{edited_shape_entries} edited source-mapped shape delta {entry_word} exported from Blender.'
            )
        if report.get('selected_only') and not report.get('output_shape_links'):
            operator.report({'WARNING'}, 'Selected parts have no source shape links; hybrid output has no blend shapes.')
        for part in report.get('parts', []):
            if part.get('rebuilt_vertices'):
                if part.get('status') == 'placeholder':
                    detail = 'C_Hip plane; corrective shape links omitted'
                elif part.get('status') == 'no_source' and part.get('shape_links'):
                    detail = ('no source identity; '
                              f"{part.get('transferred_vertices', 0)} transferred shape vertices, "
                              f"{part.get('zero_delta_vertices', 0)} zero-delta vertices")
                elif part.get('shape_links'):
                    detail = (f"{part.get('transferred_vertices', 0)} have transferred deltas, "
                              f"{part.get('zero_delta_vertices', 0)} have zero shape deltas")
                else:
                    detail = 'no source shape links'
                operator.report(
                    {'WARNING'},
                    f"Group {part.get('group', '?')} / {part.get('material', '?')}: "
                    f"{part['rebuilt_vertices']} rebuilt vertices; {detail}."
                )
