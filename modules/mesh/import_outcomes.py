"""Multi-import accounting. Partial scene changes remain undoable."""
import os

def import_files(files, directory, importer):
    outcomes=[]
    for file in files:
        path=os.path.join(directory,file.name)
        try:
            if not os.path.isfile(path): raise FileNotFoundError('File does not exist: '+path)
            status=importer(path)
            ok=status is True or isinstance(status,set) and 'FINISHED' in status
            outcomes.append(dict(file=file.name,success=ok,reason='' if ok else 'The importer did not complete. Review its specific error.'))
        except Exception as error:
            outcomes.append(dict(file=file.name,success=False,reason=str(error)))
    return outcomes

def report_import(operator, format_name, outcomes):
    total=len(outcomes); succeeded=sum(x['success'] for x in outcomes)
    failures=[x for x in outcomes if not x['success']]
    if not total:
        operator.report({'ERROR'},'No files were selected for import.')
        return {'CANCELLED'}
    if failures:
        first=failures[0]
        operator.report({'WARNING'} if succeeded else {'ERROR'}, f"Completed: {succeeded}/{total} {format_name} files; {len(failures)} failed. First failure: {first['file']}: {first['reason']}. Review failed files and retry them.")
    else: operator.report({'INFO'},f'Imported {total} {format_name} files.')
    return {'FINISHED'} if succeeded else {'CANCELLED'}
