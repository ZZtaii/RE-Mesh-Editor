# SF6 source preservation

This experimental path preserves source deformation data when editing SF6
`.mesh.230110883` files. It imports the original corrective shapes as Blender
shape keys and embeds a compressed copy of the original mesh in the blend file.
Export patches that copy instead of rebuilding the mesh from Blender alone.

The ordinary upstream importer disables blend-shape parsing, and the rebuilt
export omits blend shapes and other source sections. Retaining the source avoids
having to synthesize partially understood runtime deformation tables. Related
upstream tracking issue: https://github.com/NSACloud/RE-Mesh-Editor/issues/11.

## Usage

1. Import an original SF6 mesh with **SF6: Preserve Source + Shape Keys** enabled.
   Disable **Merge Mesh Groups** and **Merge Armature**.
2. Edit existing vertices or shape keys. Save the blend file to retain the
   embedded source; its original import path is no longer required.
3. Export the imported collection with **SF6: Preserve Source Data** enabled and
   the same axis-conversion setting used on import.

Existing projects without source metadata require a fresh import. Disabling
source preservation uses the ordinary exporter, which can discard deformation
data again.

## Supported edits and preserved data

- Existing positions and shape deltas can be edited within their original
  authored ranges. Vertex and face identity attributes map edits back to the
  original slots.
- Removing faces or imported mesh objects keeps their allocated buffer slots
  and makes the removed triangles degenerate.
- The exact **Replace Selected with C_Hip Stub** result from Game Modding Stub
  Tools 1.7 is recognized. This external add-on is not bundled. Its four-vertex
  quad occupies the original part's allocation, and unused slots remain inert.
  Parts with corrective shapes cannot use this exception.
- Source material order, skeleton, runtime tables and all source LODs are
  preserved. Unedited data remains byte-identical.
- Validation finishes before the destination is replaced. Export writes a
  temporary file in the destination directory and replaces the target atomically.

## Limits

This is a constrained preservation workflow, not a general blend-shape writer.

- The parser accepts internal versions `220705151` and `230403828`. Validation
  covered five supplied SF6 meshes; other games and arbitrary meshes using those
  version numbers have not been validated. Streamed and quad-source layouts are
  unsupported.
- Added or duplicated vertices, rewired faces, new or renamed or removed shape
  keys, changed skeletons, object transforms and active non-armature modifiers
  are rejected. Manual weight, UV and material-slot reassignment is unsupported.
  The exact C_Hip stub is a supported exception.
- Original normal, tangent, color, bounding and runtime tables are retained;
  edits to those channels are not exported. Large geometry edits needing updated
  normals or bounds remain unvalidated.
- Only imported parts receive edits. Other LODs retain their original appearance;
  a part removed at LOD0 may reappear at a distance. Export All LODs and automatic
  UV or sharp-edge splitting do not rebuild source-mode output.
- Selected Only export is unsupported. Shape keys must remain relative to Basis
  without vertex-group masks.

## Validation

Tested with Blender 4.5.3 LTS against upstream commit
`622daa75b41ec622c3444ecb44ef31a651692687`.

- Untouched import/export was byte-identical for esf005 C1, esf028 C1, esf030 C1,
  esf033 C1 and esf033 C2.
- A small esf033 C2 jersey edit changed only the intended position and associated
  shape-delta slots: 41 changed bytes and zero unrelated changed bytes.
- Renaming a shape key was rejected without overwriting an existing destination.
- Saving and reopening the blend reproduced the exported C2 candidate exactly.
- The actual external C_Hip operator produced the accessory-removal candidates.
  C2 changed 30,843 bytes and C1 changed 70,435 bytes, exclusively within the
  selected part's position, UV, weight and face ranges. Clothing and unrelated
  deformation data remained byte-identical.
- The user confirmed the C2 candidate loaded correctly with no earrings, then
  confirmed the C1 candidate worked in-game. The individual omitted table
  responsible for the earlier clothing collision was not isolated.

The automated runner requires legally obtained local game meshes. It does not
download or include game assets. From a checkout, run Blender in a separate
background process:

```sh
blender --background --factory-startup --python-exit-code 1 \
  --python tests/test_sf6_source_preservation.py -- \
  --source-dir /path/to/original/meshes \
  --report-json /path/to/results.json
```

Supply `--stub-addon-dir /path/to/addon/parent` to additionally exercise the
installed `game_modding_stub_tools` module. Tests write generated meshes and the
temporary blend to a temporary directory. The JSON report contains filenames,
hashes and results, without embedding game assets.
