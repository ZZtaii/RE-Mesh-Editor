# SF6 export in version 0.71

The normal SF6 file export, Quick Batch Export and Fluffy mod-folder export use one source-preservation workflow.

## Choose what to export

Unified export can be started from mesh Edit, Sculpt, Weight Paint, Vertex Paint, Texture Paint, armature Pose and armature Edit modes, as well as Object Mode. It uses an isolated snapshot of the current authored state so review and export do not switch the original objects out of their mode. Finish any active brush stroke or modal tool before starting export. Review is tied to the captured edits; changing them requires a fresh review.

Keep **Preserve Source Data** enabled to retain compatible original data. The exporter decides whether it can reuse the original mesh or needs to rebuild it. An unchanged mesh with compatible settings can keep all original LODs. A rebuild may need to export only LOD0; the result explains this.

Choose **LOD0 Only** if you explicitly want only the highest-detail mesh. **Keep Compatible Source LODs** does not regenerate lower LODs or promise to retain incompatible ones.

Disable preservation for an intentional Ordinary export. Live corrective shape links will not be preserved. Existing saved settings and explicit script arguments keep their values. Only genuinely new profiles receive source-preserving processing defaults.

## Review fallback and loss

When a preserving export needs Ordinary fallback, the review identifies the affected objects/material sections and known shape keys that will not remain live. Compatible sections can keep their corrective shapes. Some changes require fallback for a whole object or the entire output, rather than one material.

Choose **Cancel export** to return and repair the scene, or the explicit **Export without…** button to approve only the listed losses. Escape, Enter's default action and dismissing the review cancel; they do not approve losses. Changing the scene, settings or duplicate ownership requires a fresh review.

If copies claim the same source correctives, select an eligible copy under **Keep source correctives on**, or choose **None**. This choice alone does not approve losing shapes on other copies.

Edited rest rigs may conflict with **Preserve Bone Matrices**. The review asks which rig data to use rather than silently ignoring the edit or overriding the setting.

## Understand the output

Open **Details** for the destination, committed/failed status, LOD disposition, retained and Ordinary sections, shape losses and warnings. Details remain available as the `SF6 Export Details` Blender Text datablock. Plan/approval tokens are not saved there.

Ordinary fallback exports evaluated geometry; modifiers, current shape-key values and pose can therefore be baked into affected geometry. Compatible corrective sections follow their preserved rest/base representation. This does not guarantee identical in-game animation or appearance. Test the mod in game before distributing it.

New materials may require matching MDF entries. Exporting a mesh does not automatically repair its material file.

An unsafe output still stops. Missing positive bone weights, invalid coordinates, broken geometry or an Ordinary exporter failure are not bypassed by fallback. Unknown or corrupt saved profiles are not silently replaced.

## Files and performance

Mesh output is staged and validated before replacing the destination. Batch files commit independently, so a partial batch can have successful files alongside failures. Mod-folder export uses staged replacement and rollback, not a power-loss-proof transaction across the whole folder.

Review performs real evaluation and validation, then checks the plan and final output again at publication. Edited or modifier-heavy meshes can take longer than a single Ordinary export. The extra work prevents approving one result and silently writing a different one.

The legacy strict source functions and explicit script controls remain available for compatibility. They are not a second visible preservation mode in the standard workflow.

## Supported verification

The accompanying QA evidence identifies tested meshes, cases, source hashes and limitations. Tests use Blender 4.3.2 on Linux, with separate headless and actual managed-desktop UI checks. Passing those tests is not certification for every Blender version, Windows behavior, arbitrary simulation/driver setup or in-game result.

## Live dependency exceptions

Mesh and armature Edit modes are supported. Some other objects can drive the exported mesh through modifiers or drivers. A required shape-keyed Curve or Surface still in Edit Mode cannot be copied safely because Blender does not expose its pending point-to-key mapping, and synchronizing it can damage relative keys. Finish that dependency's Edit session before exporting. Non-keyed Surface and Text dependencies use object-scoped synchronization to capture current topology or text. An unrelated active Curve does not trigger a dependency restriction; the separate global sculpt safeguard below still applies.

Visible saved Ordinary profiles use the unified mode-safe path. Scripts that explicitly opt out of unified export retain legacy behavior and can flush pending Edit data even if they restore Edit Mode afterward.

Mode support does not change export policy: retained correctives use compatible Basis/rest data, while an approved Ordinary fallback exports current evaluated geometry, including pinned shape-key display and enabled modifiers. Mesh export does not embed painted bitmap images.

## Live sculpt synchronization

Dyntopo and Multires keep some strokes in an editing buffer. Before taking the private export snapshot, the add-on synchronizes those buffers using Blender's editor operation. It does not save the project or switch the source out of Sculpt mode. Backing mesh data is updated to the shape already being edited; this is distinct from adding or discarding an authored edit.

That Blender operation affects editing sessions globally. If any shape-keyed Mesh, Curve or Surface, or any Armature, is also in Edit Mode, export stops before synchronization and lists those objects. Finish those Edit sessions before retrying, even if they are in another scene. This protects relative shape keys and rest-bone data from Blender's synchronization side effects. Normal mesh and armature Edit exports use private copies and do not need this global operation.

Multires uses the configured export/viewport subdivision level, which can differ from the sculpt display level. Finish the current brush stroke before invoking export.
